# SPDX-License-Identifier: Apache-2.0
"""The plugin brings no server, so a deployment that runs no plugin server can install it.

Core finding graalsystems/tranibor#2134: `procurement` 0.2.1 shipped the MCP server `purchasing`, and a core
deployment that runs no plugin servers (the local `docker compose` install; plugin servers only run on
Kubernetes) refuses to install any plugin that brings a server. The comparison of offers needs no server, but it
shipped in the same plugin, so the local install could never offer it. 0.3.0 moved the server and everything
that needs it to `procurement-operations`. These tests hold the split: a server, an assistant, a procedure or
a matrix case coming back here turns this repository's CI red. They run from a core checkout, as the
`templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/procurement/tests
"""

from __future__ import annotations

from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[1]


def test_the_plugin_loads_with_no_server() -> None:
    from tranibor_core.plugins import load_directory

    plugin = load_directory(PLUGIN)
    assert plugin.version == "0.3.0"
    assert plugin.servers == {}, "a plugin that brings a server cannot be installed where no plugin server runs"


def test_the_manifest_declares_nothing_that_needs_a_server() -> None:
    contents = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["contents"]
    for key in ("servers", "assistants", "procedures", "matrix_cases"):
        assert not contents.get(key), key
    assert sorted(contents["templates"]) == ["templates/procurement-sourcing"]
    assert not (PLUGIN / "server").exists() and not (PLUGIN / "servers").exists()


def test_the_comparison_of_offers_needs_no_organisation_connector() -> None:
    manifest = yaml.safe_load(
        (PLUGIN / "templates" / "procurement-sourcing" / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert "org_connectors" not in manifest["requires"], "offers must compare without purchasing"
