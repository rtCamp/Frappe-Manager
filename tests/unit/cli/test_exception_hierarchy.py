"""Every fm exception must reach the handler that presents it as an error, not a crash.

`main.py` distinguishes exactly one thing about a raised exception:

    except FrappeManagerException as e:   ->  "Error Occurred: <msg>"   + prints e.details
    except Exception as e:                ->  "Unexpected Error: <msg>"

So the entire hierarchy answers a single question at the boundary, and the answer decides
whether the user reads a clean error or something that looks like fm fell over.

`frappe_manager/exceptions.py` has always said this in its own docstring: "All custom
exceptions inherit from FrappeManagerException to allow catching all FM-specific errors in
one place." It was not true. 31 classes rooted straight on `Exception` instead, across
site_manager, docker, services_manager, ssl_manager and the migration manager, so of 146
raise sites only 16 could reach the clean arm. Errors with careful messages, like
`BenchNotRunning` or `ComposeServiceNotFound`, were shown as "Unexpected Error" with a
traceback logged.

This file enforces the docstring. It is a repo-wide invariant rather than a list, so a new
exception added tomorrow is covered without anyone remembering to come back here.
"""

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from frappe_manager.exceptions import FrappeManagerException

PACKAGE = Path("frappe_manager")


def _all_exception_classes() -> list[tuple[str, type]]:
    """Every exception class defined in the package, found by importing every module.

    Deliberately not an AST-only scan: `issubclass` is the property that matters, and only
    the real class object knows its MRO.

    Modules are discovered from the filesystem rather than with `pkgutil.walk_packages`,
    which does not descend into a directory that has no `__init__.py`. Three of those exist
    (`utils` and `templates/adminer`). `compose_project` was a third: a directory holding
    nothing but its own exceptions file, whose five classes walk_packages silently skipped,
    making this file look green while checking nothing about them. It has since been deleted.
    """
    found: dict[str, type] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            module = importlib.import_module(str(path.with_suffix("")).replace("/", "."))
        except Exception:  # noqa: S112 - a module that cannot import is a different test's concern
            continue
        for name, obj in vars(module).items():
            if isinstance(obj, type) and issubclass(obj, BaseException) and obj.__module__.startswith("frappe_manager"):
                found[f"{obj.__module__}.{name}"] = obj
    return sorted(found.items())


EXCEPTIONS = _all_exception_classes()
EXCEPTION_NAMES = {name.rsplit(".", 1)[1] for name, _ in EXCEPTIONS}


class TestTheScanIsRealistic:
    def test_the_scan_finds_the_exception_classes(self):
        """A selector matching nothing would make every assertion below vacuous."""
        assert len(EXCEPTIONS) > 40

    def test_the_scan_reaches_every_subpackage_that_defines_errors(self):
        modules = {name.rsplit(".", 1)[0] for name, _ in EXCEPTIONS}
        for expected in (
            "frappe_manager.site_manager.exceptions",
            "frappe_manager.docker.compose_exceptions",
            "frappe_manager.services_manager.services_exceptions",
            "frappe_manager.ssl_manager.certificate_exceptions",
        ):
            assert expected in modules


class TestEveryExceptionReachesTheCleanArm:
    @pytest.mark.parametrize(("name", "cls"), EXCEPTIONS, ids=[n for n, _ in EXCEPTIONS])
    def test_it_is_a_frappe_manager_exception(self, name, cls):
        assert issubclass(cls, FrappeManagerException), (
            f"{name} inherits from Exception directly, so main.py reports it as "
            f"'Unexpected Error' with a traceback instead of a clean CLI error"
        )

    def test_no_class_declares_exception_as_a_direct_base(self):
        """The source-level form of the same rule, so the intent is visible in a diff.

        Only `FrappeManagerException` itself may sit directly on `Exception`.
        """
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.ClassDef):
                    continue
                if [ast.unparse(b) for b in node.bases] == ["Exception"] and node.name != "FrappeManagerException":
                    offenders.append(f"{path}:{node.lineno} {node.name}")

        assert offenders == [], "root these on FrappeManagerException:\n" + "\n".join(offenders)


