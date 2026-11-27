# SPDX-License-Identifier: Apache-2.0
"""The tools of the `gitops/gitops@1` profile, served over Argo CD: what an agent may read, and the writes a
person approves.

A profile's `scope` is an Argo CD AppProject. Every tool names an Application, and every Application is checked
against the scope allow-list before anything else: one of another project is refused, whether the call reads or
writes. There is no tool that deletes an Application, edits a spec (but the automated-sync policy), or changes a
project or RBAC.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from .client import ArgoCDClient
from .config import Settings
from .errors import Refused
from .summary import (
    PAUSED_ANNOTATION,
    app_detail,
    app_summary,
    auto_sync,
    automated_settings,
    event_summary,
    history_of,
    lingering_selective,
    now_iso,
    operation_phase,
    operation_running,
    parent_managed,
    resource_change,
    tree_node,
    trunc,
)

log = structlog.get_logger("tranibor_gitops_argocd.tools")

MAX_APPS = 200
MAX_RESOURCES = 100
MAX_EVENTS = 50
MIN_JUSTIFICATION = 20
ACTION_KEY_INFO = "action_key"
PROVIDER = "argocd"


class ResourceRef(BaseModel):
    """One resource of an Application, as a selective sync names it."""

    model_config = ConfigDict(extra="forbid")

    group: str | None = Field(
        None,
        description='API group ("apps", "batch"; "" for the core group). Left out, it is read from the application.',
    )
    kind: str
    name: str
    namespace: str = Field("", description="Namespace; empty for a cluster-scoped resource.")


class ArgoCDTools:
    def __init__(self, client: ArgoCDClient, settings: Settings | None = None) -> None:
        self.client = client
        self.settings = settings or client.settings
        self.projects = frozenset(self.settings.projects)

    async def get_provider(self) -> dict[str, Any]:
        """Which provider and which scopes (AppProjects) this instance serves."""
        return {"instance": self.settings.instance, "provider": PROVIDER, "scopes": sorted(self.projects)}

    # ─────────────────────────── guard ───────────────────────────

    def _check_project(self, name: str, app: dict[str, Any]) -> None:
        project = str((app.get("spec") or {}).get("project") or "default")
        if project not in self.projects:
            log.warning("project_refused", application=name, project=project)
            raise Refused(
                f"application {name!r} is in scope {project!r}, which is not in this server's allow-list "
                f"({', '.join(sorted(self.projects))})"
            )

    async def _load(self, name: str, refresh: str | None = None) -> dict[str, Any]:
        if not name or "/" in name:
            raise Refused("an application name is required (no slash)")
        app = await self.client.get_application(name, refresh)
        self._check_project(name, app)
        return app

    # ─────────────────────────── reads ───────────────────────────

    async def list_applications(
        self,
        scope: str | None,
        sync_status: str | None,
        health: str | None,
        limit: int,
    ) -> dict[str, Any]:
        if scope is not None and scope not in self.projects:
            raise Refused(f"scope {scope!r} is not in this server's allow-list ({', '.join(sorted(self.projects))})")
        wanted = [scope] if scope else sorted(self.projects)
        items = await self.client.list_applications(wanted)
        # The server's filter is trusted for what it returns, not for what it must exclude: check again.
        apps = [a for a in items if str((a.get("spec") or {}).get("project") or "default") in self.projects]
        summaries = [app_summary(a) for a in apps]
        if sync_status:
            summaries = [s for s in summaries if s["sync_status"] == sync_status]
        if health:
            summaries = [s for s in summaries if s["health"] == health]
        summaries.sort(key=lambda s: str(s["name"]))
        limit = max(1, min(limit, MAX_APPS))
        return {
            "total": len(summaries),
            "truncated": len(summaries) > limit,
            "scopes": wanted,
            "applications": summaries[:limit],
        }

    async def get_application(self, name: str) -> dict[str, Any]:
        return app_detail(await self._load(name))

    async def list_resources(self, name: str, kind: str | None, unhealthy_only: bool, limit: int) -> dict[str, Any]:
        await self._load(name)
        tree = await self.client.resource_tree(name)
        nodes = [tree_node(n) for n in tree.get("nodes") or [] if isinstance(n, dict)]
        if kind:
            nodes = [n for n in nodes if n["kind"] == kind]
        if unhealthy_only:
            nodes = [n for n in nodes if n["health"] not in (None, "Healthy")]
        limit = max(1, min(limit, MAX_RESOURCES))
        return {
            "application": name,
            "total": len(nodes),
            "truncated": len(nodes) > limit,
            "nodes": nodes[:limit],
            "orphaned_nodes": len(tree.get("orphanedNodes") or []),
        }

    async def get_diff(self, name: str, limit: int) -> dict[str, Any]:
        app = await self._load(name)
        items = await self.client.managed_resources(name)
        managed = [i for i in items if isinstance(i, dict) and not i.get("hook")]
        changes = [c for i in managed if (c := resource_change(i)) is not None]
        limit = max(1, min(limit, MAX_RESOURCES))
        history = history_of(app)
        sync = (app.get("status") or {}).get("sync") or {}
        return {
            "application": name,
            "sync_status": sync.get("status"),
            "managed_total": len(managed),
            "out_of_sync_total": len(changes),
            "by_reason": {
                reason: sum(1 for c in changes if c["reason"] == reason)
                for reason in ("missing_in_cluster", "not_in_git", "differs")
            },
            "resources": changes[:limit],
            "truncated": len(changes) > limit,
            "last_deployed_revision": history[-1].get("revision") if history else None,
            "compared_revision": sync.get("revision"),
            "ignore_differences_declared": len((app.get("spec") or {}).get("ignoreDifferences") or []),
            "note": "Values of a Secret, and of any credential-looking field, are never returned.",
        }

    async def list_events(
        self, name: str, resource_name: str | None, resource_namespace: str | None, limit: int
    ) -> dict[str, Any]:
        await self._load(name)
        events = await self.client.events(name, resource_name, resource_namespace)
        events.sort(key=lambda e: str(e.get("lastTimestamp") or e.get("eventTime") or ""), reverse=True)
        limit = max(1, min(limit, MAX_EVENTS))
        return {
            "application": name,
            "total": len(events),
            "warnings": sum(1 for e in events if e.get("type") == "Warning"),
            "truncated": len(events) > limit,
            "events": [event_summary(e) for e in events[:limit]],
        }

    async def get_sync_windows(self, name: str) -> dict[str, Any]:
        await self._load(name)
        return await self._windows(name)

    async def _windows(self, name: str) -> dict[str, Any]:
        raw = await self.client.sync_windows(name)

        def window(w: dict[str, Any]) -> dict[str, Any]:
            return {k: w.get(k) for k in ("kind", "schedule", "duration", "manualSync")}

        return {
            "application": name,
            "can_sync": bool(raw.get("canSync")),
            "active_windows": [window(w) for w in raw.get("activeWindows") or [] if isinstance(w, dict)],
            "assigned_windows": [window(w) for w in raw.get("assignedWindows") or [] if isinstance(w, dict)],
        }

    async def get_history(self, name: str, limit: int) -> dict[str, Any]:
        app = await self._load(name)
        detail = app_detail(app)
        entries = detail["history"][: max(1, min(limit, 50))]
        return {
            "application": name,
            "auto_sync": detail["auto_sync"],
            "history": entries,
            "last_deployed_revision": detail["last_deployed_revision"],
            "compared_revision": detail["compared_revision"],
            "revision_moved": detail["revision_moved"],
            "note": detail["history_note"],
            "rollback_possible": not detail["auto_sync"] and bool(entries),
        }

    async def get_revision_metadata(self, name: str, revision: str) -> dict[str, Any]:
        await self._load(name)
        if not revision or "/" in revision:
            raise Refused("a revision (a commit SHA or a chart version) is required")
        raw = await self.client.revision_metadata(name, revision)
        return {
            "application": name,
            "revision": revision,
            "author": trunc(raw.get("author"), 200),
            "date": raw.get("date"),
            "message": trunc(raw.get("message")),
            "tags": raw.get("tags") or [],
        }

    async def plan_sync(self, name: str, resources: list[ResourceRef] | None, prune: bool) -> dict[str, Any]:
        """What a sync would do, computed from the diff and the Application's state: nothing is written.

        The API's own `dryRun` is not used: it is an Operation, which replaces `status.operationState`
        (the very state that shows a lingering selective sync) and can wait on hooks."""
        app = await self._load(name)
        items = [i for i in await self.client.managed_resources(name) if isinstance(i, dict) and not i.get("hook")]
        changes = [c for i in items if (c := resource_change(i)) is not None]
        chosen = self._selection(app, resources)
        if chosen is not None:
            changes = [c for c in changes if self._wanted(c, chosen)]
        windows = await self._windows(name)
        create = [c for c in changes if c["reason"] == "missing_in_cluster"]
        update = [c for c in changes if c["reason"] == "differs"]
        orphans = [c for c in changes if c["reason"] == "not_in_git"]

        def refs(rows: list[dict[str, Any]]) -> list[str]:
            return [f"{r['kind']}/{r['name']}" for r in rows][:50]

        return {
            "application": name,
            "mode": "selective" if chosen is not None else "full",
            "prune": prune,
            "would_create": refs(create),
            "would_update": refs(update),
            "would_prune": refs(orphans) if prune else [],
            "left_alone_without_prune": [] if prune else refs(orphans),
            "blocked": self._blockers(app, windows),
            "warnings": self._warnings(app, chosen),
            "not_simulated": "Hooks, sync waves, sync options and the cluster's admission are not simulated.",
        }

    # ─────────────────────────── writes ───────────────────────────

    async def refresh(self, name: str, mode: Literal["normal", "hard"]) -> dict[str, Any]:
        if mode not in ("normal", "hard"):
            raise Refused("mode is normal or hard")
        await self._load(name)
        app = await self._load(name, refresh=mode)
        return {
            "application": name,
            "refresh": mode,
            "requested_at": now_iso(),
            "state": app_summary(app),
            "note": "The controller reconciles asynchronously: read the Application again for the new status.",
        }

    @staticmethod
    def _selection(app: dict[str, Any], resources: list[ResourceRef] | None) -> list[dict[str, str]] | None:
        """The requested resources, completed from `status.resources`; each must belong to the Application."""
        if not resources:
            return None
        known = [r for r in (app.get("status") or {}).get("resources") or [] if isinstance(r, dict)]
        chosen: list[dict[str, str]] = []
        for ref in resources:
            match = next(
                (
                    r
                    for r in known
                    if (ref.group is None or (r.get("group") or "") == ref.group)
                    and r.get("kind") == ref.kind
                    and r.get("name") == ref.name
                    and (not ref.namespace or (r.get("namespace") or "") == ref.namespace)
                ),
                None,
            )
            if match is None:
                raise Refused(f"{ref.kind}/{ref.name} is not a resource of this application")
            chosen.append(
                {
                    "group": str(match.get("group") or ""),
                    "kind": ref.kind,
                    "name": ref.name,
                    "namespace": str(match.get("namespace") or ""),
                }
            )
        return chosen

    @staticmethod
    def _wanted(change: dict[str, Any], chosen: list[dict[str, str]]) -> bool:
        return any(
            c["kind"] == change["kind"]
            and c["name"] == change["name"]
            and c["group"] == (change["group"] or "")
            and c["namespace"] == (change["namespace"] or "")
            for c in chosen
        )

    @staticmethod
    def _blockers(app: dict[str, Any], windows: dict[str, Any]) -> list[str]:
        blockers: list[str] = []
        if operation_running(app):
            blockers.append("an operation is already running: wait for it, or ask an administrator to terminate it")
        if not windows["can_sync"]:
            blockers.append("a sync window denies syncs now")
        return blockers

    @staticmethod
    def _warnings(app: dict[str, Any], chosen: list[dict[str, str]] | None) -> list[str]:
        warnings: list[str] = []
        lingering = lingering_selective(app)
        if lingering is not None:
            names = ", ".join(f"{r['kind']}/{r['name']}" for r in lingering["resources"][:5])
            warnings.append(
                f"status.operationState still records a selective sync ({names}): the trap where a later sync "
                "reuses an old resource list. Check it before relying on this sync."
            )
        if chosen is not None:
            warnings.append(
                "a selective sync runs no hooks, is not recorded in the history (no rollback to it), and "
                "leaves its resource list in status.operationState"
            )
        return warnings

    async def sync(
        self,
        name: str,
        resources: list[ResourceRef] | None,
        justification: str | None,
        prune: bool,
        revision: str | None,
        action_key: str | None,
    ) -> dict[str, Any]:
        """A sync of the whole Application; a resource list only with a written justification.

        `revision` syncs to that revision instead of the Application's own target: it is how a rollback is
        undone. Argo CD refuses it while auto-sync is on (it only syncs to the target revision then)."""
        if resources and len((justification or "").strip()) < MIN_JUSTIFICATION:
            raise Refused(
                "a selective sync needs a `justification` (at least 20 characters) that says why a full sync "
                "is not enough: it runs no hooks, is not recorded in the history, and leaves its resource list "
                "in status.operationState"
            )
        if revision is not None and (not revision.strip() or "/" in revision):
            raise Refused("a revision is a commit SHA or a chart version, without a slash")
        app = await self._load(name)
        if action_key and _has_action_key(app, action_key):
            return {
                "application": name,
                "sync_requested": False,
                "already_applied": True,
                "note": "this governed action already started a sync: nothing was sent again",
            }
        if revision and chosen_partial(resources):
            raise Refused("a sync to a revision is a full sync: it takes no resource list")
        if revision and auto_sync(app):
            raise Refused(
                "this application has automated sync enabled: Argo CD syncs it to its target revision only. "
                "Pause auto-sync first (set_auto_sync, which also needs a person), or fix git"
            )
        windows = await self._windows(name)
        blockers = self._blockers(app, windows)
        if blockers:
            raise Refused(f"cannot sync {name!r}: {'; '.join(blockers)}")
        chosen = self._selection(app, resources)
        history = history_of(app)
        request: dict[str, Any] = {"name": name, "prune": prune, "dryRun": False}
        if chosen is not None:
            request["resources"] = chosen
        if revision:
            request["revision"] = revision.strip()
        if action_key:
            request["infos"] = [{"name": ACTION_KEY_INFO, "value": action_key}]
        warnings = self._warnings(app, chosen)
        before = operation_phase(app)
        await self.client.sync(name, request)
        log.info("sync_requested", application=name, selective=chosen is not None, prune=prune)
        return {
            "application": name,
            "scope": (app.get("spec") or {}).get("project"),
            "sync_requested": True,
            "already_applied": False,
            "revision": revision.strip() if revision else None,
            "mode": "selective" if chosen is not None else "full",
            "resources": chosen or [],
            "justification": justification.strip() if chosen and justification else None,
            "prune": prune,
            "previous_operation_phase": before,
            "previous_history_id": history[-1].get("id") if history else None,
            "warnings": warnings,
            "note": "The sync runs asynchronously: read the Application to see its operationState.",
        }

    async def rollback(self, name: str, history_id: int, prune: bool) -> dict[str, Any]:
        """Roll back to a deployment recorded in the history. The id in force before is returned, and
        rolling back to it undoes this call."""
        app = await self._load(name)
        if auto_sync(app):
            raise Refused(
                "rollback cannot be performed on an application with automated sync enabled (Argo CD refuses "
                "it): propose the revert in git instead, or have an administrator disable auto-sync"
            )
        if operation_running(app):
            raise Refused("an operation is already running: wait for it, or ask an administrator to terminate it")
        history = history_of(app)
        entry = next((h for h in history if int(h.get("id", -1)) == history_id), None)
        if entry is None:
            known = ", ".join(str(h.get("id")) for h in history)
            raise Refused(f"history id {history_id} is not in this application's history ({known or 'empty'})")
        previous = history[-1]
        if int(previous.get("id", -1)) == history_id:
            raise Refused(f"history id {history_id} is already the latest deployment")
        windows = await self._windows(name)
        if not windows["can_sync"]:
            raise Refused(f"cannot roll back {name!r}: a sync window denies syncs now")
        await self.client.rollback(name, {"name": name, "id": history_id, "prune": prune, "dryRun": False})
        log.info("rollback_requested", application=name, to=history_id)
        return {
            "application": name,
            "rollback_requested": True,
            "id": history_id,
            "revision": entry.get("revision"),
            "previous_id": int(previous["id"]),
            "previous_revision": previous.get("revision"),
            "prune": prune,
            "note": "Rolling back to previous_id undoes this rollback, while it stays in the history.",
        }

    async def set_auto_sync(self, name: str, enabled: bool) -> dict[str, Any]:
        """Pauses or resumes the automated sync of one Application, by patching `spec.syncPolicy.automated`.

        Pausing removes `automated` and keeps its settings (prune, self-heal, allow-empty) in an annotation of
        the Application, so that resuming restores them. Only these two keys are ever patched."""
        app = await self._load(name)
        now = auto_sync(app)
        warnings: list[str] = []
        if parent_managed(app):
            warnings.append(
                "this Application carries its parent's tracking marker (app-of-apps): a parent with self-heal "
                "reverts this change at its next sync. Change it where the parent defines it, if the pause "
                "does not hold"
            )
        if enabled == now:
            return {
                "application": name,
                "changed": False,
                "enabled": now,
                "previous_enabled": now,
                "warnings": warnings,
                "note": f"auto-sync was already {'on' if now else 'off'}: nothing was written",
            }
        annotations = (app.get("metadata") or {}).get("annotations") or {}
        if enabled:
            saved = annotations.get(PAUSED_ANNOTATION)
            automated = _saved_settings(saved)
            patch: dict[str, Any] = {
                "metadata": {"annotations": {PAUSED_ANNOTATION: None}},
                "spec": {"syncPolicy": {"automated": automated}},
            }
            if saved is None:
                warnings.append(
                    "no settings were saved by a pause: auto-sync resumes with the plain default (no prune, "
                    "no self-heal), which may differ from what it was"
                )
            if (((app.get("status") or {}).get("sync") or {}).get("status")) == "OutOfSync":
                warnings.append("the application is OutOfSync: the controller syncs it as soon as auto-sync is on")
        else:
            patch = {
                "metadata": {"annotations": {PAUSED_ANNOTATION: json.dumps(automated_settings(app), sort_keys=True)}},
                "spec": {"syncPolicy": {"automated": None}},
            }
        await self.client.patch_auto_sync(name, patch)
        log.info("auto_sync_set", application=name, enabled=enabled)
        return {
            "application": name,
            "changed": True,
            "enabled": enabled,
            "previous_enabled": now,
            "warnings": warnings,
            "note": "The controller applies the new policy at its next reconciliation: read the Application again.",
        }


def _has_action_key(app: dict[str, Any], key: str) -> bool:
    operations = [
        (app.get("operation") or {}),
        (((app.get("status") or {}).get("operationState") or {}).get("operation") or {}),
    ]
    return any(
        i.get("name") == ACTION_KEY_INFO and i.get("value") == key
        for op in operations
        for i in op.get("info") or []
        if isinstance(i, dict)
    )


def chosen_partial(resources: list[ResourceRef] | None) -> bool:
    return bool(resources)


def _saved_settings(saved: Any) -> dict[str, Any]:
    """The automated-sync settings a pause saved, restricted to the three keys Argo CD defines."""
    if not isinstance(saved, str):
        return {}
    try:
        loaded = json.loads(saved)
    except ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: bool(v) for k, v in loaded.items() if k in ("prune", "selfHeal", "allowEmpty")}
