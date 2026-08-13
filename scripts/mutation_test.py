#!/usr/bin/env python
"""Mutation testing: does the suite NOTICE when the code becomes wrong?

Coverage answers "did this line run". That is a much weaker question than the one that matters
before a refactor, which is "if I break this line, will a test tell me". This script answers the
second one: it injects one plausible bug at a time into a line the suite already executes, runs the
suite, and records whether anything failed.

    just mutate            # 60 mutations, roughly 3 minutes
    just mutate 200        # bigger sample, proportionally slower

Read the score as: SURVIVED entries are lines where a real bug ships silently. The list written to
/tmp/mutation-results.tsv is a to-do list of missing assertions, ordered by nothing in particular:
pick the ones in code you are about to touch.

Verdicts:
    KILLED    a test failed. Good, that line is defended.
    SURVIVED  the code is now wrong and the suite is still green. That is a gap.
    TRIVIAL   the mutation broke an import, so the whole suite errored. Says nothing about
              assertion quality, and is excluded from the score.
    HUNG      the suite did not finish in time. Usually the mutation created an infinite loop;
              pytest-timeout normally converts these into ordinary failures.

Sampling is seeded, so two runs pick the same mutations and the numbers are comparable. The sample
is drawn only from lines the suite executes, which means the population grows as coverage grows:
when comparing before/after, either keep the same seed AND the same coverage file, or replay the
exact rows from a previous results file.
"""

import ast
import contextlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
COVERAGE_JSON = Path(os.environ.get("MUT_COV", str(Path(tempfile.gettempdir()) / "fm-mutation-cov.json")))
RESULTS = Path(os.environ.get("MUT_RESULTS", str(Path(tempfile.gettempdir()) / "fm-mutation-results.tsv")))
SAMPLE_SIZE = int(os.environ.get("MUT_N", "60"))
MAX_PER_FILE = int(os.environ.get("MUT_MAX_PER_FILE", "2"))
SEED = int(os.environ.get("MUT_SEED", "1337"))

# The suite runs in ~3s, so anything past this is a hang, not slowness. Kept well under
# pytest-timeout's own per-test ceiling so a wedged run is cut off here first.
RUN_TIMEOUT = 30

# Operators chosen to imitate refactor slips: off-by-one, inverted condition, swapped boolean.
MUTATIONS = [
    (" == ", " != "),
    (" != ", " == "),
    (" is None", " is not None"),
    (" is not None", " is None"),
    (" and ", " or "),
    (" or ", " and "),
    (" > ", " >= "),
    (" < ", " <= "),
    ("True", "False"),
    ("False", "True"),
    (" + ", " - "),
]

SKIP_LINE_PREFIXES = ("#", '"""', "'''", "import ", "from ", "def ", "class ", "@")

_in_flight: dict[Path, str] = {}


def _restore_all(*_):
    """Never leave a mutated file behind, whatever happens."""
    for path, original in _in_flight.items():
        with contextlib.suppress(OSError):
            path.write_text(original)
    _drop_bytecode()
    print("\n!! interrupted: every mutated file restored", flush=True)
    sys.exit(130)


