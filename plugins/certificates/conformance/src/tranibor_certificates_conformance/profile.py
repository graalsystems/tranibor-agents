# SPDX-License-Identifier: Apache-2.0
"""Loads the profile the suite checks against: the plugin's own `profiles/certificates.yaml`."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROFILE_ID = "certificates/certificates@1"
#: Overrides the location (for a checkout laid out differently, or a profile under review).
ENV = "TRANIBOR_CERTIFICATES_PROFILE"


def profile_path() -> Path:
    if os.environ.get(ENV):
        return Path(os.environ[ENV])
    # <plugin>/conformance/src/tranibor_certificates_conformance/profile.py -> <plugin>/profiles/certificates.yaml
    return Path(__file__).resolve().parents[3] / "profiles" / "certificates.yaml"


@cache
def load_profile() -> dict[str, Any]:
    path = profile_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"the certificates profile is not at {path}: install this package from the repository's "
            f"plugins/certificates/conformance (editable), or set {ENV}"
        )
    loaded = yaml.safe_load(path.read_text())
    assert isinstance(loaded, dict) and loaded["name"] == "certificates" and loaded["version"] == 1
    return loaded


def tools() -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in load_profile()["tools"]}


def output_schema(tool: str) -> dict[str, Any]:
    return {**tools()[tool]["output"], "$defs": load_profile().get("$defs", {})}
