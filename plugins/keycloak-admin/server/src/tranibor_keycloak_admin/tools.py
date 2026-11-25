# SPDX-License-Identifier: Apache-2.0
"""The tools: what an agent may read of a Keycloak realm, and the few governed writes.

Rules this module enforces before and after every call:

- **Whitelists, never blacklists.** Every object returned is rebuilt from the fields named here, so a field
  Keycloak adds later is not passed on. A user never carries credentials or attributes; a client never
  carries its secret, its registration token nor its attributes; the realm summary never carries the SMTP
  password, the keys, nor any secret; an admin event never carries its `representation` (it can hold a
  client secret or a credential), only whether there was one.
- **Identifiers are validated** (a UUID for users, groups and clients) before they reach a URL path.
- **Never**: delete a user, read a credential, impersonate, change the realm's settings, touch a client's
  secret. No tool here does, and the service account is not asked for the roles that would allow it.
- Every list is capped and says when it was cut (`has_more` / `truncated`).
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from .client import KeycloakClient
from .errors import Failure, invalid

log = structlog.get_logger("tranibor_keycloak_admin")

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
USERNAME = re.compile(r"^[^\s/\\?#%]{1,255}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
EVENT_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Required actions an administrator may ask Keycloak to email a person about. Others (custom providers,
#: TERMS_AND_CONDITIONS...) are refused: the list is the standard ones that make sense for an access review.
REQUIRED_ACTIONS = frozenset(
    {
        "UPDATE_PASSWORD",
        "CONFIGURE_TOTP",
        "VERIFY_EMAIL",
        "UPDATE_PROFILE",
        "webauthn-register",
        "webauthn-register-passwordless",
    }
)
MAX_PAGE = 100
MAX_USERS_SCAN = 2000
MAX_GROUPS = 500
MAX_MEMBER_COUNT = 2000
MAX_EVENT_PAGES = 20
DEFAULT_LIFESPAN_S = 12 * 3600
MAX_LIFESPAN_S = 30 * 24 * 3600
#: In-memory idempotency by the platform's `action_key` (a process restart forgets it).
DEDUPE_SIZE = 256


def _uuid(value: str, what: str) -> str:
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise invalid(f"{what} must be a Keycloak id (a UUID), got {str(value)[:60]!r}")
    return value.lower()


def _bounded(value: int, low: int, high: int, what: str) -> int:
    if not low <= value <= high:
        raise invalid(f"{what} must be between {low} and {high}")
    return value


def _iso(millis: Any) -> str | None:
    if not isinstance(millis, int | float) or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick(source: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {k: source[k] for k in keys if k in source}


# ---- whitelists ----------------------------------------------------------------------------------------


def user_view(user: dict[str, Any]) -> dict[str, Any]:
    """A user without credentials, attributes, federation links nor access details."""
    view = _pick(user, ("id", "username", "email", "firstName", "lastName", "enabled", "emailVerified"))
    created = user.get("createdTimestamp")
    if created is not None:
        view["created"] = _iso(created)
    if "requiredActions" in user:
        view["required_actions"] = list(user["requiredActions"])
    if "totp" in user:
        view["totp"] = bool(user["totp"])
    return view


def group_view(group: dict[str, Any]) -> dict[str, Any]:
    return _pick(group, ("id", "name", "path"))


def client_view(client: dict[str, Any]) -> dict[str, Any]:
    """A client without its secret, registration token nor attributes."""
    view = _pick(
        client,
        (
            "id",
            "clientId",
            "name",
            "description",
            "enabled",
            "protocol",
            "publicClient",
            "bearerOnly",
            "serviceAccountsEnabled",
            "standardFlowEnabled",
            "implicitFlowEnabled",
            "directAccessGrantsEnabled",
            "rootUrl",
            "baseUrl",
        ),
    )
    for key in ("redirectUris", "webOrigins"):
        if isinstance(client.get(key), list):
            view[key] = [str(u) for u in client[key]][:50]
    return view


def session_view(session: dict[str, Any]) -> dict[str, Any]:
    clients = session.get("clients")
    return {
        "id": session.get("id"),
        "started": _iso(session.get("start")),
        "last_access": _iso(session.get("lastAccess")),
        "ip_address": session.get("ipAddress"),
        "remember_me": bool(session.get("rememberMe", False)),
        "clients": sorted(str(n) for n in clients.values()) if isinstance(clients, dict) else [],
    }


def login_event_view(event: dict[str, Any]) -> dict[str, Any]:
    details = _obj(event.get("details"))
    view: dict[str, Any] = {
        "time": _iso(event.get("time")),
        "type": event.get("type"),
        "user_id": event.get("userId"),
        "client_id": event.get("clientId"),
        "ip_address": event.get("ipAddress"),
        "error": event.get("error"),
    }
    # `details` also holds code and token ids: only the human-readable keys are passed on.
    view["details"] = {k: str(details[k])[:200] for k in ("username", "auth_method", "reason") if k in details}
    return view


def admin_event_view(event: dict[str, Any]) -> dict[str, Any]:
    auth = _obj(event.get("authDetails"))
    return {
        "time": _iso(event.get("time")),
        "operation": event.get("operationType"),
        "resource_type": event.get("resourceType"),
        "resource_path": event.get("resourcePath"),
        "error": event.get("error"),
        "by": {
            "user_id": auth.get("userId"),
            "client_id": auth.get("clientId"),
            "realm_id": auth.get("realmId"),
            "ip_address": auth.get("ipAddress"),
        },
        # The representation can hold a secret or a credential: never returned, only noted.
        "representation_present": bool(event.get("representation")),
    }


REALM_FIELDS = (
    "realm",
    "displayName",
    "enabled",
    "sslRequired",
    "registrationAllowed",
    "registrationEmailAsUsername",
    "rememberMe",
    "verifyEmail",
    "loginWithEmailAllowed",
    "duplicateEmailsAllowed",
    "resetPasswordAllowed",
    "editUsernameAllowed",
    "bruteForceProtected",
    "permanentLockout",
    "maxFailureWaitSeconds",
    "failureFactor",
    "passwordPolicy",
    "otpPolicyType",
    "otpPolicyAlgorithm",
    "otpPolicyDigits",
    "defaultSignatureAlgorithm",
    "accessTokenLifespan",
    "ssoSessionIdleTimeout",
    "ssoSessionMaxLifespan",
    "offlineSessionIdleTimeout",
    "offlineSessionMaxLifespanEnabled",
    "loginTheme",
    "accountTheme",
    "adminTheme",
    "emailTheme",
    "internationalizationEnabled",
    "eventsEnabled",
    "eventsExpiration",
    "adminEventsEnabled",
    "adminEventsDetailsEnabled",
    "browserFlow",
    "directGrantFlow",
    "registrationFlow",
)


def realm_view(realm: dict[str, Any]) -> dict[str, Any]:
    """The realm's settings that matter for access and upgrades; nothing that is a secret.

    Left out on purpose: `smtpServer` (its password; only whether a host is set is reported), `keys`,
    `components`, `attributes`, `clientPolicies`, `identityProviders` and every other field."""
    view = _pick(realm, REALM_FIELDS)
    smtp = realm.get("smtpServer")
    view["smtp_configured"] = bool(isinstance(smtp, dict) and smtp.get("host"))
    types = realm.get("enabledEventTypes")
    view["enabled_event_types"] = len(types) if isinstance(types, list) else None
    listeners = realm.get("eventsListeners")
    view["events_listeners"] = [str(x) for x in listeners] if isinstance(listeners, list) else []
    return view


# ---- the tools -----------------------------------------------------------------------------------------


class Tools:
    def __init__(self, client: KeycloakClient, clock: Callable[[], datetime] | None = None) -> None:
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._done: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def now(self) -> datetime:
        return self._clock()

    # -- reads --

    async def find_users(
        self,
        search: str | None = None,
        username: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        exact: bool = False,
        enabled: bool | None = None,
        first: int = 0,
        max: int = 50,
    ) -> dict[str, Any]:
        _bounded(max, 1, MAX_PAGE, "max")
        _bounded(first, 0, 1_000_000, "first")
        rows = await self.client.get(
            "/users",
            search=search,
            username=username,
            email=email,
            firstName=first_name,
            lastName=last_name,
            exact="true" if exact else None,
            enabled=None if enabled is None else str(enabled).lower(),
            briefRepresentation="true",
            first=first,
            max=max + 1,
        )
        users = [user_view(u) for u in rows[:max] if isinstance(u, dict)]
        return {"users": users, "count": len(users), "first": first, "max": max, "has_more": len(rows) > max}

    async def _user(self, user_id: str) -> dict[str, Any]:
        row = await self.client.get(f"/users/{_uuid(user_id, 'user_id')}")
        if not isinstance(row, dict):
            raise Failure("upstream_error", "Keycloak answered a non-object for a user")
        return row

    async def get_user(self, user_id: str | None = None, username: str | None = None) -> dict[str, Any]:
        if bool(user_id) == bool(username):
            raise invalid("give exactly one of user_id and username")
        if user_id:
            return {"user": user_view(await self._user(user_id))}
        assert username is not None
        found = await self.client.get("/users", username=username, exact="true", max=2)
        rows = [r for r in found if isinstance(r, dict) and str(r.get("username", "")).lower() == username.lower()]
        if not rows:
            raise Failure("not_found", f"no user named {username!r}")
        return {"user": user_view(await self._user(str(rows[0]["id"])))}

    async def list_user_groups(self, user_id: str) -> dict[str, Any]:
        rows = await self.client.get(f"/users/{_uuid(user_id, 'user_id')}/groups", briefRepresentation="true")
        groups = [group_view(g) for g in rows if isinstance(g, dict)]
        return {"user_id": user_id.lower(), "groups": groups, "count": len(groups)}

    async def _children(self, group: dict[str, Any], depth: int, out: list[dict[str, Any]], budget: int) -> None:
        if depth > 6 or len(out) >= budget:
            return
        if not group.get("subGroupCount") and "subGroupCount" in group:
            return
        kids, _ = await self.client.get_page(f"/groups/{group['id']}/children", limit=MAX_GROUPS, subGroupsCount="true")
        for kid in kids:
            if len(out) >= budget:
                return
            out.append(kid)
            await self._children(kid, depth + 1, out, budget)

    async def _count_members(self, group_id: str) -> tuple[int, bool]:
        members, more = await self.client.get_page(
            f"/groups/{group_id}/members", limit=MAX_MEMBER_COUNT, briefRepresentation="true"
        )
        return len(members), more

    async def list_groups(
        self, search: str | None = None, with_member_counts: bool = False, limit: int = 200
    ) -> dict[str, Any]:
        _bounded(limit, 1, MAX_GROUPS, "limit")
        top, _ = await self.client.get_page(
            "/groups", limit=MAX_GROUPS, briefRepresentation="true", subGroupsCount="true"
        )
        flat: list[dict[str, Any]] = []
        for group in top:
            flat.append(group)
            await self._children(group, 1, flat, MAX_GROUPS)
        needle = (search or "").lower()
        matched = [g for g in flat if not needle or needle in str(g.get("path", g.get("name", ""))).lower()]
        cut = len(matched) > limit
        chosen = matched[:limit]
        views = [group_view(g) for g in chosen]
        if with_member_counts:
            sem = asyncio.Semaphore(8)

            async def count(view: dict[str, Any]) -> None:
                async with sem:
                    n, capped = await self._count_members(str(view["id"]))
                view["member_count"] = n
                if capped:
                    view["member_count_is_a_floor"] = True

            await asyncio.gather(*(count(v) for v in views))
        return {"groups": views, "count": len(views), "truncated": cut or len(flat) >= MAX_GROUPS}

    async def get_group_members(self, group_id: str, first: int = 0, max: int = 100) -> dict[str, Any]:
        _bounded(max, 1, MAX_PAGE, "max")
        _bounded(first, 0, 1_000_000, "first")
        gid = _uuid(group_id, "group_id")
        group = await self.client.get(f"/groups/{gid}")
        rows = await self.client.get(f"/groups/{gid}/members", briefRepresentation="true", first=first, max=max + 1)
        users = [user_view(u) for u in rows[:max] if isinstance(u, dict)]
        return {
            "group": group_view(group) if isinstance(group, dict) else {"id": gid},
            "members": users,
            "count": len(users),
            "first": first,
            "has_more": len(rows) > max,
        }

    async def list_user_sessions(self, user_id: str) -> dict[str, Any]:
        rows = await self.client.get(f"/users/{_uuid(user_id, 'user_id')}/sessions")
        sessions = [session_view(s) for s in rows if isinstance(s, dict)]
        return {"user_id": user_id.lower(), "sessions": sessions, "count": len(sessions)}

    async def list_clients(
        self,
        client_id: str | None = None,
        search: bool = False,
        first: int = 0,
        max: int = 50,
    ) -> dict[str, Any]:
        _bounded(max, 1, MAX_PAGE, "max")
        _bounded(first, 0, 1_000_000, "first")
        rows = await self.client.get(
            "/clients", clientId=client_id, search="true" if search else None, first=first, max=max + 1
        )
        clients = [client_view(c) for c in rows[:max] if isinstance(c, dict)]
        return {"clients": clients, "count": len(clients), "first": first, "has_more": len(rows) > max}

    async def _realm(self) -> dict[str, Any]:
        realm = await self.client.get("")
        if not isinstance(realm, dict):
            raise Failure("upstream_error", "Keycloak answered a non-object for the realm")
        return realm

    async def get_realm_summary(self) -> dict[str, Any]:
        realm = await self._realm()
        summary = realm_view(realm)
        try:
            counted = await self.client.get("/users/count")
            summary["user_count"] = int(counted)
        except (Failure, ValueError, TypeError):
            summary["user_count"] = None
        try:
            clients, more = await self.client.get_page("/clients", limit=MAX_USERS_SCAN)
            summary["client_count"] = len(clients)
            summary["client_count_is_a_floor"] = more
        except Failure:
            summary["client_count"] = None
        return {"realm": summary}

    @staticmethod
    def _dates(date_from: str | None, date_to: str | None) -> None:
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if value is not None and not DATE.fullmatch(value):
                raise invalid(f"{name} must be a date, yyyy-MM-dd")

    async def list_login_events(
        self,
        types: list[str] | None = None,
        user_id: str | None = None,
        client: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        first: int = 0,
        max: int = 100,
    ) -> dict[str, Any]:
        _bounded(max, 1, 200, "max")
        _bounded(first, 0, 1_000_000, "first")
        self._dates(date_from, date_to)
        wanted = types or ["LOGIN"]
        if not all(EVENT_NAME.fullmatch(t) for t in wanted):
            raise invalid("types are event type names such as LOGIN, LOGIN_ERROR, LOGOUT")
        realm = await self._realm()
        if not realm.get("eventsEnabled"):
            return {
                "events_enabled": False,
                "events": [],
                "count": 0,
                "has_more": False,
                "note": "login events are not enabled in this realm: there is nothing to read",
            }
        rows = await self.client.get(
            "/events",
            type=wanted,
            user=_uuid(user_id, "user_id") if user_id else None,
            client=client,
            dateFrom=date_from,
            dateTo=date_to,
            first=first,
            max=max + 1,
        )
        events = [login_event_view(e) for e in rows[:max] if isinstance(e, dict)]
        return {"events_enabled": True, "events": events, "count": len(events), "has_more": len(rows) > max}

    async def list_admin_events(
        self,
        operation_types: list[str] | None = None,
        resource_types: list[str] | None = None,
        auth_user_id: str | None = None,
        resource_path: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        first: int = 0,
        max: int = 100,
    ) -> dict[str, Any]:
        _bounded(max, 1, 200, "max")
        _bounded(first, 0, 1_000_000, "first")
        self._dates(date_from, date_to)
        for name, values in (("operation_types", operation_types), ("resource_types", resource_types)):
            if values and not all(EVENT_NAME.fullmatch(v) for v in values):
                raise invalid(
                    f"{name} are names such as CREATE, UPDATE, DELETE, ACTION / USER, CLIENT, REALM_ROLE_MAPPING"
                )
        if resource_path is not None and not re.fullmatch(r"^[A-Za-z0-9_./*:-]{1,200}$", resource_path):
            raise invalid("resource_path is a path such as users/<id> or users/*")
        realm = await self._realm()
        if not realm.get("adminEventsEnabled"):
            return {
                "admin_events_enabled": False,
                "events": [],
                "count": 0,
                "has_more": False,
                "note": "admin events are not enabled in this realm: there is nothing to read",
            }
        rows = await self.client.get(
            "/admin-events",
            operationTypes=operation_types,
            resourceTypes=resource_types,
            authUser=_uuid(auth_user_id, "auth_user_id") if auth_user_id else None,
            resourcePath=resource_path,
            dateFrom=date_from,
            dateTo=date_to,
            direction="desc",
            first=first,
            max=max + 1,
        )
        events = [admin_event_view(e) for e in rows[:max] if isinstance(e, dict)]
        return {
            "admin_events_enabled": True,
            "details_enabled": bool(realm.get("adminEventsDetailsEnabled")),
            "events": events,
            "count": len(events),
            "has_more": len(rows) > max,
        }

    async def _login_times(self, cutoff: datetime) -> tuple[dict[str, int], int, bool]:
        """The newest LOGIN event time (millis) of each user since `cutoff`, the events read, and whether the
        read was cut."""
        latest: dict[str, int] = {}
        read = 0
        first = 0
        for _ in range(MAX_EVENT_PAGES):
            rows = await self.client.get(
                "/events", type="LOGIN", dateFrom=cutoff.strftime("%Y-%m-%d"), first=first, max=1000
            )
            for event in rows:
                read += 1
                user, time = event.get("userId"), event.get("time")
                if isinstance(user, str) and isinstance(time, int):
                    latest[user] = max(latest.get(user, 0), time)
            if len(rows) < 1000:
                return latest, read, False
            first += 1000
        return latest, read, True

    async def dormant_users(
        self, days: int = 90, include_never_logged_in: bool = True, limit: int = 100
    ) -> dict[str, Any]:
        """Enabled accounts with no LOGIN event in the last `days` days and no session active in that window.

        Relies on the realm's login events (type LOGIN, retained for as long as the realm's event expiration
        allows) and, for each candidate, on its sessions' last access. It is evidence of non-use, not proof:
        see `evidence`."""
        _bounded(days, 1, 3650, "days")
        _bounded(limit, 1, 500, "limit")
        now = self.now()
        cutoff = now - timedelta(days=days)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        realm = await self._realm()
        expiration = realm.get("eventsExpiration")
        retention_days = round(expiration / 86400, 1) if isinstance(expiration, int) and expiration > 0 else None
        evidence: dict[str, Any] = {
            "login_events_enabled": bool(realm.get("eventsEnabled")),
            "retention_days": retention_days,
            "window_days": days,
            "window_covered_by_retention": retention_days is None or retention_days >= days,
        }
        if not evidence["login_events_enabled"]:
            return {
                "reliable": False,
                "reason": "login events are disabled in this realm: nothing tells when anyone last logged in",
                "evidence": evidence,
                "dormant": [],
                "count": 0,
            }
        latest, read, events_cut = await self._login_times(cutoff)
        evidence |= {"login_events_read": read, "events_truncated": events_cut}
        users, users_cut = await self.client.get_page("/users", limit=MAX_USERS_SCAN, briefRepresentation="true")
        evidence |= {"users_examined": len(users), "users_truncated": users_cut}
        candidates = []
        for user in users:
            if not user.get("enabled", True) or str(user.get("id")) in {u for u, t in latest.items() if t >= cutoff_ms}:
                continue
            created = user.get("createdTimestamp")
            if isinstance(created, int) and created >= cutoff_ms:
                continue  # too new to be judged
            candidates.append(user)
        sem = asyncio.Semaphore(8)

        async def judge(user: dict[str, Any]) -> dict[str, Any] | None:
            uid = str(user["id"])
            async with sem:
                sessions = await self.client.get(f"/users/{uid}/sessions")
                if any(isinstance(s, dict) and s.get("lastAccess", 0) >= cutoff_ms for s in sessions):
                    return None  # a session was used in the window
                older = await self.client.get("/events", type="LOGIN", user=uid, first=0, max=1)
            last = older[0].get("time") if older and isinstance(older[0], dict) else None
            if last is None and not include_never_logged_in:
                return None
            entry = user_view(user)
            entry["last_login"] = _iso(last)
            entry["days_since_last_login"] = (
                None if last is None else int((now.timestamp() * 1000 - last) // 86_400_000)
            )
            if last is None:
                entry["note"] = "no login event retained for this user (never logged in, or older than the retention)"
            return entry

        judged = await asyncio.gather(*(judge(u) for u in candidates[:limit]))
        dormant = [d for d in judged if d is not None]
        return {
            "reliable": evidence["window_covered_by_retention"] and not events_cut and not users_cut,
            "evidence": evidence,
            "dormant": dormant,
            "count": len(dormant),
            "candidates_not_judged": max(len(candidates) - limit, 0),
            "relies_on": "LOGIN events of the window, and the last access of each candidate's sessions",
        }

    async def users_without_group(self, max_users: int = 500, limit: int = 100) -> dict[str, Any]:
        """Enabled users that belong to no group, among the first `max_users` users."""
        _bounded(max_users, 1, MAX_USERS_SCAN, "max_users")
        _bounded(limit, 1, 500, "limit")
        users, more = await self.client.get_page("/users", limit=max_users, briefRepresentation="true")
        sem = asyncio.Semaphore(8)

        async def groups_of(user: dict[str, Any]) -> tuple[dict[str, Any], int]:
            async with sem:
                rows = await self.client.get(f"/users/{user['id']}/groups", briefRepresentation="true")
            return user, len(rows)

        enabled = [u for u in users if u.get("enabled", True)]
        checked = await asyncio.gather(*(groups_of(u) for u in enabled))
        loose = [user_view(u) for u, n in checked if n == 0]
        return {
            "users": loose[:limit],
            "count": min(len(loose), limit),
            "total_without_group": len(loose),
            "users_examined": len(enabled),
            "truncated": more or len(loose) > limit,
        }

    # -- writes --

    def _remember(self, key: str | None, result: dict[str, Any]) -> dict[str, Any]:
        if key:
            self._done[key] = result
            while len(self._done) > DEDUPE_SIZE:
                self._done.popitem(last=False)
        return result

    def _replayed(self, key: str | None, tool: str) -> dict[str, Any] | None:
        if key and f"{tool}:{key}" in self._done:
            return {**self._done[f"{tool}:{key}"], "deduplicated": True}
        return None

    async def create_user(
        self,
        username: str,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        enabled: bool = True,
        required_actions: list[str] | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent on `username`: an existing user is returned unchanged, never modified."""
        if not USERNAME.fullmatch(username or ""):
            raise invalid("username must be 1 to 255 characters without space, slash or '?#%'")
        if email is not None and not EMAIL.fullmatch(email):
            raise invalid("email is not an email address")
        actions = sorted(set(required_actions or []))
        if not set(actions) <= REQUIRED_ACTIONS:
            raise invalid(f"required_actions must be among {sorted(REQUIRED_ACTIONS)}")
        name = username.lower()  # Keycloak stores usernames in lower case
        found = await self.client.get("/users", username=name, exact="true", briefRepresentation="true", max=2)
        for row in found:
            if isinstance(row, dict) and str(row.get("username", "")).lower() == name:
                return {
                    "created": False,
                    "user": user_view(row),
                    "note": "a user of that username exists; left unchanged",
                }
        body: dict[str, Any] = {"username": name, "enabled": enabled, "emailVerified": False}
        for key, value in (("email", email), ("firstName", first_name), ("lastName", last_name)):
            if value:
                body[key] = value
        if actions:
            body["requiredActions"] = actions
        response = await self.client.request("POST", "/users", json=body)
        location = response.headers.get("Location", "")
        new_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not UUID.fullmatch(new_id):
            again = await self.client.get("/users", username=name, exact="true", briefRepresentation="true", max=2)
            new_id = str(again[0]["id"]) if again else ""
        user = user_view(await self._user(new_id)) if new_id else {"username": name}
        log.info("user_created", action_key=action_key)
        return {"created": True, "user": user}

    async def add_user_to_group(self, user_id: str, group_id: str) -> dict[str, Any]:
        uid, gid = _uuid(user_id, "user_id"), _uuid(group_id, "group_id")
        user = await self._user(uid)
        group = await self.client.get(f"/groups/{gid}")
        before = await self.client.get(f"/users/{uid}/groups", briefRepresentation="true")
        was_member = any(isinstance(g, dict) and g.get("id") == gid for g in before)
        if not was_member:
            await self.client.request("PUT", f"/users/{uid}/groups/{gid}")
        return {
            "user_id": uid,
            "username": user.get("username"),
            "group_id": gid,
            "group_path": group.get("path") if isinstance(group, dict) else None,
            "was_member": was_member,
            "changed": not was_member,
        }

    async def remove_user_from_group(self, user_id: str, group_id: str) -> dict[str, Any]:
        uid, gid = _uuid(user_id, "user_id"), _uuid(group_id, "group_id")
        user = await self._user(uid)
        group = await self.client.get(f"/groups/{gid}")
        before = await self.client.get(f"/users/{uid}/groups", briefRepresentation="true")
        was_member = any(isinstance(g, dict) and g.get("id") == gid for g in before)
        if was_member:
            await self.client.request("DELETE", f"/users/{uid}/groups/{gid}")
        return {
            "user_id": uid,
            "username": user.get("username"),
            "group_id": gid,
            "group_path": group.get("path") if isinstance(group, dict) else None,
            "was_member": was_member,
            "changed": was_member,
        }

    async def _set_enabled(self, user_id: str, enabled: bool) -> dict[str, Any]:
        uid = _uuid(user_id, "user_id")
        # The whole representation is sent back with `enabled` changed, as the admin console does: a partial
        # body is not guaranteed to leave the other fields (attributes) alone on every release.
        current = await self._user(uid)
        previous = bool(current.get("enabled", True))
        if previous != enabled:
            await self.client.request("PUT", f"/users/{uid}", json={**current, "enabled": enabled})
        return {
            "user_id": uid,
            "username": current.get("username"),
            "enabled": enabled,
            "previous_enabled": previous,
            "changed": previous != enabled,
        }

    async def disable_user(self, user_id: str) -> dict[str, Any]:
        return await self._set_enabled(user_id, False)

    async def enable_user(self, user_id: str) -> dict[str, Any]:
        return await self._set_enabled(user_id, True)

    async def logout_user(self, user_id: str) -> dict[str, Any]:
        uid = _uuid(user_id, "user_id")
        user = await self._user(uid)
        sessions = await self.client.get(f"/users/{uid}/sessions")
        await self.client.request("POST", f"/users/{uid}/logout")
        return {"user_id": uid, "username": user.get("username"), "sessions_revoked": len(sessions)}

    async def send_required_actions_email(
        self,
        user_id: str,
        actions: list[str],
        lifespan_seconds: int = DEFAULT_LIFESPAN_S,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        replay = self._replayed(action_key, "send_required_actions_email")
        if replay is not None:
            return replay
        uid = _uuid(user_id, "user_id")
        chosen = sorted(set(actions))
        if not chosen or not set(chosen) <= REQUIRED_ACTIONS:
            raise invalid(f"actions must be a non-empty list among {sorted(REQUIRED_ACTIONS)}")
        _bounded(lifespan_seconds, 60, MAX_LIFESPAN_S, "lifespan_seconds")
        if redirect_uri and not client_id:
            raise invalid("redirect_uri needs client_id")
        if redirect_uri and not redirect_uri.startswith("https://"):
            raise invalid("redirect_uri must be an https:// URL (and one of the client's valid redirect URIs)")
        user = await self._user(uid)
        if not user.get("email"):
            raise invalid("the user has no email address: Keycloak has nowhere to send the message")
        if not (await self._realm_smtp_configured()):
            raise invalid("the realm has no SMTP server configured: Keycloak cannot send email")
        await self.client.request(
            "PUT",
            f"/users/{uid}/execute-actions-email",
            params={"lifespan": lifespan_seconds, "client_id": client_id, "redirect_uri": redirect_uri},
            json=chosen,
        )
        result = {
            "user_id": uid,
            "username": user.get("username"),
            "email": user.get("email"),
            "actions": chosen,
            "lifespan_seconds": lifespan_seconds,
            "sent": True,
        }
        if action_key:
            self._remember(f"send_required_actions_email:{action_key}", result)
        return result

    async def _realm_smtp_configured(self) -> bool:
        return bool(realm_view(await self._realm())["smtp_configured"])
