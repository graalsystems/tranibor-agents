# SPDX-License-Identifier: Apache-2.0
"""The former name, `choregos`, during the window of the core's ADR 0226.

The platform sends the person's token in `X-Tranibor-User-Token` from core 0.30.0, beside the former
`X-Choregos-User-Token`; a core before 0.30.0, which the plugin still supports, sends only the former. The
server reads the new header first, the former when it is absent or empty. A file or a command that still
names the former package or command keeps working.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

from tranibor_microsoft_365.app import USER_TOKEN_HEADERS, build_app
from tranibor_microsoft_365.config import Settings

from .conftest import PERSON, Mcp

SERVER = Path(__file__).resolve().parents[1]
CALL = {"name": "list_events_with", "arguments": {"within_hours": 4}}


def _sent(mcp: Mcp, headers: dict[str, str]) -> list[str]:
    result = mcp.rpc("tools/call", CALL, headers)
    assert not result.get("isError"), result["content"]
    return [request.headers["Authorization"] for request in mcp.fake.requests]


def test_the_new_header_is_the_first_read() -> None:
    assert USER_TOKEN_HEADERS == ("X-Tranibor-User-Token", "X-Choregos-User-Token")


@pytest.mark.parametrize("header", ["X-Tranibor-User-Token", "X-Choregos-User-Token", "x-choregos-user-token"])
def test_the_person_s_token_is_read_under_either_header(mcp: Mcp, header: str) -> None:
    """A core before 0.30.0 sends only the former header; the server reads Graph as the person either way."""
    assert _sent(mcp, {header: PERSON}) == [f"Bearer {PERSON}"]


def test_with_both_headers_the_new_one_wins(mcp: Mcp) -> None:
    """The 0.30.0 core sends both with the same token; were they to differ, the new one is the person."""
    assert _sent(mcp, {"X-Tranibor-User-Token": PERSON, "X-Choregos-User-Token": "someone-else"}) == [
        f"Bearer {PERSON}"
    ]
    assert _sent(mcp, {"X-Tranibor-User-Token": "", "X-Choregos-User-Token": PERSON})[-1] == f"Bearer {PERSON}"


def test_without_either_header_nothing_is_sent(mcp: Mcp) -> None:
    result = mcp.rpc("tools/call", CALL, {})
    assert result.get("isError") and "X-Tranibor-User-Token" in result["content"][0]["text"]
    assert mcp.fake.requests == []


def test_the_former_import_name_is_the_package() -> None:
    for name in [m for m in sys.modules if m == "choregos_microsoft_365" or m.startswith("choregos_microsoft_365.")]:
        del sys.modules[name]
    with pytest.warns(DeprecationWarning, match="tranibor_microsoft_365"):
        former = importlib.import_module("choregos_microsoft_365")
    assert former is importlib.import_module("tranibor_microsoft_365")
    assert importlib.import_module("choregos_microsoft_365.app").build_app is build_app
    assert importlib.import_module("choregos_microsoft_365.config").Settings is Settings


def test_both_commands_start_the_server() -> None:
    project = tomllib.loads((SERVER / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["tranibor-microsoft-365"] == scripts["choregos-microsoft-365"] == "tranibor_microsoft_365.app:main"
    assert {"src/tranibor_microsoft_365", "src/choregos_microsoft_365"} <= set(
        project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )
    assert '"/app/.venv/bin/tranibor-microsoft-365"' in (SERVER / "Dockerfile").read_text()


def test_the_entra_check_runs_under_the_former_module_name(tmp_path: Path) -> None:
    """The first procedure said `python -m choregos_microsoft_365.entra`: it still runs the check."""
    import subprocess

    reference = SERVER / "entra" / "stage-2.json"
    ran = subprocess.run(  # noqa: S603 - this interpreter, a fixed module
        [sys.executable, "-W", "ignore", "-m", "choregos_microsoft_365.entra", "--stage", "2", str(reference)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert (ran.returncode, ran.stdout.strip()) == (0, "ok"), ran.stderr
