# tranibor-gitops-conformance

The checks every `gitops/gitops@1` server must pass ([`../profiles/gitops.yaml`](../profiles/gitops.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
async def gitops_target(...) -> AsyncIterator[McpTarget]:
    yield McpTarget(
        server,                          # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,      # servers/<name>.yaml
        scope="shop",                    # a scope of the allow-list
        foreign_scope="platform",        # a scope that exists at the provider but is not in the allow-list
        drifted_app="shop-api",          # OutOfSync, auto-sync off, nothing running: the suite syncs it
        history_app="shop-web",          # auto-sync off, two deployments or more: the suite rolls it back and undoes it
        auto_sync_app="billing-ledger",  # auto-sync on: the suite pauses and resumes it
        foreign_app="platform-dns",      # an application of foreign_scope
        upstream_writes=lambda: fake.writes,   # requests that changed something: zero for a refused call
        running_app="shop-jobs",         # optional: an operation is running
        denied_app="billing-reports",    # optional: a window forbids a sync now
    )

# server/tests/test_conformance.py
from tranibor_gitops_conformance import Conformance

class TestGitopsProfile(Conformance):
    pass
```

`Target` is the protocol behind `McpTarget`; implement it to run the suite over HTTP or against a live controller
(each provider's `server/tests/live/` does, on throwaway applications). The profile is found at
`../profiles/gitops.yaml`, or at `TRANIBOR_GITOPS_PROFILE`.

Install it in a server as an editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-gitops-conformance"]
[tool.uv.sources]
tranibor-gitops-conformance = { path = "../../gitops/conformance", editable = true }
```
