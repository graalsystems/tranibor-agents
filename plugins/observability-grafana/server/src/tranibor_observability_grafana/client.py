# SPDX-License-Identifier: Apache-2.0
"""A thin Grafana HTTP API client: one base URL, one service-account token, JSON in and out.

Routes, and where each is confirmed (see procedures/connector.mcp.grafana.md for the URLs):

- Confirmed in Grafana's published HTTP API reference: `GET /api/v1/provisioning/alert-rules/{uid}`
  (marked deprecated there, in favour of the `rules.alerting.grafana.app` App Platform API, which this
  client does not use yet), `GET /api/annotations`, `POST /api/annotations`, `GET /api/datasources`,
  `POST /api/ds/query` (the query body of each datasource plugin is NOT documented there: the docs say to
  read it from the browser's requests), `GET /api/dashboards/uid/{uid}` and `GET /api/search` (both in the
  OpenAPI document of the Grafana repository; the narrative dashboard page documents the newer `/apis`
  route only).
- Confirmed in Grafana's source (`pkg/services/ngalert/api/authorization.go`), not in a narrative page:
  `GET /api/alertmanager/grafana/api/v2/alerts`, `GET|POST /api/alertmanager/grafana/api/v2/silences`,
  `DELETE /api/alertmanager/grafana/api/v2/silence/{id}`. They are the Alertmanager v2 API served by
  Grafana's embedded Alertmanager; the request and response shapes below are those of Alertmanager's v2
  OpenAPI document.
- NOT confirmed against a live Grafana (no account): the exact `/api/ds/query` bodies for Prometheus and
  Loki, and the frame layout of their replies. They follow the datasource plugins' query models; a live run
  (tests/live) is what proves them.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from .errors import Failure

log = structlog.get_logger("tranibor_observability_grafana")

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
AM = "/api/alertmanager/grafana/api/v2"


class Grafana:
    def __init__(
        self,
        base_url: str,
        token: str,
        org_id: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if org_id is not None:
            headers["X-Grafana-Org-Id"] = str(org_id)
        self._http = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=TIMEOUT, transport=transport, follow_redirects=False
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        query_errors: bool = False,
    ) -> Any:
        try:
            response = await self._http.request(method, path, params=params, json=body)
        except httpx.HTTPError as down:
            raise Failure("upstream_error", f"Grafana could not be reached ({type(down).__name__})") from None
        status = response.status_code
        if status == 400 and query_errors:
            # `/api/ds/query` answers 400 with the per-query error in `results`, which the caller reads.
            try:
                return response.json()
            except ValueError:
                pass
        if status >= 300:  # redirects are not followed (the token must not travel), so they are failures
            raise _failure(status)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise Failure("upstream_error", "Grafana answered something that is not JSON", status) from None

    # --- alerts and silences (Grafana's embedded Alertmanager) ---------------------------------------
    async def alerts(self, filters: list[str], active: bool, silenced: bool, inhibited: bool) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "silenced": str(silenced).lower(),
            "inhibited": str(inhibited).lower(),
        }
        if filters:
            params["filter"] = filters  # repeated: `filter=a&filter=b`
        return _list(await self._request("GET", f"{AM}/alerts", params=params))

    async def silences(self, filters: list[str] | None = None) -> list[dict[str, Any]]:
        params = {"filter": filters} if filters else None
        return _list(await self._request("GET", f"{AM}/silences", params=params))

    async def create_silence(self, body: dict[str, Any]) -> str:
        answer = await self._request("POST", f"{AM}/silences", body=body)
        silence_id = answer.get("silenceID") if isinstance(answer, dict) else None
        if not isinstance(silence_id, str) or not silence_id:
            raise Failure("upstream_error", "Grafana did not return a silence id")
        return silence_id

    async def expire_silence(self, silence_id: str) -> None:
        await self._request("DELETE", f"{AM}/silence/{silence_id}")

    # --- alert rules ----------------------------------------------------------------------------------
    async def alert_rule(self, uid: str) -> dict[str, Any]:
        answer = await self._request("GET", f"/api/v1/provisioning/alert-rules/{uid}")
        return answer if isinstance(answer, dict) else {}

    # --- datasources and queries ----------------------------------------------------------------------
    async def datasources(self) -> list[dict[str, Any]]:
        return _list(await self._request("GET", "/api/datasources"))

    async def query(self, body: dict[str, Any]) -> dict[str, Any]:
        answer = await self._request("POST", "/api/ds/query", body=body, query_errors=True)
        return answer if isinstance(answer, dict) else {}

    # --- dashboards -----------------------------------------------------------------------------------
    async def search_dashboards(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = {"type": "dash-db", "query": query, "limit": limit}
        return _list(await self._request("GET", "/api/search", params=params))

    async def dashboard(self, uid: str) -> dict[str, Any]:
        answer = await self._request("GET", f"/api/dashboards/uid/{uid}")
        return answer if isinstance(answer, dict) else {}

    # --- annotations ----------------------------------------------------------------------------------
    async def annotations(
        self, start_ms: int, end_ms: int, tags: list[str], limit: int, kind: str | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"from": start_ms, "to": end_ms, "limit": limit}
        if tags:
            params["tags"] = tags  # repeated: AND filtering
        if kind:
            params["type"] = kind
        return _list(await self._request("GET", "/api/annotations", params=params))

    async def add_annotation(self, body: dict[str, Any]) -> int:
        answer = await self._request("POST", "/api/annotations", body=body)
        created = answer.get("id") if isinstance(answer, dict) else None
        if not isinstance(created, int):
            raise Failure("upstream_error", "Grafana did not return an annotation id")
        return created

    async def aclose(self) -> None:
        await self._http.aclose()


def _list(answer: Any) -> list[dict[str, Any]]:
    if answer is None:
        return []
    if not isinstance(answer, list):
        raise Failure("upstream_error", "Grafana answered an object where a list was expected")
    return [item for item in answer if isinstance(item, dict)]


def _failure(status: int) -> Failure:
    if status == 401:
        return Failure(
            "unauthorized", "Grafana refused the service-account token (missing, expired or revoked)", status
        )
    if status == 403:
        return Failure("forbidden", "the service account lacks the permission this call needs", status)
    if status == 404:
        return Failure("not_found", "Grafana has no such object", status)
    if status in (409, 412):
        return Failure("conflict", "Grafana refused the change as it conflicts with the current state", status)
    if status == 429:
        return Failure("rate_limited", "Grafana is rate limiting this token", status)
    if status in (400, 422):
        return Failure("invalid_arguments", "Grafana rejected the request as malformed", status)
    return Failure("upstream_error", f"Grafana answered HTTP {status}", status)
