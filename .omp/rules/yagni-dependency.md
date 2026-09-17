---
description: "Before adding a dependency: does the stdlib, the platform, or something already installed do it?"
condition: '"[a-zA-Z][a-zA-Z0-9_-]*(\[[^\]]+\])?\s*[><=!~]+\s*[0-9].*"'
scope: "tool:edit(pyproject.toml), tool:write(pyproject.toml)"
interruptMode: tool-only
probes:
  fire:
    # Real dependency entries with version specifiers, as they appear in pyproject.toml
    - '"typer>=0.21.0,<0.22.0"'
    - '"cryptography>=46.0.0,<50.0.0"'
    - '"email-validator[docutils]>=2.3.0"'
    - '"pytest>=9.0.0,<10.0.0"'
    - '"requests~=2.32.0"'
  silent:
    # Metadata fields that are quoted strings but not dependencies
    - '"version": "0.1.0"'
    - '"description": "A CLI tool"'
    - '"MIT"'
    - '"https://github.com/rtcamp/frappe-manager"'
    # Unquoted metadata with version-like values — pyproject.toml TOML syntax
    - 'name = "frappe-manager"'
    - 'requires-python = ">=3.13,<3.14"'
    - 'line-length = 120'
    - 'target-version = "py313"'
    - 'build-backend = "hatchling.build"'
    # Bare package name without a version specifier — the regex requires >=, ==, etc.
    - '"typer-examples"'
    - '"mike"'
    # [tool.uv.sources] entries — package name with git source, not a version pin
    - 'typer-examples = { git = "https://github.com/Xieyt/typer-examples", tag = "v1.1.0" }'
    - 'mike = { git = "https://github.com/squidfunk/mike.git" }'
    # A dependency comment
    - '# A dependency comment'
---

You are adding a dependency. That is rungs 3 to 5 of the ladder, in the order to check them:

3. **Standard library** — This project pins `>=3.13,<3.14`, so you have the full 3.13 stdlib: `tomllib` for TOML parsing, `itertools.batched()`, `asyncio.TaskGroup`, `argparse` for CLI argument parsing, `pathlib` for filesystem paths, `urllib.parse` for URL manipulation, `json`, `csv`, `email`, `http.server`, `unittest` for tests. Check there first.
4. **Already installed** — Read the existing `dependencies` and `dev` groups in `pyproject.toml`. This project already ships with `typer` (CLI), `requests` (HTTP), `pydantic` (validation), `jinja2` (templating), `ruamel.yaml` (YAML), `tomlkit` (TOML), `gitpython` (git), `psutil` (process info), `cryptography` (crypto), `click` (via typer), `rich` (via typer). A framework you already depend on often covers what you are about to add; check its exports before reaching outward.
5. **PyPI search** — If it is not in stdlib and not already installed, search PyPI before writing a helper. A well-maintained package that solves the problem correctly is cheaper than a bug you will ship.

## The cost you are signing up for

A dependency is not free at the moment you add it. It is a version pin to maintain, a transitive tree to audit on every `uv sync`, a lockfile change to review, a supply-chain surface, and a thing the next reader has to learn. A twenty-line local helper is often cheaper over a year than a package that does forty things you do not need.

## When adding one is right

When it solves a genuinely hard problem you would otherwise get wrong: cryptography, timezone data, parsers for real grammars, protocol implementations. Do not hand-roll those.

Say which rung you checked and why it did not hold, then add it.
