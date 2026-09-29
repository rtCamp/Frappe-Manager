#!/usr/bin/env python3
"""Refuse a commit that ships user-visible behavior with no changelog entry.

Run via `just changelog-check`; CI runs it next to `docs-lint`.

A commit passes if it is internal by rule, carries a ``Changelog: skip`` trailer, or touches
RECORD_PATHS. That last rule is what makes thematic entries work: the commit that starts a
theme writes the entry and its follow-ups edit it, which is the same signal. Keep the
internal-by-rule set generous -- a gate that cries wolf gets bypassed, and a bypassed gate
reports clean.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Where an entry may be recorded. Add "changelog.d/" here when fragments arrive.
RECORD_PATHS = ("docs/changelog.md",)

# Commits before this predate the gate and cannot be given trailers without rewriting
# published history; their entries were written by hand in 60efd717.
BASELINE = "60efd717"

# Commits whose entry was written after the fact, in one later pass over docs/changelog.md.
# The three normal verdicts are all properties of the commit OBJECT, so a commit that shipped
# without an entry can only be cured by rewriting it; naming it here records the entry instead.
# This is not a bypass: each sha is a claim that an entry covering it exists, reviewable in the
# same diff that adds the sha. Prefer moving BASELINE only for a whole prehistory, never for a
# commit someone could still have written an entry for.
COVERED = {
    "aa4aed80",  # redis: move a bench between external and fm-managed redis
    "679740ba",  # redis: cache and queue on different servers
    "7ca67438",  # redis: drain the queue before moving it
    "849fe2b6",  # redis: backlog wait timeout + resume producers on Ctrl-C
    "858aac22",  # redis: unbounded backlog wait
    "3f36999a",  # workers: scoped signal handling
    "4daf87fc",  # workers: pause the scheduler explicitly
    "9aafa139",  # migrate: squash the unreleased 0.20/0.21 migrations into 1.0.0
    "23d71ab3",  # migrate: --rerun version floor underflow
    "39fc71ab",  # migrate: render the plan outside the spinner
    "82baee18",  # cli: operator suggestions on the exception base
    "868b32d1",  # create: announce dotted bench names
    "69269fb3",  # bake: warn mount benches
    "98ec71aa",  # domain: suggest the remove grammar
    "b550ed92",  # ssl: address check before the HTTP-01 port guard
    "9b51a9ba",  # logs/apps list/Ctrl-C/nginx reload wording
    "7bdcfbbb",  # bench: get_bench no longer starts containers
    "25d63ab1",  # services: no empty external_domains.toml on read
    "23520c18",  # services: trusted-proxies writes the gunicorn wrapper
    "77274d44",  # services: trusted-proxies writes the gunicorn wrapper
    "56ee040c",  # nginx: log the visitor's scheme
    "e28c0272",  # prune: include the benches root in teardown
    "a4ae81c6",  # prune: include the benches root in teardown
}

# Paths that cannot change what a user observes. Deliberately generous.
INTERNAL_PREFIXES = (
    "tests/",
    "scripts/",
    ".github/",
    ".omp/",
    "docs/",  # a docs-only commit documents a change; the change itself is gated
)
INTERNAL_FILES = {
    ".gitignore",
    ".envrc",
    ".env.example",
    "justfile",
    "uv.lock",
    "cliff.toml",
    "AGENTS.md",
    "CLAUDE.md",
}

# Types that never ship behavior. `docs` belongs here: the change it documents is itself gated.
INTERNAL_TYPES = ("chore", "ci", "build", "test", "style", "docs", "revert")
TYPE_RE = re.compile(rf"^({'|'.join(INTERNAL_TYPES)})(\([^)]*\))?!?:", re.I)

SKIP_RE = re.compile(r"^Changelog:\s*skip\b\s*(.*)$", re.I | re.M)


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, no caller-supplied binary
        ("git", *args), cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def last_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0").strip() or None
    except subprocess.CalledProcessError:
        return None


def range_start() -> tuple[str, str]:
    """The newer of the last release tag and the gate baseline, so neither can drag the other back."""
    tag = last_tag()
    if not tag:
        return BASELINE, f"baseline {BASELINE}"
    # --is-ancestor is the only honest comparison here: tag names do not sort against a sha.
    ancestor = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ("git", "merge-base", "--is-ancestor", tag, BASELINE), cwd=ROOT, capture_output=True, check=False
    )
    if ancestor.returncode == 0:
        return BASELINE, f"baseline {BASELINE}"
    return tag, tag


# ASCII record/field separators: a commit body can contain anything, including the delimiters
# a naive format would pick. These two cannot appear in a git message.
_REC, _FLD = "\x1e", "\x1f"


def walk(start: str) -> list[tuple[str, str, str, list[str]]]:
    """(sha, subject, body, files) per non-merge commit, in ONE git call.

    Three calls per commit was 858 processes and ~7s over a release; this is one and ~0.15s.
    """
    out = git("log", "--no-merges", "--name-only", f"--format={_REC}%H{_FLD}%s{_FLD}%B{_FLD}", f"{start}..HEAD")
    records = []
    for chunk in out.split(_REC):
        if not chunk.strip():
            continue
        sha, subject, body, rest = chunk.split(_FLD, 3)
        files = [f for f in rest.split("\n") if f.strip()]
        records.append((sha, subject, body, files))
    return records


def verdict(sha: str, subject: str, body: str, files: list[str]) -> str:
    """recorded | skipped | internal | unaccounted."""
    if any(f.startswith(RECORD_PATHS) for f in files):
        return "recorded"
    if any(sha.startswith(c) for c in COVERED):
        return "recorded"
    if SKIP_RE.search(body):
        return "skipped"
    if TYPE_RE.match(subject):
        return "internal"
    # An empty file list is an empty commit: nothing shipped.
    if not files or all(f.startswith(INTERNAL_PREFIXES) or f in INTERNAL_FILES for f in files):
        return "internal"
    return "unaccounted"


def main() -> int:
    os.chdir(ROOT)

    if not (ROOT / RECORD_PATHS[0]).exists():
        print(f"changelog-check is not working: {RECORD_PATHS[0]} does not exist")
        return 2

    # An explicit ref points the gate at history it did not police, which is the only way to
    # check the gate itself.
    override = sys.argv[1] if len(sys.argv) > 1 else None
    start, label = (override, override) if override else range_start()
    try:
        records = walk(start)
    except subprocess.CalledProcessError:
        # A shallow clone resolves no range; reporting "clean" there passes a whole release.
        print(f"changelog-check is not working: cannot resolve {start}..HEAD (shallow clone?)")
        return 2

    buckets: dict[str, list[tuple[str, str]]] = {"recorded": [], "skipped": [], "internal": [], "unaccounted": []}
    for sha, subject, body, files in records:
        buckets[verdict(sha, subject, body, files)].append((sha[:8], subject))

    # A COVERED sha that matches nothing is a lie the register is still telling: the commit was
    # rebased away or mistyped, so it exempts nothing and hides that it stopped meaning anything.
    # Only meaningful over the gate's own range, so an explicit override does not report it.
    if not override:
        seen = {sha for sha, _, _, _ in records}
        stale = sorted(c for c in COVERED if not any(s.startswith(c) for s in seen))
        if stale:
            print(f"\n  stale COVERED entries ({len(stale)}): {', '.join(stale)}")
            print("       Remove them from scripts/changelog_check.py; they exempt nothing.")
            return 1

    missing = buckets["unaccounted"]
    print(f"changelog coverage: {len(records)} commits since {label}")
    if missing:
        print(f"\n  unaccounted ({len(missing)}):")
        for sha, subject in missing[:20]:
            print(f"    {sha}  {subject[:88]}")
        if len(missing) > 20:
            print(f"    ... {len(missing) - 20} more")
    print(
        f"\n  recorded: {len(buckets['recorded'])}"
        f"     internal: {len(buckets['internal'])}"
        f"     skipped: {len(buckets['skipped'])}"
    )

    if missing:
        print(
            f"\nerror: {len(missing)} commits ship with no changelog entry.\n"
            f"       Write one in {RECORD_PATHS[0]}, or add a trailer to the commit:\n"
            "           Changelog: skip <why this is invisible to users>"
        )
        return 1

    print(f"\nclean: every commit since {label} is accounted for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
