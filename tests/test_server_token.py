# SPDX-License-Identifier: Apache-2.0
"""Every plugin server reads the platform's token under the name the core gives it, and an older core's.

The core gives a server's pod its bearer token in `tranibor_adapters.plugin_servers.TOKEN_ENV`
(`TRANIBOR_SERVER_TOKEN` since its rename, core ADR 0176, S75-04); a core from before the rename sets
`CHOREGOS_SERVER_TOKEN`. A server that reads one name only refuses to start on the other core: on core 0.29.0
every server of this repository did (graalsystems/tranibor-agents#34, #35). So each server's source declares
`TOKEN_ENVS = (<the core's name>, "CHOREGOS_SERVER_TOKEN")` and `INSECURE_DEV_ENVS` likewise, and reads the
token nowhere else.

Run from a core checkout, as the `templates` job of CI does (the core's `TOKEN_ENV` is then checked too):

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/tests
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
CURRENT, LEGACY = "TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"  # noqa: S105 - variable names
INSECURE = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")


def servers() -> list[Path]:
    return sorted(p.parent for p in PLUGINS.glob("*/server/pyproject.toml"))


def _constants(server: Path) -> tuple[dict[str, object], list[tuple[Path, int, str]]]:
    """The module-level tuples `TOKEN_ENVS` and `INSECURE_DEV_ENVS`, and every other place a token or
    insecure-dev variable name is written as a bare string."""
    found: dict[str, object] = {}
    stray: list[tuple[Path, int, str]] = []
    names = {CURRENT, LEGACY, *INSECURE}
    for path in sorted((server / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        declared: set[int] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("TOKEN_ENVS", "INSECURE_DEV_ENVS"):
                    found[node.targets[0].id] = ast.literal_eval(node.value)
                    declared.update(id(n) for n in ast.walk(node.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in names and id(node) not in declared:
                stray.append((path, node.lineno, str(node.value)))
    return found, stray


def test_every_server_is_found() -> None:
    assert {p.parent.name for p in servers()} >= {
        "servicenow",
        "microsoft-365",
        "microsoft-365-desk",
        "procurement-operations",
    }


@pytest.mark.parametrize("server", servers(), ids=lambda p: p.parent.name)
def test_a_server_reads_the_current_core_s_token_variable_then_an_older_core_s(server: Path) -> None:
    found, stray = _constants(server)
    assert found.get("TOKEN_ENVS") == (CURRENT, LEGACY), f"{server}: TOKEN_ENVS = ({CURRENT!r}, {LEGACY!r})"
    assert found.get("INSECURE_DEV_ENVS") == INSECURE, f"{server}: INSECURE_DEV_ENVS = {INSECURE!r}"
    assert not stray, f"a token variable read outside TOKEN_ENVS / INSECURE_DEV_ENVS: {stray}"


def test_the_current_name_is_the_core_s() -> None:
    try:
        core = importlib.import_module("tranibor_adapters.plugin_servers")
    except ImportError:
        pytest.skip("the core is not importable: run from a core checkout")
    assert core.TOKEN_ENV == CURRENT, "the core moved the variable: every server's TOKEN_ENVS follows it"
