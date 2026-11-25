# SPDX-License-Identifier: Apache-2.0
"""What the tools hand out: Argo CD's large objects reduced to what a diagnosis needs.

Three rules shape every function here:

- **Minimised.** An Application's spec can hold Helm values, and a managed Secret holds its data: neither
  leaves the server. A repository URL loses its credentials; the value of a `Secret` field, and of any
  field whose name looks like a credential, is never returned (only its path).
- **Bounded.** Lists are cut (`truncated` says so) and texts are truncated.
- **Marked.** Messages, commit messages and events are text written outside this platform: they travel in
  fields the agent is told to read as data (`INSTRUCTIONS` of the server, the agent's own prompt).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MAX_TEXT = 500
MAX_VALUE = 80
RUNNING_PHASES = frozenset({"Running", "Terminating"})
#: Fields Argo CD compares after normalisation but that only record Kubernetes bookkeeping.
_BOOKKEEPING = frozenset({"managedFields", "resourceVersion", "uid", "creationTimestamp", "generation", "selfLink"})
_CREDENTIAL = re.compile(r"password|passwd|secret|token|apikey|api_key|private|credential|authorization", re.I)
#: Fields a controller commonly owns: when they are the only difference, the usual cause is a missing
#: `ignoreDifferences`, not a drift. A hint for the diagnosis, never a verdict.
NOISE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/spec/replicas$"), "spec.replicas is usually owned by a HorizontalPodAutoscaler or KEDA"),
    (re.compile(r"/caBundle$"), "caBundle is usually injected by cert-manager or the webhook's own controller"),
    (re.compile(r"^/metadata/annotations/deployment\.kubernetes\.io~1revision$"), "set by the Deployment controller"),
    (re.compile(r"restartedAt"), "set by `kubectl rollout restart`: a person changed the live object"),
    (re.compile(r"^/spec/clusterIP"), "allocated by the API server when the manifest leaves it out"),
    (re.compile(r"^/spec/(volumeName|storageClassName)$"), "filled in by the storage provisioner"),
)


def trunc(text: Any, limit: int = MAX_TEXT) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def safe_url(url: str | None) -> str | None:
    """A repository URL without the credentials it may carry (`https://user:token@host/…`)."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable URL)"
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return url


def _sources(spec: dict[str, Any]) -> list[dict[str, Any]]:
    sources = spec.get("sources")
    if isinstance(sources, list) and sources:
        return [s for s in sources if isinstance(s, dict)]
    source = spec.get("source")
    return [source] if isinstance(source, dict) else []


def history_of(app: dict[str, Any]) -> list[dict[str, Any]]:
    """`status.history`, oldest first, each `{id, revision, deployedAt, initiatedBy}`."""
    raw = (app.get("status") or {}).get("history") or []
    return sorted((h for h in raw if isinstance(h, dict)), key=lambda h: int(h.get("id", 0)))


def auto_sync(app: dict[str, Any]) -> bool:
    policy = (app.get("spec") or {}).get("syncPolicy") or {}
    return policy.get("automated") is not None


def operation_phase(app: dict[str, Any]) -> str | None:
    state = (app.get("status") or {}).get("operationState") or {}
    phase = state.get("phase")
    return str(phase) if phase else None


def operation_running(app: dict[str, Any]) -> bool:
    """An operation is requested (`operation` of the Application) or still running (`operationState`)."""
    return bool(app.get("operation")) or operation_phase(app) in RUNNING_PHASES


def selective_resources(app: dict[str, Any]) -> list[dict[str, str]]:
    """The resource list of the operation recorded in `status.operationState`, empty for a full sync."""
    state = (app.get("status") or {}).get("operationState") or {}
    sync = (state.get("operation") or {}).get("sync") or {}
    return [
        {k: str(r.get(k) or "") for k in ("group", "kind", "namespace", "name")}
        for r in sync.get("resources") or []
        if isinstance(r, dict)
    ]


def lingering_selective(app: dict[str, Any]) -> dict[str, Any] | None:
    """The known trap: a selective sync whose resource list is still in `status.operationState`.

    A later sync, an automated retry or a sync started from a client that reuses the last operation's
    parameters can replay that old list and sync only those resources, leaving the rest OutOfSync with
    nothing running. Surfaced whenever the recorded operation is selective, finished or not."""
    resources = selective_resources(app)
    if not resources:
        return None
    state = (app.get("status") or {}).get("operationState") or {}
    phase = state.get("phase")
    return {
        "detected": True,
        "phase": phase,
        "finished_at": state.get("finishedAt"),
        "resources": resources[:50],
        "finished": phase not in RUNNING_PHASES,
        "why_it_matters": (
            "status.operationState still records a selective sync. A later sync that reuses the last "
            "operation's parameters syncs only these resources and leaves the others OutOfSync. Run a full "
            "sync (no resource list), and have the stale operationState cleared by an administrator when it "
            "keeps being replayed."
        ),
    }


