#!/usr/bin/env python3
"""Docs checks that mkdocs does not do: dash style, link hygiene, and flags that do not exist.

Run via `just docs-lint`. CI runs it on every PR.

Why each check exists, so nobody has to guess before deleting one:

- **Dashes.** House style is no em/en dashes and no ` -- ` connector in prose. Typer help
  text is rendered verbatim, so a dash that lands in a docstring ships to the terminal.
- **Links.** mkdocs happily builds a site full of links to files that do not exist. Every
  removed command leaves a trail of them: deleting `fm deploy` left `docs/commands/index.md`
  pointing at a `deploy.md` that was no longer generated.
- **Flags.** Renaming a flag does not rename it in prose. The real flag set is read from the
  live CLI, not a checked-in list, so this cannot drift: rename a flag and the stale mention
  fails here.

Generated pages (`docs/commands/`) are exempt from *style* checks, because their content comes
from help text that this script cannot fix, but their *links* are checked: that is where a
removed command shows up first.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

FMX_SRC = ROOT / "Docker" / "frappe" / "fmx"

# Flags owned by other tools, legitimately named as theirs in our docs.
FOREIGN = {
    # generic, or ours-by-coincidence across many tools
    "--help",
    "--version",
    "--verbose",
    "--debug",
    "--quiet",
    "--force",
    "--yes",
    "--list",
    "--info",
    "--file",
    "--user",
    "--site",
    "--port",
    "--queue",
    "--tail",
    "--from",
    "--reinstall",
    "--upgrade",
    "--log-level",
    "--non-interactive",
    "--challenge-alias",  # acme.sh
    "--db-root-username",
    "--db-root-password",
    "--with-files",  # bench
    "--with-private-files",
    "--with-public-files",
    "--sync-config",  # bench
    "--depth",  # git
    "--fetch-python",  # uv
    "--preload",
    "--threads",
    "--worker-class",
    "--max-requests",  # gunicorn
    "--graceful-timeout",
    "--max-requests-jitter",
    "--host",  # gunicorn
    "--skip-character-set-client-handshake",  # mariadb
    "--load",
    "--platform",
    "--push",
    "--target",
    "--build-arg",
    "--label",  # docker/buildx
}

DASHES = {"\u2014": "em dash", "\u2013": "en dash", "\u2015": "horizontal bar", "\u2012": "figure dash"}


# Anything under these is somebody else's markdown: a virtualenv, an installed package, a
# node_modules tree. `just test-fmx` builds Docker/frappe/fmx/.venv, which put a vendored
# typer SKILL.md inside the Docker/ glob and failed this check on a flag typer documents
# about itself.
VENDORED = {".venv", "venv", "site-packages", "node_modules", ".git", "dist", "build", "__pycache__"}


def _ours(path: Path) -> bool:
    return not VENDORED.intersection(path.parts)


def hand_written() -> list[Path]:
    """Docs we author. Generated command pages and the changelog are excluded from style checks."""
    md = sorted(p for p in Path("docs").rglob("*.md") if _ours(p))
    md = [p for p in md if p.parts[:2] != ("docs", "commands") and p != Path("docs/changelog.md")]
    return md + sorted(p for p in Path("Docker").rglob("*.md") if _ours(p))


def fm_flags() -> set[str]:
    """Every option name the real CLI accepts, read from the live Typer/Click app."""
    import click
    import typer.main

    from frappe_manager.commands import app

    def walk(cmd):
        yield cmd
        if isinstance(cmd, click.Group):
            for sub in cmd.commands.values():
                yield from walk(sub)

    flags: set[str] = set()
    for cmd in walk(typer.main.get_command(app)):
        for param in cmd.params:
            flags |= set(param.opts) | set(param.secondary_opts)
    return flags


def fmx_flags() -> set[str]:
    """fmx's options, parsed from source: it imports supervisor, so it cannot load on a host.

    Every string literal that looks like an option declaration counts, including the
    ``"--drain/--no-drain"`` pairs typer uses for negatable flags.

    Vendored trees are excluded, and that matters more here than for the docs glob: every
    flag found becomes an ALLOWED name, so sweeping in a venv's site-packages inflates the
    allowlist (176 real flags became 638) and lets a doc typo pass by matching some flag in
    an unrelated dependency.
    """
    flags: set[str] = set()
    for py in FMX_SRC.rglob("*.py"):
        if not _ours(py):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for part in node.value.split("/"):
                    if re.fullmatch(r"--[a-z][a-z0-9-]*", part):
                        flags.add(part)
    return flags


def anchors_of(text: str) -> set[str]:
    """Heading anchors mkdocs will emit: explicit ``{#id}`` plus the auto-slug."""
    found: set[str] = set()
    for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M):
        heading = m.group(1)
        explicit = re.search(r"\{#([\w-]+)\}", heading)
        if explicit:
            found.add(explicit.group(1))
        bare = re.sub(r"\{#[\w-]+\}", "", heading)
        found.add(re.sub(r"[^\w\s-]", "", bare).strip().lower().replace(" ", "-"))
    return found


