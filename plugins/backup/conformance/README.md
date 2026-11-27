# tranibor-backup-conformance

The checks every `backup/backup@1` server must pass ([`../profiles/backup.yaml`](../profiles/backup.yaml)).

```python
# server/tests/conftest.py
@pytest.fixture
async def backup_target(...) -> AsyncIterator[McpTarget]:
    yield McpTarget(
        server,                          # the MCP server under test (in-memory client)
        descriptor_path=DESCRIPTOR,      # servers/<name>.yaml
        schedule="daily-apps",           # a schedule the suite may trigger
        backup="daily-apps-20261125",    # a restorable backup...
        source="shop",                   # ...and a part of it the suite may restore
        target_prefix="restore-test-",   # what the server's allowed rule makes every target start with
        unrestorable_backup="hourly-db-20261125",   # optional: a failed backup, which must not be restored
        upstream_calls=lambda: fake.call_count,     # zero movement for a refused call
    )

# server/tests/test_conformance.py
from tranibor_backup_conformance import Conformance

class TestBackupProfile(Conformance):
    pass
```

The suite triggers one backup and restores into the isolated target, then cleans up; it writes nowhere else.
`Target` is the protocol behind `McpTarget`; implement it to run the suite over HTTP or against a live
environment (each provider's `server/tests/live/` does). The profile is found at `../profiles/backup.yaml`, or
at `TRANIBOR_BACKUP_PROFILE`.

Install it in a server as an editable path dev-dependency:

```toml
[dependency-groups]
dev = ["tranibor-backup-conformance"]
[tool.uv.sources]
tranibor-backup-conformance = { path = "../../backup/conformance", editable = true }
```
