#!/usr/bin/env python3
"""Print one version's section of docs/changelog.md, for a GitHub release body.

    python3 scripts/release_notes.py 1.0.0 > release-notes.md

Shared by draft-release.yml and tag-on-release-merge.yml: two copies of a parser diverge
silently, because a wrong release body still publishes. Exits non-zero on a missing section
rather than emitting a placeholder.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).parent.parent / "docs" / "changelog.md"


def section(version: str, text: str) -> str | None:
    version = version.lstrip("v")
    match = re.search(rf"(## v{re.escape(version)}\b.*?)(?=\n## |\Z)", text, re.DOTALL)
    return match.group(1).strip() if match else None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_notes.py VERSION", file=sys.stderr)
        return 2

    found = section(sys.argv[1], CHANGELOG.read_text())
    if not found:
        print(f"no '## v{sys.argv[1].lstrip('v')}' section in {CHANGELOG}", file=sys.stderr)
        return 1

    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