class TestTheHandlerContractHolds:
    """What main.py relies on when it renders one of these."""

    def test_every_own_constructor_delegates_to_the_base(self):
        """`.message` and `.details` are set by the base `__init__`, so a class that defines
        its own must call it or `main.py`'s `if e.details:` would raise AttributeError on
        the arm that is supposed to be the clean one.

        Checked at source level because these constructors take domain-specific arguments
        (a compose path, a service list, a challenge name) and cannot all be called generically.
        An explicit `Base.__init__(self, ...)` counts: `BenchNotFoundError` has to use that form
        because `super()` follows the MRO into `OSError`. The runtime test below is what actually
        proves the call lands.
        """
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.ClassDef) or node.name == "FrappeManagerException":
                    continue
                if node.name not in EXCEPTION_NAMES:
                    continue  # only exception classes have this obligation
                own_init = next((c for c in node.body if isinstance(c, ast.FunctionDef) and c.name == "__init__"), None)
                if own_init is None:
                    continue
                delegates = any(
                    isinstance(c, ast.Call)
                    and ast.unparse(c.func).endswith(".__init__")
                    and ("super" in ast.unparse(c.func) or (c.args and ast.unparse(c.args[0]) == "self"))
                    for c in ast.walk(own_init)
                )
                if not delegates:
                    offenders.append(f"{path}:{own_init.lineno} {node.name}.__init__")

        assert offenders == [], "these never reach FrappeManagerException.__init__:\n" + "\n".join(offenders)

    def test_every_constructible_exception_really_has_details(self):
        """The runtime form of the rule above, and the only form that catches an MRO diversion.

        The source check cannot. `BenchNotFoundError` textually called `super().__init__(name, path)`
        and satisfied it for a whole release, while `FileNotFoundError` preceding `BenchException` in
        its MRO sent that call to `OSError.__init__`, which reads two positional arguments as
        (errno, strerror). So `.details` was never set, the bench name rendered as a bogus
        "[Errno nope.localhost]" prefix, and `main.py`'s clean arm died with an AttributeError on
        every "bench not found" -- the single most common error in the CLI.
        """
        checked, missing = 0, []
        for name, cls in EXCEPTIONS:
            try:
                params = [p for n, p in inspect.signature(cls.__init__).parameters.items() if n != "self"]
                required = [
                    "x"
                    for p in params
                    if p.default is inspect.Parameter.empty
                    and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                ]
                error = cls(*required)
            except Exception:  # noqa: S112 -- nothing to log: a constructor we cannot synthesise
                continue  # domain-specific arguments; the source-level check above covers it
            checked += 1
            if not hasattr(error, "details"):
                missing.append(name)

        assert checked > 30, f"expected most exceptions to be constructible, only built {checked}"
        assert missing == [], "these do not reach FrappeManagerException.__init__ at runtime:\n" + "\n".join(missing)

    def test_the_base_defaults_details_to_an_empty_mapping(self):
        assert FrappeManagerException("boom").details == {}

    def test_the_base_keeps_the_message_addressable(self):
        error = FrappeManagerException("boom")

        assert error.message == "boom"
        assert str(error) == "boom"

    def test_a_class_inheriting_the_base_constructor_takes_a_single_message(self):
        """Re-rooting is only safe because of this shape.

        Every re-rooted class either inherits the base `__init__` or calls
        `super().__init__(<one string>)`, which is exactly the base's `message` parameter.
        The inherited-constructor ones are checkable directly.
        """
        inherited = [(name, cls) for name, cls in EXCEPTIONS if cls.__init__ is FrappeManagerException.__init__]
        assert len(inherited) > 10, "expected many classes to inherit the base constructor"

        for name, cls in inherited:
            error = cls("boom")
            assert error.message == "boom", name
            assert error.details == {}, name
