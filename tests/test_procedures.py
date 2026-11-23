# SPDX-License-Identifier: Apache-2.0
"""Every plugin server is documented by a procedure the platform can show (ADR 0067 of the core).

A plugin that ships a server (`servers/*.yaml`) ships the procedure of its connector: a file of the plugin's
`contents.procedures` whose `applies_to.connector` is `mcp/<plugin>` or `mcp/<server>`, whose front matter
`fields` are fields of the server's `config_schema`, and which describes every `secret_fields` entry. The
console shows that procedure when the server is configured, and the core's documentation site publishes it, so
a field it names that the form does not have, or a secret it leaves out, is a wrong page in front of an
administrator. Each procedure also goes through the core's own parser when the core is importable.

Run from a core checkout, as the `templates` job of CI does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/tests
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def manifest(plugin: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((plugin / "plugin.yaml").read_text())
    assert isinstance(loaded, dict), plugin
    return loaded


def all_plugins() -> list[Path]:
    return sorted(p for p in PLUGINS.iterdir() if (p / "plugin.yaml").is_file())


def plugins_with_servers() -> list[Path]:
    return [p for p in all_plugins() if sorted((p / "servers").glob("*.yaml"))]


def plugins_with_procedures() -> list[Path]:
    return [p for p in all_plugins() if (manifest(p).get("contents") or {}).get("procedures")]


def procedure_files(plugin: Path) -> list[Path]:
    """The files `contents.procedures` declares (a pattern or a path), as the packer reads them."""
    files: list[Path] = []
    for pattern in (manifest(plugin).get("contents") or {}).get("procedures") or []:
        if "*" in pattern:
            files.extend(sorted(plugin.glob(pattern)))
        elif (plugin / pattern).is_file():
            files.append(plugin / pattern)
    return list(dict.fromkeys(files))


def front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: no front matter"
    end = text.index("\n---\n", 4)
    loaded = yaml.safe_load(text[4:end])
    assert isinstance(loaded, dict), path
    return loaded


def servers(plugin: Path) -> list[dict[str, Any]]:
    loaded = [yaml.safe_load(p.read_text()) for p in sorted((plugin / "servers").glob("*.yaml"))]
    assert all(isinstance(s, dict) for s in loaded), plugin
    return loaded


def the_procedure(plugin: Path, server: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    targets = {f"mcp/{plugin.name}", f"mcp/{server['name']}"}
    found = [
        (path, front)
        for path in procedure_files(plugin)
        if (front := front_matter(path)).get("applies_to", {}).get("connector") in targets
    ]
    assert found, (
        f"{plugin.name}: the server `{server['name']}` has no procedure in the plugin's "
        f"`contents.procedures` whose applies_to.connector is one of {sorted(targets)}"
    )
    assert len(found) == 1, (
        f"{plugin.name}: several procedures apply to {sorted(targets)}: {[p for p, _ in found]}"
    )
    return found[0]


SERVER_CASES = [
    pytest.param(plugin, server, id=f"{plugin.name}/{server['name']}")
    for plugin in plugins_with_servers()
    for server in servers(plugin)
]


def test_there_are_servers_to_check() -> None:
    """The test checks something: a layout change that hid every server would turn it green on nothing."""
    assert SERVER_CASES
    assert plugins_with_procedures()


@pytest.mark.parametrize(("plugin", "server"), SERVER_CASES)
def test_every_server_has_its_procedure(plugin: Path, server: dict[str, Any]) -> None:
    the_procedure(plugin, server)


@pytest.mark.parametrize(("plugin", "server"), SERVER_CASES)
def test_the_procedures_fields_are_the_servers_configuration(plugin: Path, server: dict[str, Any]) -> None:
    path, front = the_procedure(plugin, server)
    properties = set((server.get("config_schema") or {}).get("properties") or {})
    fields = set(front.get("fields") or {})
    unknown = sorted(fields - properties)
    assert not unknown, f"{path.name}: fields {unknown} are not in `{server['name']}`'s config_schema"
    secrets = server.get("secret_fields") or []
    assert set(secrets) <= properties, f"{server['name']}: secret_fields outside config_schema"
    undescribed = sorted(set(secrets) - fields)
    assert not undescribed, f"{path.name}: the secret fields {undescribed} are not described in `fields`"


@pytest.mark.parametrize("plugin", plugins_with_procedures(), ids=lambda p: p.name)
def test_a_plugin_that_lists_procedures_ships_them(plugin: Path) -> None:
    assert procedure_files(plugin), f"{plugin.name}: `contents.procedures` matches no file"


@pytest.mark.parametrize(
    "path",
    [path for plugin in plugins_with_procedures() for path in procedure_files(plugin)],
    ids=lambda p: f"{p.parents[1].name}/{p.name}",
)
def test_each_procedure_parses_with_the_cores_parser(path: Path) -> None:
    if importlib.util.find_spec("tranibor_core") is None:
        pytest.skip("the core is not importable: run from a core checkout (`uv run` there) to parse with it")
    from tranibor_core.procedures import parse

    procedure = parse(path.read_text(), where=str(path))
    assert procedure.id == path.stem, f"{path.name}: its id is {procedure.id}"
