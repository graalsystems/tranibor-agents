# tranibor-observability-conformance

The checks every `observability/observability@1` server must pass ([`../profiles/observability.yaml`](../profiles/observability.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
async def observability_target(...) -> AsyncIterator[McpTarget]:
    yield McpTarget(
        server,                          # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,      # servers/<name>.yaml
        alert_labels={"alertname": "HighErrorRate", "service": "checkout"},  # exact labels of a firing alert
        rule_id="rule-high-error-rate",  # that alert's rule
        metrics_source="prom-main",
        logs_source="loki-main",
        metrics_expr="up", logs_expr='{service="checkout"}',
        start="2026-11-25T08:00:00Z", end="2026-11-25T09:00:00Z",
        foreign_source="prom-staging",   # exists upstream, outside the allow-list
        upstream_calls=lambda: len(fake.requests),   # unchanged for a refused call
        foreign_silence_id="sil-human",  # a silence the server did not create
    )

# server/tests/test_conformance.py
from tranibor_observability_conformance import Conformance

class TestObservabilityProfile(Conformance):
    pass
```

`Target` is the protocol behind `McpTarget`; implement it to run the suite over HTTP or against a live stack
(each provider's `server/tests/live/` does). The profile is found at `../profiles/observability.yaml`, or at
`TRANIBOR_OBSERVABILITY_PROFILE`.

Install it in a server as an editable path dev-dependency (`[tool.uv.sources]`) of its `pyproject.toml`; the
image installs no dev group.
