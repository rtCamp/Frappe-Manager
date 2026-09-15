#!/usr/bin/env python3
"""Dead-code radar: vulture over the Python packages + a defined-but-never-called
scan over the container shell scripts.

A RADAR, not a gate. Every hit needs human judgment before deletion -- vulture is
static and this repo has real dynamic dispatch. Known dynamic patterns are
suppressed below (each with its reason); what remains is classified into:

  DEAD        name referenced nowhere else in the repo (production OR tests)
  TESTS-ONLY  name referenced only under tests/ -- per AGENTS.md a test earns its
              place only by defending live behavior, so these are delete
              candidates too (method AND its tests), after checking the tests
              don't pin behavior reachable another way

Run via `just deadcode`. Exits 0 always (advisory); exits 2 if the scan itself
found nothing scannable (broken checkout).
"""

from __future__ import annotations

import ast
import functools
import re
import sys
from pathlib import Path

from vulture import Vulture

ROOT = Path(__file__).resolve().parent.parent

PY_ROOTS = ["frappe_manager", "Docker/frappe/fmx/fmx"]
SH_ROOTS = ["Docker", "scripts"]


@functools.cache
def _enum_member_lines(filename: str) -> frozenset[int]:
    """Line numbers of class-level assignments inside Enum subclasses.

    Enum members are consumed by ITERATION (`for s in ServicesEnum`) and by
    typer/pydantic parsing raw CLI/config values -- none of which names the
    member, so name-based analysis calls live members dead (seen live:
    ServicesEnum.global_nginx_proxy via `fm services stop`'s enum walk).
    """
    try:
        tree = ast.parse(Path(filename).read_text())
    except (OSError, SyntaxError):
        return frozenset()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any("Enum" in ast.dump(base) for base in node.bases):
            for stmt in node.body:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    lines.add(stmt.lineno)
    return frozenset(lines)


# Names invoked dynamically -- vulture cannot see the caller. Keep each reason.
IGNORE_NAMES = [
    "cli_entrypoint",  # pyproject [project.scripts] entry point
    "MigrationV*",  # MigrationDiscovery imports migration modules by filename
    "frame",  # signal-handler signature (signal.signal callback contract)
    "fm_ctx",  # LogRecord attr consumed by the file formatter's %(fm_ctx)s template (log.py)
    # Result-object payload fields: written by live constructors (often same-file
    # kwargs the cross-file sweep cannot see), read by humans via debug output or
    # kept as diagnostic state -- not dead weight (see also checks_* below).
    "validated_url",  # AppValidationResult diagnostic (bench_config.py)
    "resolved_ip",  # CDNDetectionResult debug field, designed as such (cdn_detection.py docstring)
    "server_enforces_tls",  # ProbeResult report state (db_probe.py)
    "tls_in_force",  # ProbeResult report state (db_probe.py)
    "expected_value",  # ValidationResult dig diagnostic (dns_validator.py)
    "dns_query_output",  # ValidationResult raw dig output (dns_validator.py)
    # Read-side twins used by tests to verify live write behavior; deleting them
    # would inline non-trivial logic into every assertion:
    "get_events",  # JSONOutputHandler's read API for its captured-event product
    "has_redirect_config",  # VhostConfigManager marker-block query (7 live-behavior tests)
    # Pydantic config fields reached ONLY via string dispatch, kwargs, or TOML keys
    # -- each verified live by hand (see the STRING-REF audit in the session log):
    "last_deploy_at",  # [deploy_state] field: written deploy_orchestrator.py, read back via kwarg in bench_config.py
    "before_restart",  # switch hook fields: getattr dispatch via hooks.py field tuples
    "after_restart",  # and deploy_orchestrator's _switch_hook("<name>") string lookups
    "before_migrate",
    "key_source",  # CustomCertificate field: declared_field(cert, "key_source") dispatch
    # PropagationStatus result-payload fields: kwarg-constructed in the SAME file
    # (dns_validator.py:277), which the cross-file string sweep cannot see.
    # Write-only informational payload on a result object, not dead weight.
    "checks_passed",
    "checks_total",
    "model_config",  # pydantic reads it off the class
    # ruamel.yaml emitter knobs: attribute writes ON the yaml object, read by ruamel
    "preserve_quotes",
    "default_flow_style",
    "default_style",
    "ignore_aliases",
    "__getattr__",  # module-level lazy import hook (PEP 562)
]

IGNORE_DECORATORS = [
    "@docker_command",  # builds argv FROM the signature; bodies are empty on purpose
    "@field_validator",  # pydantic calls these
    "@model_validator",
    "@computed_field",
    "@*.command",  # typer registers the function; CLI invokes it
    "@*.callback",
    "@overload",  # typing overload stubs are never "called"
]

WORD = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"


