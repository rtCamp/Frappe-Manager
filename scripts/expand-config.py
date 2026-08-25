#!/usr/bin/env python3
"""Substitute `FM_ACTION_*` environment variables into a config file, for CI.

The problem this solves is a committed config file that needs a secret. You want to keep
`ci/build.toml` in the repo, but not the registry token in it:

    [registry]
    username = "acme-ci"
    password = "${FM_ACTION_REGISTRY_TOKEN}"

The workflow puts the real value in the environment, this expands it, and fm reads a file
that never existed in git.

Deliberately narrow, for three reasons learned from the expansion already in fm
(`transport.py`, which uses `os.path.expandvars` on the `[registry]` credentials):

- **Only names starting with the prefix.** `os.path.expandvars` rewrites anything, so a
  perfectly reasonable `$HOME` or `$PATH` in a config value silently becomes a host path.
  Here only `FM_ACTION_*` is touched and everything else is left exactly as written.
- **An unset reference is an error.** `expandvars` leaves `${FM_ACTION_TOKENN}` as literal text,
  so a typo becomes a password of `${FM_ACTION_TOKENN}` and the failure surfaces later as a
  registry rejection. A missing variable stops the job instead.
- **Values are never printed.** Only the names that were substituted are logged.

This lives in the action layer on purpose. Expanding inside fm at load time would be
worse than useless: `export_to_toml` writes from the model, and 27 call sites rewrite
`bench_config.toml` during normal operation, so the first `fm switch` after a load-time
expansion would bake the plaintext secret into the file on disk.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ${NAME} or $NAME. The braced form is captured separately so a malformed braced
# reference can be reported rather than silently left alone.
BRACED = re.compile(r"\$\{([^}]*)\}")
BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ExpandError(Exception):
    """A reference that cannot be resolved, reported with everything that is wrong."""


def expand(text: str, prefix: str, environ: dict[str, str]) -> tuple[str, list[str]]:
    """Return the expanded text and the names substituted, in first-seen order.

    Only references whose name starts with ``prefix`` are touched. Anything else, braced
    or bare, is left exactly as it appears in the source.
    """
    missing: list[str] = []
    malformed: list[str] = []
    used: list[str] = []

    def take(name: str) -> str | None:
        if not name.startswith(prefix):
            return None
        if name not in environ:
            if name not in missing:
                missing.append(name)
            return ""
        if name not in used:
            used.append(name)
        return environ[name]

    def braced(match: re.Match[str]) -> str:
        inner = match.group(1)
        if not NAME.match(inner):
            # e.g. ${FM_ACTION_TOKEN:-fallback}. Shell default syntax is NOT supported, and
            # leaving it alone would look like it worked until the value reached fm.
            if inner.startswith(prefix) and inner not in malformed:
                malformed.append(inner)
            return match.group(0)
        value = take(inner)
        return match.group(0) if value is None else value

    def bare(match: re.Match[str]) -> str:
        value = take(match.group(1))
        return match.group(0) if value is None else value

    result = BARE.sub(bare, BRACED.sub(braced, text))

    problems: list[str] = []
    if malformed:
        problems.append(
            "unsupported reference syntax (a plain ${NAME} is the only form; shell "
            f"defaults and modifiers are not expanded): {', '.join('${' + m + '}' for m in malformed)}"
        )
    if missing:
        problems.append(f"referenced but not set in the environment: {', '.join(missing)}")
    if problems:
        raise ExpandError("; ".join(problems))

    return result, used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Substitute FM_ACTION_* environment variables into a config file.",
    )
    parser.add_argument("--in", dest="source", required=True, help="input file, or - for stdin")
    parser.add_argument("--out", dest="dest", required=True, help="output file, or - for stdout")
    parser.add_argument(
        "--prefix", default="FM_ACTION_", help="only expand names with this prefix (default FM_ACTION_)"
    )
    args = parser.parse_args(argv)

    if args.source == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    else:
        path = Path(args.source)
        if not path.is_file():
            print(f"error: config not found: {path}", file=sys.stderr)
            return 1
        text = path.read_text()
        label = str(path)

    try:
        expanded, used = expand(text, args.prefix, dict(os.environ))
    except ExpandError as e:
        print(f"error: {label}: {e}", file=sys.stderr)
        return 1

    if args.dest == "-":
        sys.stdout.write(expanded)
    else:
        out = Path(args.dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        # The result may hold secrets, so it is never group- or world-readable, and its
        # contents are never echoed. Only the names are.
        out.write_text(expanded)
        out.chmod(0o600)

    if used:
        print(f"{label}: expanded {', '.join(used)}", file=sys.stderr)
    else:
        print(f"{label}: no {args.prefix}* references", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