def check_dashes(files: list[Path]) -> list[str]:
    hits = []
    for f in files:
        fenced = False
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue  # style is about prose; `--` in a shell example is an argument separator
            for ch, name in DASHES.items():
                if ch in line:
                    hits.append(f"{f}:{i} {name}: {line.strip()[:70]}")
            if re.search(r"\S -- \S", line) and not re.search(r"--[a-z]", line):
                hits.append(f"{f}:{i} prose --: {line.strip()[:70]}")
    return hits


def check_links() -> tuple[list[str], list[str]]:
    """Absolute links, and links whose target file or anchor does not exist."""
    all_md = sorted(p for p in Path("docs").rglob("*.md") if _ours(p))
    anchors = {f: anchors_of(f.read_text()) for f in all_md}

    absolute: list[str] = []
    broken: list[str] = []
    for f in all_md:
        for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)", f.read_text()):
            target = m.group(1)
            if target.startswith("/"):
                absolute.append(f"{f}: {target}")
                continue
            if target.startswith(("http", "mailto:")):
                continue
            rel, _, frag = target.partition("#")
            if not rel:
                if frag and frag not in anchors[f]:
                    broken.append(f"{f}: #{frag} (missing anchor, same page)")
                continue
            # A directory link resolves to its index page, which is what mkdocs serves.
            raw = rel + "index.md" if rel.endswith("/") else rel
            resolved = Path(os.path.normpath(f.parent / raw))
            if not resolved.exists():
                broken.append(f"{f}: {target} (missing file)")
            elif frag and resolved.suffix == ".md" and frag not in anchors.get(resolved, set()):
                broken.append(f"{f}: {target} (missing anchor)")
    return absolute, broken


def check_flags(files: list[Path], real: set[str]) -> dict[str, set[str]]:
    unknown: dict[str, set[str]] = {}
    for f in files:
        for m in re.finditer(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})", f.read_text()):
            flag = m.group(1)
            if flag not in real:
                unknown.setdefault(flag, set()).add(str(f))
    return unknown


def main() -> int:
    os.chdir(ROOT)
    files = hand_written()
    fm, fmx = fm_flags(), fmx_flags()

    # A silent no-op is worse than a failure: an empty glob or a CLI that stopped importing
    # would otherwise report a clean run and let anything through.
    if len(files) < 10 or len(fm) < 50:
        print(f"docslint is not working: {len(files)} docs, {len(fm)} fm flags, {len(fmx)} fmx flags")
        return 2

    dash_hits = check_dashes(files)
    absolute, broken = check_links()
    unknown = check_flags(files, fm | fmx | FOREIGN)

    for label, items in (
        ("dash violations", dash_hits),
        ("absolute links", absolute),
        ("broken links", broken),
    ):
        print(f"{label}: {len(items)}")
        for line in items[:12]:
            print(f"  {line}")
    print(f"flags in docs that exist in neither fm nor fmx: {len(unknown)}")
    for flag, where in sorted(unknown.items()):
        print(f"  {flag:<32} {sorted(where)[:2]}")

    failed = bool(dash_hits or absolute or broken or unknown)
    if not failed:
        print(f"\nclean: {len(files)} hand-written docs checked against {len(fm | fmx)} real flags")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