def _repo_text(subdirs: list[str], suffixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    out = []
    for sub in subdirs:
        base = ROOT / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix in suffixes and p.is_file() and ".venv" not in p.parts and "node_modules" not in p.parts:
                out.append((p, p.read_text(errors="replace")))
    return out


def python_hits() -> list[tuple[str, str]]:
    """Returns (classification, description) rows."""
    v = Vulture(ignore_names=IGNORE_NAMES, ignore_decorators=IGNORE_DECORATORS)
    v.scavenge([str(ROOT / r) for r in PY_ROOTS])

    tests = _repo_text(["tests", "Docker/frappe/fmx/tests"], (".py",))
    # Non-python surfaces that can legitimately reference a python name (templates,
    # docs snippets don't count -- only executable surfaces).
    other = _repo_text(["Docker", "scripts", "frappe_manager/templates"], (".sh", ".py", ".conf", ".tmpl"))
    # Production python, re-scanned as TEXT. Vulture sees identifiers only; this
    # catches string-borne references it is blind to: pydantic forward-ref
    # annotations ("AppBuildHooks | None"), getattr/string dispatch
    # (_switch_hook("after_migrate")), constructor kwargs (cert_source=...).
    prod_text = _repo_text(PY_ROOTS, (".py",))

    rows = []
    for item in v.get_unused_code(min_confidence=60):
        if item.typ == "variable" and item.confidence == 100:
            # 100%-confidence variables are unused FUNCTION PARAMETERS -- a
            # signature-cleanliness concern, not dead weight (and mostly @overload
            # stubs here). Module-level constants report at 60% and stay in.
            continue
        if item.typ == "variable" and item.first_lineno in _enum_member_lines(str(item.filename)):
            continue  # Enum member: reached by iteration/value parsing, not by name
        pat = re.compile(WORD.format(re.escape(item.name)))
        rel = Path(item.filename).relative_to(ROOT)
        in_tests = any(pat.search(text) for _, text in tests)
        in_other = any(pat.search(text) for p, text in other if p.resolve() != Path(item.filename).resolve())
        if in_other:
            continue  # referenced from a shell script/template: live
        if any(pat.search(text) for p, text in prod_text if p.resolve() != Path(item.filename).resolve()):
            # Vulture found no identifier use, so this occurrence is a string,
            # kwarg, or comment in another production file: usually live (dynamic
            # dispatch), occasionally a stale comment. Human call.
            rows.append(("STRING-REF", f"{rel}:{item.first_lineno}  {item.typ} {item.name}"))
            continue
        kind = item.typ
        desc = f"{rel}:{item.first_lineno}  {kind} {item.name}"
        rows.append(("TESTS-ONLY" if in_tests else "DEAD", desc))
    return rows


def shell_hits() -> list[str]:
    """Bash functions defined in tracked .sh files but never referenced anywhere."""
    scripts = _repo_text(SH_ROOTS, (".sh",))
    everything = scripts + _repo_text(["frappe_manager", "tests", "Docker"], (".py", ".conf", ".tmpl", ".yml", ".yaml"))
    # Reference-only surfaces without a scannable suffix: justfiles and Dockerfiles
    # call bash functions too (RUN bash -c ..., source + call in recipes).
    for extra in [ROOT / "justfile", *ROOT.glob("scripts/justfile*"), *ROOT.rglob("Docker/**/*Dockerfile*")]:
        if extra.is_file():
            everything.append((extra, extra.read_text(errors="replace")))

    defn = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.M)
    rows = []
    for path, text in scripts:
        for m in defn.finditer(text):
            name = m.group(1)
            pat = re.compile(WORD.format(re.escape(name)))
            refs = sum(len(pat.findall(t)) for _, t in everything)
            # 1 = the definition itself
            if refs <= 1:
                line = text[: m.start()].count("\n") + 1
                rows.append(f"{path.relative_to(ROOT)}:{line}  function {name}")
    return rows


def duplicate_constants() -> list[str]:
    """Module-level UPPER_CASE names defined in more than one production module.

    The drift alarm for the constants consolidation: BENCH_PYTHON was once defined
    identically in two sibling modules, and CONTAINER_BENCH_DIR had a synonym
    (FRAPPE_BENCH_DIR) plus two value-twins. One name, one home; a duplicate here
    is either a missed import or the start of the next divergence.
    """
    owners: dict[str, list[str]] = {}
    for p, text in _repo_text(PY_ROOTS, (".py",)):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            targets = list(node.targets) if isinstance(node, ast.Assign) else []
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper() and not t.id.startswith("_"):
                    owners.setdefault(t.id, []).append(f"{p.relative_to(ROOT)}:{node.lineno}")
    return [f"{name}: {', '.join(sites)}" for name, sites in sorted(owners.items()) if len(sites) > 1]


def main() -> int:
    py = python_hits()
    sh = shell_hits()

    if not py and not sh and not any((ROOT / r).is_dir() for r in PY_ROOTS):
        # The scan saw nothing scannable at all: broken checkout, not a clean one.
        print("deadcode: nothing scannable -- broken checkout?", file=sys.stderr)
        return 2

    for label in ("DEAD", "TESTS-ONLY", "STRING-REF"):
        rows = [d for k, d in py if k == label]
        if rows:
            print(f"\n== python: {label} ({len(rows)}) ==")
            print("\n".join(rows))
    if sh:
        print(f"\n== shell: never referenced ({len(sh)}) ==")
        print("\n".join(sh))

    dupes = duplicate_constants()
    if dupes:
        print(f"\n== constants: same name, multiple homes ({len(dupes)}) ==")
        print("\n".join(dupes))

    total = len(py) + len(sh) + len(dupes)
    print(f"\n{total} candidate(s). Radar output: verify dynamic dispatch before deleting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
