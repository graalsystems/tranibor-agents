# tranibor-lan-conformance

The checks every `lan/lan@1` server must pass ([`../profiles/lan.yaml`](../profiles/lan.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
def lan_target(server, fake) -> McpTarget:
    return McpTarget(
        server,  # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,  # servers/<name>.yaml
        site_id=...,
        device_id=...,
        offline_device_id=...,
        restart_device_id=...,
        poe_device_id=...,
        poe_port=...,
        client_id=...,
        missing_id=...,
        upstream_calls=lambda: len(fake.calls),  # must not move for a `not_supported` call
    )


# server/tests/test_conformance.py
from tranibor_lan_conformance import Conformance


class TestLanProfile(Conformance):
    pass
```

A provider that cannot serve a write lists it in `get_provider.unsupported_tools`; the suite then requires that
tool to answer `not_supported` with nothing sent upstream, and skips its effect checks.

The profile is found at `../profiles/lan.yaml`, or at `TRANIBOR_LAN_PROFILE`. Install it in a server as an
editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-lan-conformance"]
[tool.uv.sources]
tranibor-lan-conformance = { path = "../../lan/conformance", editable = true }
```
