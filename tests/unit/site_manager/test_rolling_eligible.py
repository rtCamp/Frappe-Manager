"""Contract tests for the rolling (blue-green) deploy eligibility gate.

Defends: auto-mode picks rolling whenever the replica overlap is safe -- a
no-migrate deploy, an operator-asserted additive migration (empty
maintenance_mode_phases), or a migrate deploy covered by an active maintenance
window (both replicas serve the maintenance 503 off the shared
common_site_config, so old code never executes real requests against the
migrated schema). Only a migrate deploy with the maintenance window DISABLED
must recreate-swap. The --rolling/--no-rolling override wins regardless.
"""

import pytest

from frappe_manager.site_manager.modules.deploy_orchestrator import rolling_eligible


@pytest.mark.parametrize(
    ("migrate", "maintenance_mode", "phases", "expected"),
    [
        (False, True, ["migrate"], True),  # no migrate -> old code cannot break on new schema
        (False, False, [], True),  # no migrate, no window needed
        (True, False, [], True),  # migrate but operator asserts additive (no maintenance page)
        (True, True, ["migrate"], True),  # migrate covered by the maintenance window -> rolling is safe
        (True, True, ["migrate", "build"], True),  # window covers it regardless of extra phases
        (True, False, ["migrate"], False),  # migrate with the window DISABLED -> recreate-swap
    ],
)
def test_auto_mode(migrate, maintenance_mode, phases, expected):
    assert rolling_eligible(migrate, maintenance_mode, phases) is expected


@pytest.mark.parametrize("migrate", [True, False])
@pytest.mark.parametrize("maintenance_mode", [True, False])
@pytest.mark.parametrize("phases", [[], ["migrate"]])
def test_override_forces(migrate, maintenance_mode, phases):
    # --rolling / --no-rolling win over the auto gate in every combination.
    assert rolling_eligible(migrate, maintenance_mode, phases, override=True) is True
    assert rolling_eligible(migrate, maintenance_mode, phases, override=False) is False
