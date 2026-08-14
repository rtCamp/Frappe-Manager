"""Characterization of the version-requirement parsing in ``bench_config``.

Six regex-based parses of the same kind of string (">=3.14,<3.15", "^18.0.0", "18.x")
live in this module and none of them was covered:

* ``parse_python_version_for_runtime``  -- first ``major.minor`` found anywhere,
* ``parse_node_version_for_runtime``    -- first digit run found anywhere,
* ``validate_python_version_compatibility`` -- ``>=X.Y`` floor, ``<X.Y`` ceiling and an
  exact ``X.Y`` that sets both,
* ``validate_node_version_compatibility``  -- ``>=N`` floor or exact ``N``.

They look alike but disagree on purpose (floor vs ceiling vs exact), on the accepted
shape (anchored operator vs "any digits anywhere") and on the return type (str vs int
tuple), so the disagreements below are the contract. The ugly corners are deliberate:
``parse_node_version_for_runtime(">=3.14,<3.15")`` returning ``"3"``, the runtime parsers
swallowing every exception while the validators do not, and the validators rejecting
``"3.14.2"``/``"18.x"`` that the runtime parsers happily accept. Callers in
``commands/update.py``, ``migrate_0_19_0.py`` and ``modules/bench_app.py`` branch on
exactly these values.
"""

import pytest

from frappe_manager.site_manager.bench_config import (
    parse_node_version_for_runtime,
    parse_python_version_for_runtime,
    validate_node_version_compatibility,
    validate_python_version_compatibility,
)

# --------------------------------------------------------------------------------------
# The python runtime parser -- hand uv a major.minor
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        # multi-clause: the FLOOR wins purely because it is textually first, not because
        # the operator is understood.
        (">=3.14,<3.15", "3.14"),
        (">=3.10,<3.14", "3.10"),
        (" >= 3.10 , < 3.14 ", "3.10"),
        # bare versions and poetry caret
        ("3.11", "3.11"),
        ("^3.11", "3.11"),
        ("~=3.12.0", "3.12"),
        # patch level is dropped
        ("3.10.5", "3.10"),
        # a bare major with no minor is NOT a version here
        (">=3", None),
        ("3", None),
        ("18.x", None),
        # malformed / empty
        ("abc", None),
        ("no digits here", None),
        ("   ", None),
        ("", None),
        (None, None),
        # no operator awareness at all: junk before the number is ignored
        ("v3.12", "3.12"),
        ("python3.12", "3.12"),
    ],
)
def test_parse_python_version_for_runtime(requirement, expected):
    assert parse_python_version_for_runtime(requirement) == expected


def test_parse_python_version_for_runtime_swallows_non_string_input():
    """The blanket ``except Exception`` turns a wrong-typed argument into None, not a crash."""
    assert parse_python_version_for_runtime(3) is None
    assert parse_python_version_for_runtime(["3.10"]) is None


# --------------------------------------------------------------------------------------
# The node runtime parser -- hand fnm a bare major
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        (">=18", "18"),
        (">=24", "24"),
        ("^18.0.0", "18"),
        ("18.x", "18"),
        ("18.12.0", "18"),
        ("18", "18"),
        # multi-clause: first digit run again, so a range keeps the floor
        (">=20 <22", "20"),
        # ...which is why feeding it a PYTHON requirement yields the useless "3"
        (">=3.14,<3.15", "3"),
        ("3.11", "3"),
        # malformed / empty
        ("abc", None),
        ("   ", None),
        ("", None),
        (None, None),
        # junk before the number is ignored
        ("v18", "18"),
    ],
)
def test_parse_node_version_for_runtime(requirement, expected):
    assert parse_node_version_for_runtime(requirement) == expected


def test_parse_node_version_for_runtime_swallows_non_string_input():
    assert parse_node_version_for_runtime(3) is None
    assert parse_node_version_for_runtime(3.5) is None


