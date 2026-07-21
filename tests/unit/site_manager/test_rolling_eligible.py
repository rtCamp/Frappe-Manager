"""Contract tests for the rolling (blue-green) deploy eligibility gate.

Defends: auto-mode picks rolling only for a no-migrate deploy or an operator-
asserted additive migration (empty maintenance_mode_phases), a migrate deploy
with a maintenance window stays on recreate-swap, and the --rolling/--no-rolling
override wins regardless of the migrate/phases state.
"""

import pytest

from frappe_manager.site_manager.modules.deploy_orchestrator import rolling_eligible


@pytest.mark.parametrize(
    ("migrate", "phases", "expected"),
    [
        (False, ["migrate"], True),   # no migrate -> old code cannot break on new schema
        (False, [], True),            # no migrate, additive assertion
        (True, [], True),             # migrate but operator asserts additive (no maintenance page)
        (True, ["migrate"], False),   # migrate with maintenance window -> recreate-swap
        (True, ["migrate", "build"], False),
    ],
)
def test_auto_mode(migrate, phases, expected):
    assert rolling_eligible(migrate, phases) is expected


@pytest.mark.parametrize("migrate", [True, False])
@pytest.mark.parametrize("phases", [[], ["migrate"]])
def test_override_forces(migrate, phases):
    # --rolling / --no-rolling win over the auto gate in every combination.
    assert rolling_eligible(migrate, phases, override=True) is True
    assert rolling_eligible(migrate, phases, override=False) is False
