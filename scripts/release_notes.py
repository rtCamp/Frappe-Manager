#!/usr/bin/env python3
"""Extract one version's section out of docs/changelog.md for GitHub release notes.

Exits non-zero when the section is missing. The inline version this replaced fell
back to printing ``Release <version>``, so a missing section produced an
empty-looking release with nothing in the logs to explain it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_CHANGELOG = Path("docs/changelog.md")


def extract_section(changelog: str, version: str) -> str | None:
    """Return the ``## v<version>`` section, or None when it is absent.

    The ``(?![0-9.])`` matters: without it, ``0.19.1`` also matches a
    ``## v0.19.10`` heading and would ship the wrong notes.
    """
    pattern = rf"^## v{re.escape(version)}(?![0-9.]).*?(?=^## v|\Z)"
    match = re.search(pattern, changelog, re.DOTALL | re.MULTILINE)
    return match.group(0).strip() if match else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="release version without the leading v, e.g. 0.19.1")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
        help=f"path to the changelog (default: {DEFAULT_CHANGELOG})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the section here instead of stdout",
    )
    args = parser.parse_args(argv)

    version = args.version.lstrip("v")

    if not args.changelog.is_file():
        print(f"::error::changelog not found at {args.changelog}", file=sys.stderr)
        return 1

    section = extract_section(args.changelog.read_text(encoding="utf-8"), version)

    if section is None:
        headings = re.findall(r"^## (v\S+)", args.changelog.read_text(encoding="utf-8"), re.MULTILINE)
        print(
            f"::error::no '## v{version}' section in {args.changelog}. "
            f"Found: {', '.join(headings[:10]) or 'none'}. "
            "Run the Prepare Release workflow so git-cliff generates it before tagging.",
            file=sys.stderr,
        )
        return 1

    if args.output:
        args.output.write_text(section + "\n", encoding="utf-8")
        print(f"wrote {len(section.splitlines())} lines to {args.output}")
    else:
        print(section)

    return 0


if __name__ == "__main__":
    sys.exit(main())
