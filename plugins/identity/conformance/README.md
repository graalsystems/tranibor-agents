# tranibor-identity-conformance

The checks every `identity/identity@1` server must pass ([`../profiles/identity.yaml`](../profiles/identity.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
async def identity_target(...) -> AsyncIterator[McpTarget]:
    yield McpTarget(
        server,                       # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,   # servers/<name>.yaml
        directory="demo",             # an allow-listed directory with at least one user and one group
        foreign_directory="private",  # exists at the provider (or in its fake) but is not allow-listed
        upstream_calls=lambda: fake.call_count,   # zero for a refused call
    )

# server/tests/test_conformance.py
from tranibor_identity_conformance import Conformance

class TestIdentityProfile(Conformance):
    pass
```

The suite creates accounts named `conf-...` and puts them in an existing group; it never deletes anything (the
profile has no deletion) and ends by disabling and removing what it made. Run it on a fake or a throwaway
directory, never on a production one.

`Target` is the protocol behind `McpTarget`; implement it to run the suite over HTTP or against a live
directory (each provider's `server/tests/live/` does). The profile is found at `../profiles/identity.yaml`, or
at `TRANIBOR_IDENTITY_PROFILE`.

Install it in a server as an editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-identity-conformance"]
[tool.uv.sources]
tranibor-identity-conformance = { path = "../../identity/conformance", editable = true }
```
