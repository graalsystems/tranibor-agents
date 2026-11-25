---
id: connector.mcp.grafana
title: Connect a Grafana stack for alert triage
applies_to: {connector: mcp/grafana}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "Alerting provisioning HTTP API (alert rules, contact points)", url: "https://grafana.com/docs/grafana/latest/developers/http_api/alerting_provisioning/"}
  - {title: "Annotations HTTP API", url: "https://grafana.com/docs/grafana/latest/developers/http_api/annotations/"}
  - {title: "Data source HTTP API (list, and query through /api/ds/query)", url: "https://grafana.com/docs/grafana/latest/developers/http_api/data_source/"}
  - {title: "Dashboard HTTP API", url: "https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/"}
  - {title: "Webhook contact point", url: "https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/webhook-notifier/"}
  - {title: "Alerting permission checks (source of the silence and alert routes' rights)", url: "https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/authorization.go"}
egress: ["<your Grafana host>"]
fields:
  base_url: "https://grafana.example.com, without a trailing slash (https is required)"
  token: "the service account's token, typed once"
  org_id: "the organization id, only if the service account is not in the organization you want to read"
  datasources: "optional: the uids of the Prometheus, Mimir and Loki datasources the agent may query"
  max_range_hours: "the widest window of one metric or log query (default 6, at most 24)"
  max_lines: "the most log lines one query returns (default 200, at most 1000)"
  max_silence_minutes: "the longest silence the plugin may create (default 120, at most 1440)"
---

## Before you start

- A Grafana (OSS, Enterprise or Cloud) you administer, with Grafana-managed alerting, a Prometheus or Mimir
  datasource and, for logs, a Loki datasource.
- The `alert-triage` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- A project from the template `alert-triage`, and a person of the `on-call` group to review its work items.
- **What this procedure has not been run against.** The plugin was written from Grafana's documentation
  and checked on a seeded fake, not on a live stack. Its live suite (`server/tests/live`) is skipped until
  you set `TRANIBOR_LIVE_GRAFANA_URL` and `TRANIBOR_LIVE_GRAFANA_TOKEN`: run it against a test stack
  before you trust the plugin in production. See *What is not confirmed*.

## At Grafana

1. **The service account.** Administration, Users and access, **Service accounts**, **Add service
   account**: name `tranibor-alert-triage`, role **Viewer**. Viewer reads alert rules, alerts, silences,
   datasources, dashboards and annotations, and may run queries.
2. **Writes, only if you want them.** The plugin creates silences and adds annotations. Viewer cannot.
   Either give the service account the **Editor** role, or (Grafana Enterprise and Cloud, custom roles)
   a custom role with exactly: `alert.instances:read`, `alert.instances:create`, `alert.instances:write`
   (silences), and `annotations:create` + `annotations:read` scoped to organization annotations. Without
   them the server returns `forbidden` on those three tools and everything else keeps working. The rights
   of the silence routes come from Grafana's own permission checks (the `authorization.go` page above),
   not from a documentation page.
3. **The token.** On the service account, **Add service account token**; set an expiry (90 days or less)
   and copy it once. It is typed in Tranibor below and sealed there.
4. **The organization.** If the service account lives in another organization than the one you want to
   read, note its numeric id (sent as `X-Grafana-Org-Id`).
5. **The webhook contact point.** Alerting, Contact points, **Add contact point**, integration
   **Webhook**: URL = the trigger URL of *In Tranibor* step 3, HTTP method `POST`, Authorization header
   scheme `Bearer` and credentials = the trigger's token. Then point a notification policy at it (for
   instance for `severity=~"critical|warning"`), grouped by `alertname` so one work item covers one alert.
   Set **Max alerts** to a small number; the trigger reads the first alert and the group's common labels.

## Permissions

| Tool | Grafana route | Needs |
|:--|:--|:--|
| list_firing_alerts, list_silences | `GET /api/alertmanager/grafana/api/v2/alerts`, `/silences` | Viewer (`alert.instances:read`) |
| get_alert_rule | `GET /api/v1/provisioning/alert-rules/{uid}` | Viewer (`alert.rules:read`) |
| list_datasources, query_metrics, query_logs | `GET /api/datasources`, `POST /api/ds/query` | Viewer (`datasources:query`) |
| list_dashboards, get_dashboard_panels | `GET /api/search`, `/api/dashboards/uid/{uid}` | Viewer (`dashboards:read`) |
| list_annotations | `GET /api/annotations` | Viewer (`annotations:read`) |
| create_silence, expire_silence | `POST /api/alertmanager/grafana/api/v2/silences`, `DELETE …/silence/{id}` | Editor (`alert.instances:create`, `:write`) |
| add_annotation | `POST /api/annotations` | Editor (`annotations:create`) |

