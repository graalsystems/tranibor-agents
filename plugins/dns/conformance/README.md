# tranibor-dns-conformance

The checks every `dns/dns@1` server must pass ([`../profiles/dns.yaml`](../profiles/dns.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
async def dns_target(...) -> AsyncIterator[McpTarget]:
    yield McpTarget(
        server,                       # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,   # servers/<name>.yaml
        zone="example.com",           # an allow-listed zone holding at least one record
        foreign_zone="private.example",
        upstream_calls=lambda: fake.call_count,   # zero for a refused call
        stages_changes=True,          # True when `publish` really publishes (OVHcloud), False otherwise
    )

# server/tests/test_conformance.py
from tranibor_dns_conformance import Conformance

class TestDnsProfile(Conformance):
    pass
```

`Target` is the protocol behind `McpTarget`; implement it to run the suite over HTTP or against a live account
(each provider's `server/tests/live/` does). The profile is found at `../profiles/dns.yaml`, or at
`TRANIBOR_DNS_PROFILE`.

Install it in a server as an editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-dns-conformance"]
[tool.uv.sources]
tranibor-dns-conformance = { path = "../../dns/conformance", editable = true }
```