def test_runtime_parsers_disagree_on_the_same_string():
    """Same input, two different answers -- the two parsers are not interchangeable."""
    assert parse_python_version_for_runtime(">=3.14,<3.15") == "3.14"
    assert parse_node_version_for_runtime(">=3.14,<3.15") == "3"
    assert parse_python_version_for_runtime("^18.0.0") == "18.0"
    assert parse_node_version_for_runtime("^18.0.0") == "18"


# --------------------------------------------------------------------------------------
# The python validator -- floor plus ceiling, operator-anchored
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_version", "frappe_requirement", "compatible"),
    [
        # inside the frappe window
        ("3.14", ">=3.14,<3.15", True),
        (">=3.10,<3.14", ">=3.10,<3.14", True),
        (">=3.14", ">=3.14,<3.15", True),
        ("3.11", ">=3.10", True),
        # below the floor
        ("3.11", ">=3.14,<3.15", False),
        ("3.9", ">=3.10", False),
        # exact user version implies max = (major, minor + 1), so 3.15 breaks a <3.15 ceiling
        ("3.15", ">=3.14,<3.15", False),
        # frappe requirement unparseable or ceiling-only -> no floor -> always compatible
        ("3.11", "abc", True),
        ("3.11", "", True),
        ("3.10", "<3.14", True),
    ],
)
def test_validate_python_version_compatibility(user_version, frappe_requirement, compatible):
    is_compatible, message = validate_python_version_compatibility(user_version, frappe_requirement)
    assert is_compatible is compatible
    if compatible:
        assert message == ""
    else:
        assert message == f"Python {user_version} is incompatible with Frappe requirement {frappe_requirement}"


@pytest.mark.parametrize("user_version", ["abc", "", "   ", "<3.15", "3.14.2", "3"])
def test_validate_python_version_compatibility_unparseable_user_version(user_version):
    """A three-component version and a ceiling-only bound are 'unparseable' users.

    ``3.14.2`` is accepted by ``parse_python_version_for_runtime`` but rejected here: the
    exact-match branch is anchored (``^\\d+\\.\\d+$``), so the two parses genuinely differ.
    """
    assert validate_python_version_compatibility(user_version, ">=3.14,<3.15") == (
        False,
        f"Could not parse user version: {user_version}",
    )


def test_validate_python_version_compatibility_raises_on_none():
    """No blanket except here, unlike the runtime parsers."""
    with pytest.raises(TypeError):
        validate_python_version_compatibility(None, ">=3.14")


# --------------------------------------------------------------------------------------
# The node validator -- floor only
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_version", "frappe_requirement", "compatible"),
    [
        ("24", ">=24", True),
        (">=20", ">=18", True),
        ("18", ">=24", False),
        # frappe floor unparseable -> always compatible
        ("18", "abc", True),
        ("18", "", True),
    ],
)
def test_validate_node_version_compatibility(user_version, frappe_requirement, compatible):
    is_compatible, message = validate_node_version_compatibility(user_version, frappe_requirement)
    assert is_compatible is compatible
    if compatible:
        assert message == ""
    else:
        assert message == f"Node {user_version} is incompatible with Frappe requirement {frappe_requirement}"


@pytest.mark.parametrize("user_version", ["abc", "", "18.12.0", "18.x", "^18", "v18"])
def test_validate_node_version_compatibility_unparseable_user_version(user_version):
    """Everything ``parse_node_version_for_runtime`` tolerates, this rejects.

    The floor regex needs a literal ``>`` and the exact branch is ``^\\d+$``, so no caret,
    no ``v`` prefix, no dotted form gets through.
    """
    assert validate_node_version_compatibility(user_version, ">=18") == (
        False,
        f"Could not parse user version: {user_version}",
    )


def test_validate_node_version_compatibility_raises_on_none():
    with pytest.raises(TypeError):
        validate_node_version_compatibility(None, ">=18")
