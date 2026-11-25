# SPDX-License-Identifier: Apache-2.0
"""The tools of the `identity/identity@1` profile for Keycloak, and the two Keycloak-only extras.

Rules this module enforces before and after every call:

- **The allow-list comes first.** Every tool takes a `directory` (a Keycloak realm). `Tools._dir` refuses one
  that is not in the configured list, by exact match, before any argument is read or any request is sent.
- **Whitelists, never blacklists.** Every object returned is rebuilt from the fields named here, so a field
  Keycloak adds later is not passed on. A user never carries credentials or attributes (only the *kinds* of
  second factor enrolled); a client never carries its secret, its registration token nor its attributes; the
  realm summary never carries the SMTP password, the keys, nor any secret; an admin event never carries its
  `representation` (it can hold a client secret or a credential), only whether there was one.
- **Identifiers are validated** (a UUID for users, groups and clients) before they reach a URL path.
- **Never**: delete a user, read a credential, impersonate, change the realm's settings, touch a client's
  secret. No tool here does, and the service account is not asked for the roles that would allow it.
- Every list is capped and says when it was cut (`has_more` / `truncated` / `complete`).
- Plans (`plan_joiner`, `plan_leaver`) read and write nothing; they return the calls to propose.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from .client import KeycloakClient
from .errors import Failure, invalid

log = structlog.get_logger("tranibor_identity_keycloak")

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
USERNAME = re.compile(r"^[^\s/\\?#%\x00-\x1f]{1,255}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
EVENT_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: The profile's setup actions and the Keycloak required action each one is. Others (custom providers,
#: TERMS_AND_CONDITIONS...) are refused: these are the standard ones that make sense for a joiner or a review.
SETUP_ACTIONS = {
    "set_password": "UPDATE_PASSWORD",
    "enroll_mfa": "CONFIGURE_TOTP",
    "enroll_passkey": "webauthn-register-passwordless",
    "verify_email": "VERIFY_EMAIL",
    "update_profile": "UPDATE_PROFILE",
}
_SETUP_NAME = {v: k for k, v in SETUP_ACTIONS.items()}
#: Credential types that count as a second factor. Whether a passwordless passkey alone is a "second" factor is a
#: policy question; it is reported by its own name and counted as enrolled.
MFA_TYPES = frozenset({"otp", "webauthn", "webauthn-passwordless"})
#: The administrative roles of the built-in `realm-management` client that `list_privileged_users` looks for.
ADMIN_ROLES = (
    "realm-admin",
    "manage-users",
    "manage-realm",
    "manage-clients",
    "manage-authorization",
    "manage-identity-providers",
    "manage-events",
    "impersonation",
    "create-client",
)
PRIVILEGE_SCOPE = (
    "Read: holders of each realm-management client role listed (direct assignment) and the groups that carry such "
    "a role (their members, and the members of their subgroups). Not read: realm roles, roles given through a "
    "composite role, and administrators of the master realm's own admin console permissions. An empty answer is "
    "therefore not proof that nobody else administers the realm."
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


def _clean(view: dict[str, Any]) -> dict[str, Any]:
    """The view without its None values: the profile's optional fields are absent, never null."""
    return {k: v for k, v in view.items() if v is not None}


# ---- whitelists ----------------------------------------------------------------------------------------


def user_view(user: dict[str, Any]) -> dict[str, Any]:
    """A user in the profile's words, without credentials, attributes, federation links nor access details."""
    view: dict[str, Any] = {
        "id": user.get("id"),
        "username": user.get("username"),
        "enabled": bool(user.get("enabled", True)),
    }
    for source, target in (
        ("email", "email"),
        ("firstName", "first_name"),
        ("lastName", "last_name"),
        ("emailVerified", "email_verified"),
    ):
        if source in user:
            view[target] = user[source]
    if user.get("createdTimestamp") is not None:
        view["created_at"] = _iso(user["createdTimestamp"])
    if isinstance(user.get("requiredActions"), list):
        view["setup_actions"] = [_SETUP_NAME.get(str(a), str(a)) for a in user["requiredActions"]]
    return _clean(view)


def group_view(group: dict[str, Any]) -> dict[str, Any]:
    return _clean({"id": group.get("id"), "name": group.get("name"), "path": group.get("path")})


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
    return _clean(
        {
            "id": session.get("id"),
            "started_at": _iso(session.get("start")),
            "last_access_at": _iso(session.get("lastAccess")),
            "ip_address": session.get("ipAddress"),
            "remember_me": bool(session.get("rememberMe", False)),
            "applications": sorted(str(n) for n in clients.values()) if isinstance(clients, dict) else [],
        }
    )


def sign_in_event_view(event: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "time": _iso(event.get("time")),
            "type": event.get("type"),
            "user_id": event.get("userId"),
            "application": event.get("clientId"),
            "ip_address": event.get("ipAddress"),
            "error": event.get("error"),
        }
    )