The server never creates, edits or deletes a rule, a contact point, a dashboard or a datasource, and it
expires only silences it created itself (`createdBy` `tranibor-alert-triage`).

### What is not confirmed

Everything below is in a code comment of `server/src/tranibor_alert_triage/client.py` too.

- **`/api/ds/query` bodies and frames.** The route and its `queries[]` shape (`datasource.uid`, `refId`,
  `from`/`to`, `expr`, `intervalMs`, `maxDataPoints`) come from the data source API page; the frame layout
  the server reads for Prometheus (time and value columns, a `__name__`-less label set) and Loki (time and
  line columns) is from the documented response shape and Grafana's source, and was not checked on a live
  stack. If a datasource answers a different layout, the metric and log tools return empty series: the
  live suite fails on it.
- **Alerts, silences and their routes** (`/api/alertmanager/grafana/api/v2/alerts`, `/silences`,
  `/silence/{id}`) follow the Alertmanager v2 API, which Grafana implements; no narrative page of the
  docs describes them (the one we tried answers 404), the route and its rights are read from Grafana's
  source (`authorization.go`).
- **The alert rule route** (`/api/v1/provisioning/alert-rules/{uid}`) is documented, and marked deprecated
  in favour of the App Platform `rules.alerting.grafana.app` API. A future Grafana may remove it.
- **The dashboard route** (`/api/dashboards/uid/{uid}`) is the legacy one; the current docs describe the
  newer `/apis` route. The legacy route is in Grafana's merged OpenAPI document.
- **The rule uid of an alert.** The server reads it from what the agent gives; Grafana puts the rule uid in
  the generator URL (`/alerting/grafana/<uid>/view`). Whether the `__alert_rule_uid__` label is present on
  every alert was not confirmed.
- **The narrower custom role** of step 2 is inferred from the permissions in Grafana's source, not from a
  documentation page. If a write fails with `forbidden`, use Editor.

## In Tranibor

1. Catalogue, **Plugins**, `alert-triage`, **Configure**: type the fields above. `base_url` is the only
   host the server may reach. Set `datasources` to the uids it may query if you want a narrower list than
   "every Prometheus-type and Loki datasource the token sees".
2. Create a project from the template `alert-triage` (workflow `alert-triage`, internal tracker).
3. **The trigger** (a template cannot ship one; ADR 0113 of the core). In the project's configuration:

   ```yaml
   triggers:
     - name: grafana-alert
       title: "{/commonLabels/alertname}: {/commonAnnotations/summary}"
       body: "Triage the firing alert {/commonLabels/alertname} (severity {/commonLabels/severity}). Started at {/alerts/0/startsAt}. Rule: {/alerts/0/generatorURL}"
       key: /groupKey
       fields:
         alertname: /commonLabels/alertname
         severity: /commonLabels/severity
         starts_at: /alerts/0/startsAt
       when: { /status: ["firing"] }
       workflow: alert-triage
   ```

   `when` ignores `resolved` notifications; `key` gives one work item per Grafana group while it runs.
   Mint the trigger's token (**Settings**, the project's triggers; shown once) and give Grafana the URL
   `https://<your tranibor>/api/v1/webhooks/triggers/<org>/<project>/grafana-alert`.
4. Fire it once by hand with a payload you type (the console's *fire* box) before Grafana sends one.

## Test it

1. In Grafana, on the contact point, **Test**: a work item opens and the triager runs.
2. The report cites the rule, a metric value with its time, and the annotations. Read it: a claim without a
   number or a time behind it is a defect, report it.
3. Reviewing a `silence` outcome opens an approval for a second person. Approve a short silence on a test
   alert; check it in Grafana (Alerting, Silences) under `createdBy` `tranibor-alert-triage`, then expire it there (or let it end).

## When it fails

- **`unauthorized`**: the token is wrong or expired. Issue another and type it again.
- **`forbidden` on `create_silence`, `expire_silence` or `add_annotation`**: the service account is a
  Viewer. See step 2.
- **`not_found` on a rule or dashboard**: the uid is from another organization; set `org_id`.
- **A query is refused as outside the allow-list**: add the datasource's uid to `datasources`, or empty it.
- **A silence is refused**: its matchers must be exact, include `alertname`, match at least one firing
  alert and at most 25, and last no longer than `max_silence_minutes`. Settle it again in the review form.
- **No work item opens**: check the trigger's token, that the notification policy reaches the contact
  point, and the project's *Received events* card, which records each delivery and why it was ignored.
