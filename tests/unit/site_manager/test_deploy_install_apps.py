"""Contract tests for finalize new-app install detection (#323).

Covers the pure helpers behind DeployOrchestrator._install_new_apps:
- _parse_installed_apps: tolerant parse of `bench list-apps` output.
- _new_apps: image/config apps not yet installed on the site.

The defensive guard (skip unless the parsed set contains the always-present
`frappe`) lives in _install_new_apps and is exercised by the remote e2e; these
tests lock the parse + diff behaviour it depends on.
"""

from frappe_manager.site_manager.modules.deploy_orchestrator import (
    _new_apps,
    _parse_installed_apps,
)


def test_parse_name_only_lines():
    assert _parse_installed_apps(["frappe", "erpnext", "hrms"]) == {"frappe", "erpnext", "hrms"}


def test_parse_with_version_and_branch_columns():
    # Some Frappe versions print aligned version/branch columns after the name.
    lines = [
        "frappe                    15.30.0   version-15",
        "erpnext                   15.20.0   version-15",
    ]
    assert _parse_installed_apps(lines) == {"frappe", "erpnext"}


def test_parse_ignores_blanks_and_noise():
    # Blank lines, uppercase headers, and tokens with punctuation are dropped;
    # only lowercase app-module names survive.
    lines = ["", "   ", "Apps installed:", "-- frappe --", "custom_app"]
    assert _parse_installed_apps(lines) == {"custom_app"}


def test_parse_handles_none():
    assert _parse_installed_apps(None) == set()


def test_new_apps_returns_uninstalled_in_order():
    assert _new_apps(["frappe", "erpnext", "hrms"], {"frappe"}) == ["erpnext", "hrms"]


def test_new_apps_empty_when_all_installed():
    assert _new_apps(["frappe", "erpnext"], {"frappe", "erpnext", "payments"}) == []


def test_new_apps_empty_when_nothing_wanted():
    assert _new_apps([], {"frappe"}) == []
