---
description: "Run formatters and lint autofixers only on the files you edited, never on a directory or the whole package"
condition: "(?i)(?=[^\\n]*(?:ruff\\s+(?:check|format)|\\bblack\\b|\\bisort\\b|\\beslint\\b|\\bprettier\\b|\\bbiome\\b|\\bgofmt\\b|cargo\\s+fmt))(?=[^\\n]*(?:--fix\\b|--unsafe-fixes\\b|--write\\b|ruff\\s+format\\b|cargo\\s+fmt\\b|gofmt\\b))(?![^\\n]*--(?:check|diff)\\b)(?![^\\n]*\\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|rb|java)\\b)[^\\n]{0,300}"
scope: "thinking, tool"
interruptMode: always
probes:
  fire:
    # A fixer pointed at a directory or at nothing rewrites files you never touched
    - "uv run ruff check frappe_manager --fix"
    - "uv run ruff check . --fix --output-format=concise"
    - "ruff check --fix"
    - "uv run ruff format ."
    - "cd /repo && ruff check src --unsafe-fixes"
    - "eslint --fix src/"
    - "cargo fmt"
    # A recipe or CI step doing the same thing is the same hazard, institutionalized
    - "fix:\n    uv run ruff check . --fix"
    - "      run: uv run ruff format ."
  silent:
    # Named files: the fix lands only where you were already working
    - "uv run ruff check frappe_manager/commands/ssl/add.py --fix"
    - "uv run ruff check tests/unit/cli/test_address_completion.py --fix --output-format=concise"
    - "uv run ruff check frappe_manager/site_manager/site.py frappe_manager/utils/helpers.py --fix"
    # Read-only: reporting is not rewriting, and scope does not matter
    - "uv run ruff check frappe_manager --output-format=concise"
    - "uv run ruff check . --statistics"
    - "uv run ruff format --check ."
    - "git show HEAD:file.py | uv run ruff check --stdin-filename file.py -"
    # Not a fixer at all
    - "just test"
    - "uv run pytest tests/unit -q"
---

Stop. You are about to rewrite files you did not edit.

A fixer pointed at a directory touches every file under it. The ones you edited get their fix; the rest get import re-ordering, quote normalization, `split` to `rsplit`, `if/elif` merges. All of it lands in your working tree, and at commit time it is indistinguishable from your change.

Name the files instead:

```
uv run ruff check path/to/the_file_you_edited.py --fix
```

Several files is fine. A directory is not.

## Why this matters more than it looks

The damage is not the fixes. They are usually correct. The damage is three things they do to the change you are actually making:

1. **The diff stops being reviewable.** A reviewer cannot tell your logic from the autofixer's whitespace, so they skim, and skimming is where real bugs get waved through.
2. **The blast radius is unverified.** You ran the tests for the module you changed. You did not run them thinking about the fifteen other files that just moved.
3. **It smuggles a second change into the first.** Even a correct, behaviour-neutral fix in an unrelated file belongs in its own commit, with its own message saying what it is.

House style can also be at stake. This repo sets `quote-style = "preserve"` precisely so nobody's quotes get rewritten out from under them; a package-wide fixer run does it anyway.

## Before you commit

If it already happened, do not talk yourself into keeping it because "the tests pass". Tests passing is not the bar; the bar is whether the file belongs in this change.

```
git diff --cached --name-only
```

Every file in that list must be one this change needed. For anything else:

```
git restore --staged --worktree <unrelated files>
```

Then re-run the suite, because you just changed the tree back.

Read the reverted diffs before you throw them away. A fixer that deleted lines is worth a second look: usually it merged two branches with identical bodies and is correct, occasionally it is not, and you cannot tell which from the `+1 -8` in the stat.

## When this fires on a file you are writing

It watches every tool, not just the shell, because a `tool:bash(...)` scope needs a path glob and a shell command has no path. So it also fires when you WRITE a `just` recipe or a CI step that runs a package-wide autofix. That is intended: a recipe doing this on every run is the same hazard with a schedule attached. Scope the recipe, or say why the sweep is deliberate.

## The one honest exception

A deliberate, repo-wide formatting sweep is fine. It is its own commit, it contains nothing else, and its message says so. That is the opposite of this: you are not sweeping, you are fixing one thing and letting a tool rewrite the neighbourhood.

State which files you are fixing, then continue.