def admin_event_view(event: dict[str, Any]) -> dict[str, Any]:
    auth = _obj(event.get("authDetails"))
    return _clean(
        {
            "time": _iso(event.get("time")),
            "operation": event.get("operationType"),
            "resource_type": event.get("resourceType"),
            "resource_path": event.get("resourcePath"),
            "error": event.get("error"),
            "actor_id": auth.get("userId"),
            "actor_ip_address": auth.get("ipAddress"),
            # The representation can hold a secret or a credential: never returned, only noted.
            "representation_present": bool(event.get("representation")),
        }
    )


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
    def __init__(
        self,
        client: KeycloakClient,
        realms: tuple[str, ...],
        instance: str = "identity-keycloak",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.realms = realms
        self.instance = instance
        self._clock = clock or (lambda: datetime.now(UTC))
        self._done: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def now(self) -> datetime:
        return self._clock()

    def _dir(self, directory: str) -> str:
        """The allow-list: an exact match with a configured realm, or a refusal before anything is sent."""
        if not isinstance(directory, str) or directory not in self.realms:
            raise invalid(
                f"directory {str(directory)[:60]!r} is not in this server's allow-list ({', '.join(self.realms)})"
            )
        return directory

    # -- the provider --

    async def get_provider(self) -> dict[str, Any]:
        return {"instance": self.instance, "provider": "keycloak", "directories": list(self.realms)}

    async def _realm(self, d: str) -> dict[str, Any]:
        realm = await self.client.get(d, "")
        if not isinstance(realm, dict):
            raise Failure("upstream_error", "Keycloak answered a non-object for the realm")
        return realm

    async def get_directory(self, directory: str) -> dict[str, Any]:
        d = self._dir(directory)
        realm = await self._realm(d)
        expiration = realm.get("eventsExpiration")
        view: dict[str, Any] = {
            "directory": d,
            "display_name": realm.get("displayName"),
            "enabled": bool(realm.get("enabled", True)),
            "sign_in_events_enabled": bool(realm.get("eventsEnabled")),
            "admin_events_enabled": bool(realm.get("adminEventsEnabled")),
            "events_retention_days": round(expiration / 86400, 1)
            if isinstance(expiration, int) and expiration > 0
            else None,
            "mail_configured": bool(realm_view(realm)["smtp_configured"]),
        }
        # Unconfirmed on every release: `GET /users/count` answers a bare integer.
        with contextlib.suppress(Failure, ValueError, TypeError):
            view["user_count"] = int(await self.client.get(d, "/users/count"))
        return _clean(view)

    # -- users --

    async def find_users(
        self,
        directory: str,
        search: str | None = None,
        username: str | None = None,
        email: str | None = None,
        exact: bool = False,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, MAX_PAGE, "limit")
        _bounded(offset, 0, 1_000_000, "offset")
        rows = await self.client.get(
            d,
            "/users",
            search=search,
            username=username,
            email=email,
            exact="true" if exact else None,
            enabled=None if enabled is None else str(enabled).lower(),
            briefRepresentation="true",
            first=offset,
            max=limit + 1,
        )
        users = [user_view(u) for u in rows[:limit] if isinstance(u, dict)]
        return {"directory": d, "users": users, "count": len(users), "has_more": len(rows) > limit}

    async def _user(self, d: str, user_id: str) -> dict[str, Any]:
        row = await self.client.get(d, f"/users/{_uuid(user_id, 'user_id')}")
        if not isinstance(row, dict):
            raise Failure("upstream_error", "Keycloak answered a non-object for a user")
        return row

    async def _resolve(self, d: str, user_id: str | None, username: str | None) -> dict[str, Any]:
        """The whole user, named by exactly one of `user_id` and `username`."""
        if bool(user_id) == bool(username):
            raise invalid("give exactly one of user_id and username")
        if user_id:
            return await self._user(d, user_id)
        assert username is not None
        found = await self.client.get(d, "/users", username=username, exact="true", max=2)
        rows = [r for r in found if isinstance(r, dict) and str(r.get("username", "")).lower() == username.lower()]
        if not rows:
            raise Failure("not_found", f"no user named {username!r}")
        return await self._user(d, str(rows[0]["id"]))

    async def _mfa_methods(self, d: str, user_id: str) -> list[str]:
        """The kinds of second factor enrolled: the `type` of each credential, nothing else of it.

        Unconfirmed: `GET /users/{id}/credentials` returns `credentialData` (and a masked `secretData`) for each
        credential; they are never read here, the answer is rebuilt from `type` alone."""
        rows = await self.client.get(d, f"/users/{user_id}/credentials")
        types = {str(r.get("type")) for r in rows if isinstance(r, dict)}
        return sorted(types & MFA_TYPES)

    async def get_user(self, directory: str, user_id: str | None = None, username: str | None = None) -> dict[str, Any]:
        d = self._dir(directory)
        user = await self._resolve(d, user_id, username)
        result: dict[str, Any] = {"directory": d, "user": user_view(user)}
        try:
            result["mfa_methods"] = await self._mfa_methods(d, str(user["id"]))
        except Failure as failure:
            if failure.code != "forbidden":
                raise
        return result

    async def list_user_groups(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        d = self._dir(directory)
        user = await self._resolve(d, user_id, username)
        rows = await self.client.get(d, f"/users/{user['id']}/groups", briefRepresentation="true")
        groups = [group_view(g) for g in rows if isinstance(g, dict)]
        return {
            "directory": d,
            "user_id": user["id"],
            "username": user.get("username"),
            "groups": groups,
            "count": len(groups),
        }

    # -- groups --

    async def _children(
        self, d: str, group: dict[str, Any], depth: int, out: list[dict[str, Any]], budget: int
    ) -> None:
        if depth > 6 or len(out) >= budget:
            return
        if not group.get("subGroupCount") and "subGroupCount" in group:
            return
        kids, _ = await self.client.get_page(
            d, f"/groups/{group['id']}/children", limit=MAX_GROUPS, subGroupsCount="true"
        )
        for kid in kids:
            if len(out) >= budget:
                return
            out.append(kid)
            await self._children(d, kid, depth + 1, out, budget)

    async def _all_groups(self, d: str) -> list[dict[str, Any]]:
        top, _ = await self.client.get_page(
            d, "/groups", limit=MAX_GROUPS, briefRepresentation="true", subGroupsCount="true"
        )
        flat: list[dict[str, Any]] = []
        for group in top:
            flat.append(group)
            await self._children(d, group, 1, flat, MAX_GROUPS)
        return flat

    async def _members(self, d: str, group_id: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
        return await self.client.get_page(d, f"/groups/{group_id}/members", limit=limit, briefRepresentation="true")

    async def list_groups(
        self, directory: str, search: str | None = None, with_member_counts: bool = False, limit: int = 200
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, MAX_GROUPS, "limit")
        flat = await self._all_groups(d)
        needle = (search or "").lower()
        matched = [g for g in flat if not needle or needle in str(g.get("path", g.get("name", ""))).lower()]
        cut = len(matched) > limit
        views = [group_view(g) for g in matched[:limit]]
        if with_member_counts:
            sem = asyncio.Semaphore(8)

            async def count(view: dict[str, Any]) -> None:
                async with sem:
                    members, capped = await self._members(d, str(view["id"]), MAX_MEMBER_COUNT)
                view["member_count"] = len(members)
                if capped:
                    view["member_count_is_a_floor"] = True

            await asyncio.gather(*(count(v) for v in views))
        return {"directory": d, "groups": views, "count": len(views), "truncated": cut or len(flat) >= MAX_GROUPS}

    async def _group(self, d: str, group_id: str) -> dict[str, Any]:
        group = await self.client.get(d, f"/groups/{_uuid(group_id, 'group_id')}")
        if not isinstance(group, dict):
            raise Failure("upstream_error", "Keycloak answered a non-object for a group")
        return group

    async def get_group_members(
        self, directory: str, group_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, MAX_PAGE, "limit")
        _bounded(offset, 0, 1_000_000, "offset")
        group = await self._group(d, group_id)
        rows = await self.client.get(
            d, f"/groups/{group['id']}/members", briefRepresentation="true", first=offset, max=limit + 1
        )
        users = [user_view(u) for u in rows[:limit] if isinstance(u, dict)]
        return {
            "directory": d,
            "group": group_view(group),
            "members": users,
            "count": len(users),
            "has_more": len(rows) > limit,
        }

    async def compare_group_members(
        self, directory: str, group_id: str, expected_usernames: list[str]
    ) -> dict[str, Any]:
        d = self._dir(directory)
        if len(expected_usernames) > MAX_MEMBER_COUNT:
            raise invalid(f"expected_usernames holds at most {MAX_MEMBER_COUNT} names")
        group = await self._group(d, group_id)
        members, more = await self._members(d, str(group["id"]), MAX_MEMBER_COUNT)
        actual = {str(m.get("username", "")).lower(): m for m in members}
        wanted = {name.lower() for name in expected_usernames}
        missing = [name for name in expected_usernames if name.lower() not in actual]
        unexpected = [user_view(m) for name, m in actual.items() if name not in wanted]
        return {
            "directory": d,
            "group": group_view(group),
            "missing": missing,
            "unexpected": unexpected,
            "matched": len(wanted & set(actual)),
            "complete": not more,
        }

    async def list_user_sessions(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        d = self._dir(directory)
        user = await self._resolve(d, user_id, username)
        rows = await self.client.get(d, f"/users/{user['id']}/sessions")
        sessions = [session_view(s) for s in rows if isinstance(s, dict)]
        return {
            "directory": d,
            "user_id": user["id"],
            "username": user.get("username"),
            "sessions": sessions,
            "count": len(sessions),
        }

    # -- who has what --

    async def list_privileged_users(self, directory: str) -> dict[str, Any]:
        d = self._dir(directory)
        found = await self.client.get(d, "/clients", clientId="realm-management")
        management = next((c for c in found if isinstance(c, dict) and c.get("clientId") == "realm-management"), None)
        if management is None:
            raise Failure("not_found", "the realm has no realm-management client: its administrators cannot be read")
        cid = _uuid(str(management.get("id")), "realm-management client id")
        users: dict[str, dict[str, Any]] = {}
        grants: dict[str, list[dict[str, str]]] = {}
        complete = True

        def grant(user: dict[str, Any], privilege: str, via: str) -> None:
            uid = str(user.get("id"))
            users.setdefault(uid, user)
            entry = {"privilege": privilege, "via": via}
            if entry not in grants.setdefault(uid, []):
                grants[uid].append(entry)

        sem = asyncio.Semaphore(8)

        async def direct(role: str) -> None:
            nonlocal complete
            async with sem:
                try:
                    holders, more = await self.client.get_page(
                        d, f"/clients/{cid}/roles/{role}/users", limit=MAX_USERS_SCAN
                    )
                except Failure as failure:
                    if failure.code == "not_found":
                        return  # a role this Keycloak release does not have
                    raise
            complete = complete and not more
            for holder in holders:
                grant(holder, role, "direct")

        await asyncio.gather(*(direct(role) for role in ADMIN_ROLES))

        flat = await self._all_groups(d)
        carried: dict[str, list[str]] = {}

        async def mapped(group: dict[str, Any]) -> None:
            async with sem:
                rows = await self.client.get(d, f"/groups/{group['id']}/role-mappings/clients/{cid}")
            names = [str(r.get("name")) for r in rows if isinstance(r, dict) and r.get("name") in ADMIN_ROLES]
            if names:
                carried[str(group["path"])] = names

        await asyncio.gather(*(mapped(g) for g in flat))
        for group in flat:
            path = str(group["path"])
            for origin, roles in carried.items():
                if path == origin or path.startswith(origin + "/"):
                    members, more = await self._members(d, str(group["id"]), MAX_MEMBER_COUNT)
                    complete = complete and not more
                    for member in members:
                        for role in roles:
                            grant(member, role, f"group:{origin}")
        rows = [
            {**_pick(user_view(user), ("id", "username", "enabled", "email")), "grants": grants[uid]}
            for uid, user in sorted(users.items(), key=lambda kv: str(kv[1].get("username")))
        ]
        return {
            "directory": d,
            "users": rows,
            "count": len(rows),
            "scope": PRIVILEGE_SCOPE,
            "complete": complete and len(flat) < MAX_GROUPS,
        }

    # -- review --

    @staticmethod
    def _dates(date_from: str | None, date_to: str | None) -> None:
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if value is not None and not DATE.fullmatch(value):
                raise invalid(f"{name} must be a date, yyyy-MM-dd")

    async def list_sign_in_events(
        self,
        directory: str,
        types: list[str] | None = None,
        user_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, 200, "limit")
        _bounded(offset, 0, 1_000_000, "offset")
        self._dates(date_from, date_to)
        wanted = types or ["LOGIN"]
        if not all(EVENT_NAME.fullmatch(t) for t in wanted):
            raise invalid("types are event type names such as LOGIN, LOGIN_ERROR, LOGOUT")
        realm = await self._realm(d)
        if not realm.get("eventsEnabled"):
            return {
                "directory": d,
                "events_enabled": False,
                "events": [],
                "count": 0,
                "has_more": False,
                "note": "sign-in events are not enabled in this realm: there is nothing to read",
            }
        rows = await self.client.get(
            d,
            "/events",
            type=wanted,
            user=_uuid(user_id, "user_id") if user_id else None,
            dateFrom=date_from,
            dateTo=date_to,
            first=offset,
            max=limit + 1,
        )
        events = [sign_in_event_view(e) for e in rows[:limit] if isinstance(e, dict)]
        return {
            "directory": d,
            "events_enabled": True,
            "events": events,
            "count": len(events),
            "has_more": len(rows) > limit,
        }

    async def list_admin_events(
        self,
        directory: str,
        operations: list[str] | None = None,
        resource_types: list[str] | None = None,
        actor_id: str | None = None,
        resource_path: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, 200, "limit")
        _bounded(offset, 0, 1_000_000, "offset")
        self._dates(date_from, date_to)
        for name, values in (("operations", operations), ("resource_types", resource_types)):
            if values and not all(EVENT_NAME.fullmatch(v) for v in values):
                raise invalid(
                    f"{name} are names such as CREATE, UPDATE, DELETE, ACTION / USER, CLIENT, REALM_ROLE_MAPPING"
                )
        if resource_path is not None and not re.fullmatch(r"^[A-Za-z0-9_./*:-]{1,200}$", resource_path):
            raise invalid("resource_path is a path such as users/<id> or users/*")
        realm = await self._realm(d)
        if not realm.get("adminEventsEnabled"):
            return {
                "directory": d,
                "admin_events_enabled": False,
                "events": [],
                "count": 0,
                "has_more": False,
                "note": "admin events are not enabled in this realm: there is nothing to read",
            }
        rows = await self.client.get(
            d,
            "/admin-events",
            operationTypes=operations,
            resourceTypes=resource_types,
            authUser=_uuid(actor_id, "actor_id") if actor_id else None,
            resourcePath=resource_path,
            dateFrom=date_from,
            dateTo=date_to,
            direction="desc",
            first=offset,
            max=limit + 1,
        )
        events = [admin_event_view(e) for e in rows[:limit] if isinstance(e, dict)]
        result: dict[str, Any] = {
            "directory": d,
            "admin_events_enabled": True,
            "events": events,
            "count": len(events),
            "has_more": len(rows) > limit,
        }
        if not realm.get("adminEventsDetailsEnabled"):
            result["note"] = "admin events carry no representation in this realm (details are off)"
        return result

    async def _login_times(self, d: str, cutoff: datetime) -> tuple[dict[str, int], int, bool]:
        """The newest LOGIN event time (millis) of each user since `cutoff`, the events read, and whether the
        read was cut."""
        latest: dict[str, int] = {}
        read = 0
        first = 0
        for _ in range(MAX_EVENT_PAGES):
            rows = await self.client.get(
                d, "/events", type="LOGIN", dateFrom=cutoff.strftime("%Y-%m-%d"), first=first, max=1000
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
        self, directory: str, days: int = 90, include_never_signed_in: bool = True, limit: int = 100
    ) -> dict[str, Any]:
        """Enabled accounts with no LOGIN event in the last `days` days and no session active in that window.

        Relies on the realm's sign-in events (type LOGIN, retained for as long as the realm's event expiration
        allows) and, for each candidate, on its sessions' last access. It is evidence of non-use, not proof:
        see `evidence`."""
        d = self._dir(directory)
        _bounded(days, 1, 3650, "days")
        _bounded(limit, 1, 500, "limit")
        now = self.now()
        cutoff = now - timedelta(days=days)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        realm = await self._realm(d)
        expiration = realm.get("eventsExpiration")
        retention_days = round(expiration / 86400, 1) if isinstance(expiration, int) and expiration > 0 else None
        evidence: dict[str, Any] = {
            "sign_in_events_enabled": bool(realm.get("eventsEnabled")),
            "retention_days": retention_days,
            "window_days": days,
            "window_covered_by_retention": retention_days is None or retention_days >= days,
        }
        evidence = _clean(evidence)
        if not evidence["sign_in_events_enabled"]:
            return {
                "directory": d,
                "reliable": False,
                "reason": "sign-in events are disabled in this realm: nothing tells when anyone last signed in",
                "evidence": evidence,
                "dormant": [],
                "count": 0,
            }
        latest, read, events_cut = await self._login_times(d, cutoff)
        evidence |= {"sign_in_events_read": read, "events_truncated": events_cut}
        users, users_cut = await self.client.get_page(d, "/users", limit=MAX_USERS_SCAN, briefRepresentation="true")
        evidence |= {"users_examined": len(users), "users_truncated": users_cut}
        recent = {u for u, t in latest.items() if t >= cutoff_ms}
        candidates = []
        for user in users:
            if not user.get("enabled", True) or str(user.get("id")) in recent:
                continue
            created = user.get("createdTimestamp")
            if isinstance(created, int) and created >= cutoff_ms:
                continue  # too new to be judged
            candidates.append(user)
        sem = asyncio.Semaphore(8)

        async def judge(user: dict[str, Any]) -> dict[str, Any] | None:
            uid = str(user["id"])
            async with sem:
                sessions = await self.client.get(d, f"/users/{uid}/sessions")
                if any(isinstance(s, dict) and s.get("lastAccess", 0) >= cutoff_ms for s in sessions):
                    return None  # a session was used in the window
                older = await self.client.get(d, "/events", type="LOGIN", user=uid, first=0, max=1)
            last = older[0].get("time") if older and isinstance(older[0], dict) else None
            if last is None and not include_never_signed_in:
                return None
            entry = _pick(user_view(user), ("id", "username", "enabled", "email", "created_at"))
            entry["last_sign_in_at"] = _iso(last)
            entry["days_since_last_sign_in"] = (
                None if last is None else int((now.timestamp() * 1000 - last) // 86_400_000)
            )
            if last is None:
                entry["note"] = "no sign-in event retained for this user (never signed in, or older than the retention)"
            return _clean(entry)

        judged = await asyncio.gather(*(judge(u) for u in candidates[:limit]))
        dormant = [x for x in judged if x is not None]
        return {
            "directory": d,
            "reliable": evidence["window_covered_by_retention"] and not events_cut and not users_cut,
            "evidence": evidence,
            "dormant": dormant,
            "count": len(dormant),
            "candidates_not_judged": max(len(candidates) - limit, 0),
            "relies_on": "LOGIN events of the window, and the last access of each candidate's sessions",
        }

    async def users_without_mfa(self, directory: str, max_users: int = 500, limit: int = 100) -> dict[str, Any]:
        """Enabled users with no second factor enrolled, among the first `max_users` users."""
        d = self._dir(directory)
        _bounded(max_users, 1, MAX_USERS_SCAN, "max_users")
        _bounded(limit, 1, 500, "limit")
        users, more = await self.client.get_page(d, "/users", limit=max_users, briefRepresentation="true")
        enabled = [u for u in users if u.get("enabled", True)]
        sem = asyncio.Semaphore(8)

        async def methods(user: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
            async with sem:
                return user, await self._mfa_methods(d, str(user["id"]))

        checked = await asyncio.gather(*(methods(u) for u in enabled))
        bare = [user_view(u) for u, found in checked if not found]
        return {
            "directory": d,
            "users": bare[:limit],
            "count": min(len(bare), limit),
            "total_without_mfa": len(bare),
            "users_examined": len(enabled),
            "truncated": more or len(bare) > limit,
        }

    async def users_without_group(self, directory: str, max_users: int = 500, limit: int = 100) -> dict[str, Any]:
        """Enabled users that belong to no group, among the first `max_users` users."""
        d = self._dir(directory)
        _bounded(max_users, 1, MAX_USERS_SCAN, "max_users")
        _bounded(limit, 1, 500, "limit")
        users, more = await self.client.get_page(d, "/users", limit=max_users, briefRepresentation="true")
        sem = asyncio.Semaphore(8)

        async def groups_of(user: dict[str, Any]) -> tuple[dict[str, Any], int]:
            async with sem:
                rows = await self.client.get(d, f"/users/{user['id']}/groups", briefRepresentation="true")
            return user, len(rows)

        enabled = [u for u in users if u.get("enabled", True)]
        checked = await asyncio.gather(*(groups_of(u) for u in enabled))
        loose = [user_view(u) for u, n in checked if n == 0]
        return {
            "directory": d,
            "users": loose[:limit],
            "count": min(len(loose), limit),
            "total_without_group": len(loose),
            "users_examined": len(enabled),
            "truncated": more or len(loose) > limit,
        }

    # -- keycloak-only extras --

    async def list_clients(
        self, directory: str, client_id: str | None = None, search: bool = False, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        d = self._dir(directory)
        _bounded(limit, 1, MAX_PAGE, "limit")
        _bounded(offset, 0, 1_000_000, "offset")
        rows = await self.client.get(
            d, "/clients", clientId=client_id, search="true" if search else None, first=offset, max=limit + 1
        )
        clients = [client_view(c) for c in rows[:limit] if isinstance(c, dict)]
        return {"directory": d, "clients": clients, "count": len(clients), "has_more": len(rows) > limit}

    async def get_realm_summary(self, directory: str) -> dict[str, Any]:
        d = self._dir(directory)
        summary = realm_view(await self._realm(d))
        try:
            summary["user_count"] = int(await self.client.get(d, "/users/count"))
        except (Failure, ValueError, TypeError):
            summary["user_count"] = None
        try:
            clients, more = await self.client.get_page(d, "/clients", limit=MAX_USERS_SCAN)
            summary["client_count"] = len(clients)
            summary["client_count_is_a_floor"] = more
        except Failure:
            summary["client_count"] = None
        return {"directory": d, "realm": summary}

    # -- plans: they read, they write nothing, they say which writes to propose --

    async def plan_joiner(
        self,
        directory: str,
        username: str,
        email: str,
        first_name: str,
        last_name: str,
        group_ids: list[str] | None = None,
        setup_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        d = self._dir(directory)
        errors: list[str] = []
        warnings: list[str] = []
        if not USERNAME.fullmatch(username or ""):
            errors.append("username must be 1 to 255 characters without space, slash or '?#%'")
        if not EMAIL.fullmatch(email or ""):
            errors.append("email is not an email address")
        if not (first_name or "").strip() or not (last_name or "").strip():
            errors.append("first_name and last_name are both needed")
        actions = sorted(set(setup_actions or []))
        if not set(actions) <= set(SETUP_ACTIONS):
            errors.append(f"setup_actions must be among {sorted(SETUP_ACTIONS)}")
        ids: list[str] = []
        for gid in dict.fromkeys(group_ids or []):
            if not isinstance(gid, str) or not UUID.fullmatch(gid):
                errors.append(f"group {str(gid)[:60]!r} is not a group id (read them with list_groups)")
            else:
                ids.append(gid.lower())
        state: dict[str, Any] = {"account_exists": None, "groups": []}
        if not errors:
            found = await self.client.get(d, "/users", username=username.lower(), exact="true", max=2)
            exists = any(isinstance(r, dict) and str(r.get("username", "")).lower() == username.lower() for r in found)
            state["account_exists"] = exists
            if exists:
                errors.append(f"an account named {username!r} already exists: nothing to create")
            for gid in ids:
                try:
                    state["groups"].append(group_view(await self._group(d, gid)))
                except Failure as failure:
                    if failure.code != "not_found":
                        raise
                    errors.append(f"group {gid} does not exist")
            mail = bool(realm_view(await self._realm(d))["smtp_configured"])
        else:
            mail = False
        if errors:
            return {
                "directory": d,
                "valid": False,
                "errors": errors,
                "warnings": warnings,
                "state": state,
                "proposed_calls": [],
            }
        calls: list[dict[str, Any]] = [
            {
                "tool": "create_user",
                "arguments": _clean(
                    {
                        "directory": d,
                        "username": username,
                        "email": email,
                        "first_name": first_name,
                        "last_name": last_name,
                        "setup_actions": actions or None,
                    }
                ),
                "reason": "create the account without a password",
            }
        ]
        for group in state["groups"]:
            calls.append(
                {
                    "tool": "add_user_to_group",
                    "arguments": {"directory": d, "username": username, "group_id": group["id"]},
                    "reason": f"membership of {group['path']}",
                }
            )
        if mail:
            calls.append(
                {
                    "tool": "send_setup_email",
                    "arguments": {"directory": d, "username": username, "actions": actions or ["set_password"]},
                    "reason": "email the person the link to finish setting up",
                }
            )
        else:
            warnings.append(
                "the directory cannot send mail: no setup email is proposed; the person needs another way to a "
                "first sign-in"
            )
        return {
            "directory": d,
            "valid": True,
            "errors": [],
            "warnings": warnings,
            "state": state,
            "proposed_calls": calls,
        }

    async def plan_leaver(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        d = self._dir(directory)
        if bool(user_id) == bool(username):
            raise invalid("give exactly one of user_id and username")
        try:
            user = await self._resolve(d, user_id, username)
        except Failure as failure:
            if failure.code != "not_found":
                raise
            return {
                "directory": d,
                "valid": False,
                "errors": [failure.args[0]],
                "warnings": [],
                "state": {},
                "proposed_calls": [],
            }
        uid = str(user["id"])
        groups = [
            group_view(g)
            for g in await self.client.get(d, f"/users/{uid}/groups", briefRepresentation="true")
            if isinstance(g, dict)
        ]
        sessions = await self.client.get(d, f"/users/{uid}/sessions")
        enabled = bool(user.get("enabled", True))
        warnings: list[str] = []
        calls: list[dict[str, Any]] = []
        if enabled:
            calls.append(
                {
                    "tool": "disable_user",
                    "arguments": {"directory": d, "user_id": uid},
                    "reason": "the account can no longer sign in",
                }
            )
        else:
            warnings.append("the account is already disabled")
        if sessions:
            calls.append(
                {
                    "tool": "logout_user",
                    "arguments": {"directory": d, "user_id": uid},
                    "reason": f"{len(sessions)} session(s) are still open",
                }
            )
        for group in groups:
            calls.append(
                {
                    "tool": "remove_user_from_group",
                    "arguments": {"directory": d, "user_id": uid, "group_id": group["id"]},
                    "reason": f"membership of {group['path']}",
                }
            )
        if not calls:
            warnings.append("nothing is left to do for this account")
        return {
            "directory": d,
            "valid": True,
            "errors": [],
            "warnings": warnings,
            "state": {
                "user": user_view(user),
                "groups": groups,
                "open_sessions": len(sessions),
            },
            "proposed_calls": calls,
        }

    # -- writes --

    def _remember(self, key: str, result: dict[str, Any]) -> None:
        self._done[key] = result
        while len(self._done) > DEDUPE_SIZE:
            self._done.popitem(last=False)

    async def create_user(
        self,
        directory: str,
        username: str,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        enabled: bool = True,
        setup_actions: list[str] | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent on `username`: an existing user is returned unchanged, never modified. That also makes a
        replay of the same `action_key` harmless, so the key is only logged."""
        d = self._dir(directory)
        if not USERNAME.fullmatch(username or ""):
            raise invalid("username must be 1 to 255 characters without space, slash or '?#%'")
        if email is not None and not EMAIL.fullmatch(email):
            raise invalid("email is not an email address")
        actions = sorted(set(setup_actions or []))
        if not set(actions) <= set(SETUP_ACTIONS):
            raise invalid(f"setup_actions must be among {sorted(SETUP_ACTIONS)}")
        name = username.lower()  # Keycloak stores usernames in lower case
        found = await self.client.get(d, "/users", username=name, exact="true", briefRepresentation="true", max=2)
        for row in found:
            if isinstance(row, dict) and str(row.get("username", "")).lower() == name:
                return {
                    "directory": d,
                    "created": False,
                    "user": user_view(row),
                    "note": "a user of that username exists; left unchanged",
                }
        body: dict[str, Any] = {"username": name, "enabled": enabled, "emailVerified": False}
        for key, value in (("email", email), ("firstName", first_name), ("lastName", last_name)):
            if value:
                body[key] = value
        if actions:
            body["requiredActions"] = [SETUP_ACTIONS[a] for a in actions]
        response = await self.client.request("POST", d, "/users", json=body)
        location = response.headers.get("Location", "")
        new_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not UUID.fullmatch(new_id):
            again = await self.client.get(d, "/users", username=name, exact="true", briefRepresentation="true", max=2)
            new_id = str(again[0]["id"]) if again else ""
        user = user_view(await self._user(d, new_id)) if new_id else {"id": "", "username": name, "enabled": enabled}
        log.info("user_created", directory=d, action_key=action_key)
        return {"directory": d, "created": True, "user": user}

    async def _membership(
        self, directory: str, user_id: str | None, username: str | None, group_id: str, add: bool
    ) -> dict[str, Any]:
        d = self._dir(directory)
        user = await self._resolve(d, user_id, username)
        group = await self._group(d, group_id)
        uid, gid = str(user["id"]), str(group["id"])
        before = await self.client.get(d, f"/users/{uid}/groups", briefRepresentation="true")
        was_member = any(isinstance(g, dict) and g.get("id") == gid for g in before)
        changed = was_member != add
        if changed:
            await self.client.request("PUT" if add else "DELETE", d, f"/users/{uid}/groups/{gid}")
        return _clean(
            {
                "directory": d,
                "user_id": uid,
                "username": user.get("username"),
                "group_id": gid,
                "group_path": group.get("path"),
                "was_member": was_member,
                "changed": changed,
            }
        )

    async def add_user_to_group(
        self, directory: str, group_id: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        return await self._membership(directory, user_id, username, group_id, True)

    async def remove_user_from_group(
        self, directory: str, group_id: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        return await self._membership(directory, user_id, username, group_id, False)

    async def _set_enabled(
        self, directory: str, user_id: str | None, username: str | None, enabled: bool
    ) -> dict[str, Any]:
        d = self._dir(directory)
        # The whole representation is sent back with `enabled` changed, as the admin console does: a partial
        # body is not guaranteed to leave the other fields (attributes) alone on every release.
        current = await self._resolve(d, user_id, username)
        previous = bool(current.get("enabled", True))
        if previous != enabled:
            await self.client.request("PUT", d, f"/users/{current['id']}", json={**current, "enabled": enabled})
        return _clean(
            {
                "directory": d,
                "user_id": current["id"],
                "username": current.get("username"),
                "enabled": enabled,
                "previous_enabled": previous,
                "changed": previous != enabled,
            }
        )

    async def disable_user(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        return await self._set_enabled(directory, user_id, username, False)

    async def enable_user(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        return await self._set_enabled(directory, user_id, username, True)

    async def logout_user(
        self, directory: str, user_id: str | None = None, username: str | None = None
    ) -> dict[str, Any]:
        d = self._dir(directory)
        user = await self._resolve(d, user_id, username)
        sessions = await self.client.get(d, f"/users/{user['id']}/sessions")
        await self.client.request("POST", d, f"/users/{user['id']}/logout")
        return _clean(
            {
                "directory": d,
                "user_id": user["id"],
                "username": user.get("username"),
                "sessions_revoked": len(sessions),
            }
        )

    async def send_setup_email(
        self,
        directory: str,
        actions: list[str],
        user_id: str | None = None,
        username: str | None = None,
        lifespan_seconds: int = DEFAULT_LIFESPAN_S,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        d = self._dir(directory)
        chosen = sorted(set(actions))
        if not chosen or not set(chosen) <= set(SETUP_ACTIONS):
            raise invalid(f"actions must be a non-empty list among {sorted(SETUP_ACTIONS)}")
        _bounded(lifespan_seconds, 60, MAX_LIFESPAN_S, "lifespan_seconds")
        user = await self._resolve(d, user_id, username)
        key = f"{d}:send_setup_email:{action_key}" if action_key else None
        if key is not None and key in self._done:
            return {**self._done[key], "deduplicated": True}
        if not user.get("email"):
            raise invalid("the user has no email address: Keycloak has nowhere to send the message")
        if not realm_view(await self._realm(d))["smtp_configured"]:
            raise invalid("the realm has no SMTP server configured: Keycloak cannot send email (mail)")
        await self.client.request(
            "PUT",
            d,
            f"/users/{user['id']}/execute-actions-email",
            params={"lifespan": lifespan_seconds},
            json=[SETUP_ACTIONS[a] for a in chosen],
        )
        result = {
            "directory": d,
            "user_id": user["id"],
            "username": user.get("username"),
            "email": user.get("email"),
            "actions": chosen,
            "lifespan_seconds": lifespan_seconds,
            "sent": True,
        }
        if key is not None:
            self._remember(key, result)
        return result