def app_summary(app: dict[str, Any]) -> dict[str, Any]:
    meta, spec, status = app.get("metadata") or {}, app.get("spec") or {}, app.get("status") or {}
    sources = _sources(spec)
    first = sources[0] if sources else {}
    destination = spec.get("destination") or {}
    sync = status.get("sync") or {}
    health = status.get("health") or {}
    return {
        "name": meta.get("name"),
        "project": spec.get("project", "default"),
        "sync_status": sync.get("status"),
        "health": health.get("status"),
        "revision": sync.get("revision") or (sync.get("revisions") or [None])[0],
        "target_revision": first.get("targetRevision"),
        "repo": safe_url(first.get("repoURL")),
        "path": first.get("path") or first.get("chart"),
        "destination": {
            "server": destination.get("server") or destination.get("name"),
            "namespace": destination.get("namespace"),
        },
        "auto_sync": auto_sync(app),
        "operation_phase": operation_phase(app),
        "selective_operation_recorded": bool(selective_resources(app)),
    }


def _history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    initiator = entry.get("initiatedBy") or {}
    return {
        "id": entry.get("id"),
        "revision": entry.get("revision") or (entry.get("revisions") or [None])[0],
        "deployed_at": entry.get("deployedAt"),
        "initiated_by": initiator.get("username") or ("automated" if initiator.get("automated") else None),
    }


def operation_summary(app: dict[str, Any]) -> dict[str, Any] | None:
    state = (app.get("status") or {}).get("operationState")
    if not isinstance(state, dict):
        return None
    operation = state.get("operation") or {}
    sync = operation.get("sync") or {}
    results = (state.get("syncResult") or {}).get("resources") or []
    failed = [
        {
            "kind": r.get("kind"),
            "name": r.get("name"),
            "namespace": r.get("namespace"),
            "status": r.get("status"),
            "sync_phase": r.get("syncPhase"),
            "hook_type": r.get("hookType"),
            "hook_phase": r.get("hookPhase"),
            "message": trunc(r.get("message")),
        }
        for r in results
        if isinstance(r, dict)
        and (r.get("hookPhase") in ("Failed", "Error") or r.get("status") in ("SyncFailed", "Failed"))
    ]
    initiator = operation.get("initiatedBy") or {}
    return {
        "phase": state.get("phase"),
        "message": trunc(state.get("message")),
        "started_at": state.get("startedAt"),
        "finished_at": state.get("finishedAt"),
        "retry_count": state.get("retryCount"),
        "initiated_by": initiator.get("username") or ("automated" if initiator.get("automated") else None),
        "revision": sync.get("revision") or (state.get("syncResult") or {}).get("revision"),
        "prune": bool(sync.get("prune")),
        "dry_run": bool(sync.get("dryRun")),
        "sync_options": sync.get("syncOptions") or [],
        "selective_resources": selective_resources(app)[:50],
        "info": [{"name": i.get("name"), "value": trunc(i.get("value"), 200)} for i in operation.get("info") or []],
        "failed_resources": failed[:20],
        "resources_total": len(results),
    }


def app_detail(app: dict[str, Any]) -> dict[str, Any]:
    spec, status = app.get("spec") or {}, app.get("status") or {}
    history = [_history_entry(h) for h in history_of(app)]
    last_deployed = history[-1]["revision"] if history else None
    sync = status.get("sync") or {}
    compared = sync.get("revision") or (sync.get("revisions") or [None])[0]
    out_of_sync = [
        {
            "group": r.get("group") or "",
            "kind": r.get("kind"),
            "namespace": r.get("namespace"),
            "name": r.get("name"),
            "status": r.get("status"),
            "health": (r.get("health") or {}).get("status"),
            "requires_pruning": bool(r.get("requiresPruning")),
        }
        for r in status.get("resources") or []
        if isinstance(r, dict) and r.get("status") != "Synced"
    ]
    sync_policy = spec.get("syncPolicy") or {}
    detail = app_summary(app)
    detail.update(
        {
            "namespace": (app.get("metadata") or {}).get("namespace"),
            "health_message": trunc((status.get("health") or {}).get("message")),
            "reconciled_at": status.get("reconciledAt"),
            "conditions": [
                {"type": c.get("type"), "message": trunc(c.get("message")), "since": c.get("lastTransitionTime")}
                for c in status.get("conditions") or []
                if isinstance(c, dict)
            ],
            "sync_policy": {
                "automated": sync_policy.get("automated") is not None,
                "prune": bool((sync_policy.get("automated") or {}).get("prune")),
                "self_heal": bool((sync_policy.get("automated") or {}).get("selfHeal")),
                "options": sync_policy.get("syncOptions") or [],
                "retry": sync_policy.get("retry") is not None,
            },
            "ignore_differences": [
                {
                    k: v
                    for k, v in d.items()
                    if k
                    in (
                        "group",
                        "kind",
                        "name",
                        "namespace",
                        "jsonPointers",
                        "jqPathExpressions",
                        "managedFieldsManagers",
                    )
                }
                for d in spec.get("ignoreDifferences") or []
                if isinstance(d, dict)
            ],
            "operation_requested": bool(app.get("operation")),
            "operation_in_progress": operation_running(app),
            "operation_state": operation_summary(app),
            "lingering_selective_operation": lingering_selective(app),
            "last_deployed_revision": last_deployed,
            "compared_revision": compared,
            # Git moved since the last deployment: an OutOfSync is then (at least partly) a pending commit.
            "revision_moved": bool(last_deployed and compared and last_deployed != compared),
            "history": history[-10:][::-1],
            "history_note": "A selective sync is never recorded in the history, so it cannot be rolled back to.",
            "resources_not_synced": out_of_sync[:50],
            "resources_not_synced_truncated": len(out_of_sync) > 50,
        }
    )
    return detail


