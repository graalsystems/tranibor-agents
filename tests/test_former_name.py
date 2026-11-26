# SPDX-License-Identifier: Apache-2.0
"""The former product name, `choregos`, appears only where a rule below says why.

The core's ADR 0226 retires the former name from every layer: each name gets its `tranibor` twin, the core
reads both for a window of two minor versions (0.30.x and 0.31.x), writes the new one, and drops the former
in 0.32.0. A plugin here must keep working on every core its `metadata.core` admits, so the former name
stays where a supported core reads nothing else, where a server reads a twin, where a customer's system
holds it (stored state: never renamed in place), where no core has renamed it yet, and in history. Every
other occurrence is a bug: this test fails on it. Each rule must still be used: a twin removed from the
code is removed from this list in the same change (CONTRIBUTING.md, "The former name").

Runs anywhere `git` does (no core needed); CI's `templates` job runs it with the repository's other tests.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import subprocess
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELF = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()
FORMER = re.compile(r"choregos", re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    paths: tuple[str, ...]
    pattern: str
    why: str


SERVERS = ("plugins/servicenow/*", "plugins/microsoft-365/*", "plugins/microsoft-365-desk/*")

RULES = (
    # ─── whole files: the window's own code and tests ───
    Rule(
        ("plugins/*/server/src/choregos_*/__init__.py",),
        r".*",
        "the former import name of a server's package, an alias for the window",
    ),
    Rule(
        ("plugins/*/server/tests/test_former_name.py",),
        r".*",
        "each server's tests of what it still reads under the former name",
    ),
    # ─── what a supported core reads under the former name only ───
    Rule(
        ("plugins/*.yaml", "README.md", "CONTRIBUTING.md", "scripts/*"),
        r"apiVersion\W+choregos/v1",
        "a manifest's and the index's apiVersion: a core before 0.30.0 reads no other",
    ),
    Rule(
        ("scripts/publish.py", "CONTRIBUTING.md"),
        r"application/vnd\.choregos\.plugin(-index)?\.v1",
        "the OCI media types: no core reads another, 0.30.0 included",
    ),
    Rule(
        ("plugins/investment-research/*", "CONTRIBUTING.md"),
        r"choregos-quant",
        "the job command of the investment-research image: a core before 0.30.0 has no other name for it",
    ),
    Rule(
        ("plugins/servicenow/README.md", "plugins/servicenow/evals/matrix/*"),
        r"choregos_tools",
        "the generated tool client of a run's scripts: no core has renamed it",
    ),
    Rule(
        (
            "plugins/*/server/src/*/config.py",
            "plugins/*/server/tests/*",
            "plugins/*/README.md",
            "tests/test_server_token.py",
            "CONTRIBUTING.md",
        ),
        r"CHOREGOS_(SERVER_TOKEN|SERVER_INSECURE_DEV|\*|\")|\"CHOREGOS_\"",
        "the platform's token under a core's former variable name (core ADR 0176, R4), and the prefix that "
        "`tranibor plugin validate` refuses in a fake's variables",
    ),
    Rule(
        (
            "plugins/investment-research/templates/investment-research/README.md",
            "plugins/investment-research/templates/investment-research/workflows/campaign.yaml",
        ),
        r"`seed` stays `choregos`|seed: \{ type: string, default: choregos,",
        "the default anonymisation seed is an input to a hash: the template keeps it (the README says why)",
    ),
    # ─── the run directory a core before 0.30.0 lays down ───
    Rule(
        (
            "plugins/software-delivery/agents/*",
            "plugins/software-delivery/evals/*",
            "plugins/software-delivery/README.md",
            "plugins/software-delivery/plugin.yaml",
            "plugins/requests/*",
            "plugins/procurement-operations/agents/*",
            "plugins/procurement-operations/README.md",
        ),
        r"\.choregos/",
        "the run directory of a core before 0.30.0, which lays the knowledge bases there (the agents name both)",
    ),
    # ─── the window's twins, read by the servers ───
    Rule(
        (*SERVERS, "CONTRIBUTING.md"),
        r"[Xx]-[Cc]horegos-(Secret|User-Token|\*)|x-choregos-(secret|user-token)",
        "a header under its former name: a core before 0.30.0 sends it, a Business Rule set up before sends it",
    ),
    Rule(
        ("plugins/servicenow/*", "plugins/microsoft-365-desk/*"),
        r"\[choregos(-status)?:|choregos(-status)?:|choregos-(status|action)\b|\bChoregos:|\(\?:tranibor\|choregos\)"
        r"|FORMER_(PROPERTY_)?NAME = \"tranibor\", \"choregos\"|\"choregos\"\)|\(\"choregos:\", \"tranibor:\"\)"
        r"|\(\"tranibor:\", \"choregos:\"\)|\"Choregos:\"",
        "what an earlier server wrote on a customer's system (a marker, a correlation id, a category, an "
        "extended property) and the marker or label a core hands under the former name: found, never rewritten",
    ),
    Rule(
        (*SERVERS, "CONTRIBUTING.md"),
        r"`choregos`|choregos_(servicenow|microsoft_365|m365_desk)|choregos-(servicenow|microsoft-365|m365-desk)\b",
        "the former name said as such; the former package and command of a server, aliases for the window; the "
        "Entra application an earlier procedure registered as `choregos-microsoft-365` (the customer's name)",
    ),
    # ─── stored state on a customer's system, created from an earlier procedure ───
    Rule(
        ("plugins/servicenow/README.md", "plugins/servicenow/procedures/*"),
        r"choregos(\.integration|_integration|\.webhook_url|\.webhook_secret|\.\*)",
        "the integration user, role and system properties an instance set up before 0.2.22 holds",
    ),
    # ─── history ───
    Rule(
        ("plugins/software-delivery/skills/madr-4/PROVENANCE.json",),
        r"choregos_core/catalogue_d_agents",
        "the core's path the skill was copied from, at the commit it names",
    ),
)


def tracked() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in listed.decode().split("\0") if name and name != SELF and (ROOT / name).is_file()]


def _rules_for(name: str) -> list[tuple[int, Rule]]:
    return [(i, r) for i, r in enumerate(RULES) if any(fnmatch.fnmatchcase(name, glob) for glob in r.paths)]


def test_the_former_name_appears_only_where_a_rule_says_why() -> None:
    offenders: list[str] = []
    used: set[int] = set()
    for name in tracked():
        if FORMER.search(name) and not any(fnmatch.fnmatchcase(name, g) for g in RULES[0].paths):
            offenders.append(f"{name}: the file's name")
        text = (ROOT / name).read_bytes().decode("utf-8", errors="ignore")
        if not FORMER.search(text):
            continue
        rules = _rules_for(name)
        for number, line in enumerate(text.splitlines(), 1):
            if not FORMER.search(line):
                continue
            rest = line
            for index, rule in rules:
                stripped = re.sub(rule.pattern, "", rest)
                if stripped != rest:
                    used.add(index)
                rest = stripped
            if FORMER.search(rest):
                offenders.append(f"{name}:{number}: {line.strip()[:120]}")
    assert not offenders, "the former name outside the allowlist:\n" + "\n".join(offenders)
    stale = [f"{r.paths}: {r.pattern}" for i, r in enumerate(RULES) if i not in used]
    assert not stale, "rules no line uses any more (remove them):\n" + "\n".join(stale)


def test_a_new_occurrence_is_caught() -> None:
    """The guard bites: a line that names the former product outside any rule is an offender."""
    line = "Install the choregos plugin."
    rest = line
    for _, rule in _rules_for("plugins/servicenow/README.md"):
        rest = re.sub(rule.pattern, "", rest)
    assert FORMER.search(rest)