def _drop_bytecode():
    """Restoring a same-size mutation within the same second leaves a STALE .pyc.

    Python invalidates cached bytecode on (source mtime, source size). A one-character operator
    swap keeps the size, and a fast restore can land in the same mtime second, so the interpreter
    happily keeps running the MUTATED bytecode afterwards. That produces phantom import errors long
    after this script has exited, so the cache goes at the end of every mutation.
    """
    for cache in list(ROOT.glob("frappe_manager/**/__pycache__")) + list(ROOT.glob("tests/**/__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


def ensure_coverage() -> dict[str, Any]:
    """Mutations are only meaningful on lines the suite actually executes."""
    if not COVERAGE_JSON.exists():
        print(f"collecting coverage -> {COVERAGE_JSON}", flush=True)
        subprocess.run(  # noqa: S603
            [
                PY,
                "-m",
                "pytest",
                "tests/unit",
                "-q",
                "-p",
                "no:cacheprovider",
                "--cov=frappe_manager",
                f"--cov-report=json:{COVERAGE_JSON}",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    return json.loads(COVERAGE_JSON.read_text())


def build_candidates(cov: dict[str, Any]) -> list[tuple[str, int, int, str, str]]:
    """Every candidate is (file, line, COLUMN, old, new).

    The column matters. A line like ``mkdir(parents=True, exist_ok=True)`` holds two ``True``
    tokens, and they are not equivalent: flipping ``parents`` is inert in the tested paths while
    flipping ``exist_ok`` raises FileExistsError and is caught by 5 tests. Identifying a mutation by
    line alone (and replacing the first match) silently conflates the two, so a replay can "confirm"
    a verdict that belongs to a different mutation. Each occurrence is therefore its own candidate.
    """
    found = []
    for filename, data in cov["files"].items():
        if not data["executed_lines"]:
            continue
        try:
            lines = Path(ROOT / filename).read_text().splitlines()
        except OSError:
            continue
        for lineno in data["executed_lines"]:
            if lineno > len(lines):
                continue
            line = lines[lineno - 1]
            stripped = line.strip()
            if not stripped or stripped.startswith(SKIP_LINE_PREFIXES):
                continue
            for old, new in MUTATIONS:
                start = line.find(old)
                while start != -1:
                    found.append((filename, lineno, start, old, new))
                    start = line.find(old, start + 1)
    random.seed(SEED)
    random.shuffle(found)
    return found


def run_suite() -> tuple[str, str, float]:
    started = time.time()
    try:
        result = subprocess.run(  # noqa: S603
            [PY, "-m", "pytest", "tests/unit", "-q", "--no-cov", "-x", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            # No readable stdin: a mutation that steers code into a prompt must fail fast rather
            # than block on input that will never come.
            stdin=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "HUNG", f"no result in {RUN_TIMEOUT}s", time.time() - started

    if result.returncode == 0:
        return "SURVIVED", "suite still green", time.time() - started

    output = (result.stdout or "") + (result.stderr or "")
    tail = ([line for line in output.strip().splitlines() if line.strip()] or [""])[-1][:80]
    if "error" in tail.lower() and "failed" not in tail.lower():
        return "TRIVIAL", tail, time.time() - started
    return "KILLED", tail, time.time() - started


# While this runs, production files on disk are TRANSIENTLY WRONG: each mutation is written, tested,
# then restored. Anything that reads the tree meanwhile (rsync to a server, a build, a container
# image, another pytest run) can capture a mutated file and look like a real failure somewhere else
# entirely. This lock makes that visible instead of mysterious.
LOCK = ROOT / ".mutation-in-progress"


def _acquire_lock() -> None:
    if LOCK.exists():
        print(f"refusing to start: {LOCK} exists ({LOCK.read_text().strip()}).", file=sys.stderr)
        print(
            "another mutation run is in flight, or a previous one was killed -9. Delete it to override.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    LOCK.write_text(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    print("!! source files are transiently mutated while this runs. Do NOT deploy, rsync or build.")
    print(f"   lock: {LOCK}")


def _release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def main() -> int:
    signal.signal(signal.SIGINT, _restore_all)
    signal.signal(signal.SIGTERM, _restore_all)
    os.chdir(ROOT)
    _acquire_lock()

    candidates = build_candidates(ensure_coverage())
    print(f"{len(candidates)} mutable covered lines; sampling {SAMPLE_SIZE} (max {MAX_PER_FILE}/file, seed {SEED})")
    print("-" * 100, flush=True)

    RESULTS.write_text("")
    verdicts: Counter[str] = Counter()
    per_file: defaultdict[str, int] = defaultdict(int)
    ran = 0
    started = time.time()

    for filename, lineno, col, old, new in candidates:
        if ran >= SAMPLE_SIZE:
            break
        if per_file[filename] >= MAX_PER_FILE:
            continue

        path = ROOT / filename
        original = path.read_text()
        lines = original.splitlines(keepends=True)
        line = lines[lineno - 1]
        # Splice at the recorded column so the mutation is unambiguous even when the same token
        # appears several times on one line.
        if line[col : col + len(old)] != old:
            continue
        mutated_line = line[:col] + new + line[col + len(old) :]
        original_line = lines[lineno - 1].rstrip("\n")
        lines[lineno - 1] = mutated_line
        source = "".join(lines)
        try:
            ast.parse(source)
        except SyntaxError:
            continue

        per_file[filename] += 1
        ran += 1
        change = f"col{col}:{old.strip() or 'not'} -> {new.strip() or '(removed)'}"
        print(f"[{ran:>4}/{SAMPLE_SIZE}] {filename:<58}:{lineno:<5} {change:<20} ...", flush=True)

        _in_flight[path] = original
        try:
            path.write_text(source)
            verdict, detail, elapsed = run_suite()
        finally:
            path.write_text(original)
            _in_flight.pop(path, None)
            _drop_bytecode()

        verdicts[verdict] += 1
        print(f"{'':>11} {'':<58} {'':<5} {'':<20} -> {verdict:<9} {elapsed:>5.1f}s", flush=True)
        with RESULTS.open("a") as handle:
            # The ORIGINAL line text is recorded too. Line NUMBERS drift as soon as anyone edits the
            # file, so a later replay that trusts the number alone can mutate a different line and
            # report a confident, wrong verdict. With the text, a replay can relocate the line.
            handle.write(f"{filename}\t{lineno}\t{change}\t{verdict}\t{original_line}\t{detail}\n")

    print("-" * 100)
    print(f"{ran} mutations across {len(per_file)} modules in {time.time() - started:.0f}s")
    for verdict, count in verdicts.most_common():
        print(f"  {verdict:<10} {count:>4}")

    scored = verdicts["KILLED"] + verdicts["SURVIVED"]
    if scored:
        print(f"\nmutation score: {100 * verdicts['KILLED'] // scored}% ({verdicts['KILLED']}/{scored})")
        print(f"gaps (SURVIVED) listed in {RESULTS}")
    _release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
