#!/usr/bin/env python3
"""Stamp the Unreleased section with a version and date.

    python3 scripts/changelog_release.py 1.0.0

Writes no prose: entries are already there, written per-commit and enforced by
changelog_check.py. Refuses an empty Unreleased section, because a release whose notes are a
bare heading is the failure this mechanism exists to prevent.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).parent.parent / "docs" / "changelog.md"
UNRELEASED = "## Unreleased"


def stamp(text: str, version: str, today: str) -> str:
    start = text.find(f"{UNRELEASED}\n")
    if start == -1:
        raise SystemExit(f"no '{UNRELEASED}' section in {CHANGELOG}")

    end = text.find("\n## ", start + 1)
    body = text[start + len(UNRELEASED) : (end if end != -1 else len(text))]
    if not re.search(r"^\s*-\s+\S", body, re.M):
        raise SystemExit(f"'{UNRELEASED}' has no entries; nothing to release")

    return text[:start] + f"{UNRELEASED}\n\n## v{version} - {today}" + text[start + len(UNRELEASED) :]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: changelog_release.py VERSION", file=sys.stderr)
        return 2

    version = sys.argv[1].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"'{version}' is not a semver release", file=sys.stderr)
        return 2

    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    _ = CHANGELOG.write_text(stamp(CHANGELOG.read_text(), version, today))
    print(f"changelog: v{version} - {today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
