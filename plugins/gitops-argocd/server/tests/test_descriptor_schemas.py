# SPDX-License-Identifier: Apache-2.0
"""The server descriptor carries the profile's own schemas (what `plugin validate --profiles-from` compares)."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


def test_descriptor_schemas_equal_the_profile_ones() -> None:
    profile = _load(ROOT / "gitops" / "profiles" / "gitops.yaml")
    server = _load(ROOT / "gitops-argocd" / "servers" / "gitops-argocd.yaml")
    tools = {t["name"]: t for t in profile["tools"]}
    ops = {o["name"]: o for o in server["operations"]}
    assert set(ops) == set(tools)
    for name, op in ops.items():
        for key, pkey in (("input_schema", "input"), ("output_schema", "output")):
            schema = {k: v for k, v in op[key].items() if k != "$defs"}
            expected = {k: v for k, v in tools[name][pkey].items() if k != "$defs"}
            assert schema == expected, f"{name} {key}"
