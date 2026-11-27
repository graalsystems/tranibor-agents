# tranibor-certificates-conformance

The checks every `certificates/certificates@1` server must pass ([`../profiles/certificates.yaml`](../profiles/certificates.yaml)).
The profile is read-only, so the suite writes nothing.

```python
# server/tests/conftest.py
@pytest.fixture
def certificates_target(server, fake) -> McpTarget:
    return McpTarget(
        server,  # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,  # servers/<name>.yaml
        foreign_id="other.example.org:443",  # an id outside what the instance may look at
        unknown_id="absent.example.com:443",  # an id inside it that names nothing
        upstream_calls=lambda: fake.calls,  # must not move on a refused foreign id
        refusal_is_local=True,  # False for a source with no allow-list on ids (a log)
        window_days=30,  # a window holding some of the fake's certificates, not all
    )


# server/tests/test_conformance.py
from tranibor_certificates_conformance import Conformance


class TestCertificatesProfile(Conformance):
    pass
```

The fake must hold: a certificate expiring inside the window, one outside it, a healthy one, and something
wrong (`list_findings` must not be empty). `Target` is the protocol behind `McpTarget`; implement it to run
the suite over HTTP or against a live source (each provider's `server/tests/live/` does). The profile is found
at `../profiles/certificates.yaml`, or at `TRANIBOR_CERTIFICATES_PROFILE`.

Install it in a server as an editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-certificates-conformance"]
[tool.uv.sources]
tranibor-certificates-conformance = { path = "../../certificates/conformance", editable = true }
```
