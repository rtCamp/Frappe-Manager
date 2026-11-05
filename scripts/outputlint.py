#!/usr/bin/env python3
"""A ratchet on the shape of fm's operator output. Run via `just output-lint`; CI runs it on every PR.

The channel an operator cannot turn off reached 653 lines one commit at a time, none of them
unreasonable on its own, while `-v` sat at 23. Sorting that out was a one-time pass; this is what
stops it happening again. Each count may fall and may never rise, which is the only form of this
check that does not fail every existing line on the day it lands.

The four style rules are exact, read off the parsed source:

- **Gerund openers.** "Installing X.." narrates work the spinner heading above it is already
  narrating. Effects are past tense.
- **"successfully".** No line prints it on failure, so it distinguishes nothing.
- **Trailing `.` or `..`.** Cosmetic drift; 93 lines had one and 560 did not.
- **Opening with an interpolated value.** "{domain} removed" reads as a list entry and sorts
  badly in a log; "Removed {domain}" is a sentence.

Prose, prompts, error blocks and instructions are NOT exempt from the count -- they are why the
numbers do not go to zero. The ratchet does not care *which* lines are left, only that the total
never grows, so a new effect line written in the wrong shape has to displace an old one.

A FIFTH rule is reported and never fails the build: a `change_head` whose function contains no
later `print` is a step that finishes with nothing on screen, because the live display is
transient. Checked inside one function it over-reports by about a third -- the completion often
lives in a callee -- and a gate that cries wolf gets bypassed, so this one only warns.

Deliberately NOT checked: the total number of always-on lines. A new command legitimately prints
its answer, and a cap on the total would be satisfied by writing that answer at the wrong level,
which is the defect this whole effort removed.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "frappe_manager"

# Recorded on the commit that finished the reclassification. Lower these when you improve a line;
# never raise one to make a new line fit.
BASELINE = {
    "gerund": 18,
    "successfully": 1,
    "trailing_period": 56,
    "value_first": 14,
}

EXPLAIN = {
    "gerund": "opens with a gerund (narration belongs in change_head, effects are past tense)",
    "successfully": "says 'successfully' (nothing prints it on failure)",
    "trailing_period": "ends in a full stop or ellipsis",
    "value_first": "opens with an interpolated value instead of a verb",
}

# Not verbs, they just end that way. "Nothing was changed" is not narration.
NOT_GERUNDS = {"Nothing", "Something", "Anything", "Everything", "Missing", "String", "During", "Ring"}


def _message(call: ast.Call) -> str | None:
    """The first argument as text, with interpolations collapsed to `{}`."""
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(str(p.value) if isinstance(p, ast.Constant) else "{}" for p in arg.values)
    return None


def _first_word(text: str) -> str:
    stripped = re.sub(r"^[^\w{]*", "", text)
    words = stripped.split()
    return words[0] if words else ""


def collect() -> tuple[dict[str, list[str]], list[str]]:
    findings: dict[str, list[str]] = {key: [] for key in BASELINE}
    silent_steps: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        # The handler's own module defines these methods rather than calling them.
        if "output_manager" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = path.relative_to(PACKAGE.parent)

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "print":
                continue
            text = _message(node)
            if not text or not text.strip():
                continue
            where = f"{rel}:{node.lineno}  {text[:60]}"
            opener = _first_word(text)
            if opener.endswith("ing") and opener not in NOT_GERUNDS:
                findings["gerund"].append(where)
            if "uccessful" in text:
                findings["successfully"].append(where)
            if text.rstrip().endswith("."):
                findings["trailing_period"].append(where)
            if text.lstrip().startswith("{}"):
                findings["value_first"].append(where)

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            heads, prints = [], []
            for node in ast.walk(func):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "change_head":
                        heads.append(node.lineno)
                    elif node.func.attr in ("print", "print_data", "live_lines"):
                        prints.append(node.lineno)
            for head in heads:
                if not any(line > head for line in prints):
                    silent_steps.append(f"{rel}:{head}  in {func.name}()")

    return findings, silent_steps


def main() -> int:
    findings, silent_steps = collect()

    if not any(findings.values()) and not silent_steps:
        # An empty result means the walk found nothing to walk, not a clean tree.
        print("outputlint: found no output calls at all; the checkout looks broken", file=sys.stderr)
        return 2

    failed = False
    for key, baseline in BASELINE.items():
        found = len(findings[key])
        if found > baseline:
            failed = True
            print(f"\n{key}: {found} lines {EXPLAIN[key]}, up from {baseline}.")
            for line in findings[key][:10]:
                print(f"    {line}")
            if found > 10:
                print(f"    … and {found - 10} more")
        elif found < baseline:
            print(f"{key}: down to {found} from {baseline} -- lower BASELINE in {Path(__file__).name}")

    if silent_steps:
        print(f"\nwarning: {len(silent_steps)} heading(s) with no completion in their own function.")
        print("A heading erases itself, so these steps may finish with nothing on screen. About a")
        print("third are false alarms where the completion lives in a callee; this never fails.")
        for line in silent_steps[:5]:
            print(f"    {line}")
        if len(silent_steps) > 5:
            print(f"    … and {len(silent_steps) - 5} more")

    if failed:
        print("\nA line in the wrong shape has to displace an old one, not add to the total.")
        return 1

    counts = ", ".join(f"{key} {len(findings[key])}" for key in BASELINE)
    print(f"clean: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