def tree_node(node: dict[str, Any]) -> dict[str, Any]:
    health = node.get("health") or {}
    return {
        "group": node.get("group") or "",
        "kind": node.get("kind"),
        "namespace": node.get("namespace"),
        "name": node.get("name"),
        "health": health.get("status"),
        "health_message": trunc(health.get("message"), 200) if health.get("message") else None,
        "parents": [f"{p.get('kind')}/{p.get('name')}" for p in node.get("parentRefs") or [] if isinstance(p, dict)],
        "created_at": node.get("createdAt"),
    }


def _state(text: Any) -> Any:
    """A state of a managed resource: a JSON text; `""`, `"null"` or nothing is an absent object."""
    if text in (None, "", "null"):
        return None
    if isinstance(text, str):
        try:
            return json.loads(text)
        except ValueError:
            return None
    return text


def _pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _walk(live: Any, target: Any, path: str, out: list[tuple[str, Any, Any]], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(live, dict) and isinstance(target, dict):
        for key in sorted(set(live) | set(target)):
            if path == "/metadata" and key in _BOOKKEEPING:
                continue
            if path == "" and key == "status":
                continue
            _walk(live.get(key), target.get(key), f"{path}/{_pointer(key)}", out, limit)
    elif isinstance(live, list) and isinstance(target, list) and len(live) == len(target):
        for index, (a, b) in enumerate(zip(live, target, strict=True)):
            _walk(a, b, f"{path}/{index}", out, limit)
    elif live != target:
        out.append((path, live, target))


def _shown(value: Any, path: str, redact: bool) -> str:
    if redact or _CREDENTIAL.search(path):
        return "(hidden)"
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    if value is None:
        return "(absent)"
    return trunc(text, MAX_VALUE)


def noise_hints(paths: list[str]) -> list[str]:
    hints: list[str] = []
    for path in paths:
        for pattern, hint in NOISE_HINTS:
            if pattern.search(path) and hint not in hints:
                hints.append(hint)
    return hints


def resource_change(item: dict[str, Any], limit: int = 15) -> dict[str, Any] | None:
    """One managed resource as a summary of how live differs from desired, or `None` when it is in sync.

    Compares what Argo CD compares: the live object after normalisation (`normalizedLiveState`) against the
    state a sync would produce (`predictedLiveState`), falling back to `liveState` and `targetState`. The
    `diff` text the API also carries is not used: it can hold Secret data."""
    live = _state(item.get("normalizedLiveState") or item.get("liveState"))
    target = _state(item.get("predictedLiveState") or item.get("targetState"))
    kind = item.get("kind")
    redact = kind == "Secret"
    if live is None and target is None:
        return None
    if live is None:
        reason = "missing_in_cluster"
        changes: list[dict[str, Any]] = []
    elif target is None:
        reason = "not_in_git"
        changes = []
    else:
        found: list[tuple[str, Any, Any]] = []
        _walk(live, target, "", found, limit + 1)
        if not found and not item.get("modified"):
            return None
        reason = "differs"
        changes = [
            {"path": p, "live": _shown(a, p, redact), "desired": _shown(b, p, redact)} for p, a, b in found[:limit]
        ]
    paths = [c["path"] for c in changes]
    return {
        "group": item.get("group") or "",
        "kind": kind,
        "namespace": item.get("namespace"),
        "name": item.get("name"),
        "reason": reason,
        "changes": changes,
        "changes_truncated": reason == "differs" and len(changes) >= limit and bool(changes),
        "noise_hints": noise_hints(paths),
        "all_changes_look_controller_owned": bool(paths)
        and all(any(pattern.search(p) for pattern, _ in NOISE_HINTS) for p in paths),
    }


def event_summary(event: dict[str, Any]) -> dict[str, Any]:
    involved = event.get("involvedObject") or {}
    return {
        "type": event.get("type"),
        "reason": event.get("reason"),
        "message": trunc(event.get("message")),
        "count": event.get("count"),
        "last_seen": event.get("lastTimestamp") or event.get("eventTime"),
        "object": f"{involved.get('kind')}/{involved.get('name')}",
    }


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
