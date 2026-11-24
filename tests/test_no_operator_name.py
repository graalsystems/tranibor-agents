# SPDX-License-Identifier: Apache-2.0
"""The agents name no operator.

The plugins are installed by any operator of Tranibor. A record of where something was measured says
"an operator's dev cluster", never whose: read by anyone else, an operator's name, host or private
repository says the plugins are that operator's. The core holds the same rule (its ADR 0157).
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
#: An operator's names and the internal hosts and paths that would give one away.
OPERATOR = re.compile(r"diametral|insights360|treydone|harbor\.build\.|layer4", re.IGNORECASE)
SELF = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()


def test_no_tracked_file_names_an_operator() -> None:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    offenders = []
    for name in filter(None, listed.decode().split("\0")):
        path = ROOT / name
        if name == SELF or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        offenders += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if OPERATOR.search(line)]
    assert not offenders, f"an operator is named: {offenders}"
