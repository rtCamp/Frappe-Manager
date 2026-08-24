"""Contract tests for the pure helpers behind finalize new-app install (#323).

- ``_parse_app_names``: tolerant parse of one-name-per-line output. Used for both
  ``bench list-apps`` (what the site has) and an ``apps/`` directory listing (what
  the image carries), which is why it is not named after either one.
- ``_new_apps``: apps the image carries that are not yet on the site.

The behaviour that uses them, including WHERE the baked set comes from, is pinned
by ``TestInstallNewApps`` in ``test_deploy_orchestrator_contract.py``, which owns
the orchestrator harness.
"""

from frappe_manager.site_manager.modules.deploy_orchestrator import (
    _new_apps,
    _parse_app_names,
)


class TestParseAppNames:
    """One parser for both surfaces: first token, when it looks like a module name."""

    def test_parse_name_only_lines(self):
        assert _parse_app_names(["frappe", "erpnext", "hrms"]) == {"frappe", "erpnext", "hrms"}

    def test_parse_with_version_and_branch_columns(self):
        # Some Frappe versions print aligned version/branch columns after the name.
        lines = [
            "frappe                    15.30.0   version-15",
            "erpnext                   15.20.0   version-15",
        ]
        assert _parse_app_names(lines) == {"frappe", "erpnext"}

    def test_parse_ignores_blanks_and_noise(self):
        # Blank lines, uppercase headers, and tokens with punctuation are dropped;
        # only lowercase app-module names survive.
        lines = ["", "   ", "Apps installed:", "-- frappe --", "custom_app"]
        assert _parse_app_names(lines) == {"custom_app"}

    def test_parse_handles_none(self):
        assert _parse_app_names(None) == set()

    def test_a_directory_listing_drops_everything_that_cannot_be_an_app(self):
        """``apps/`` is on-disk truth, so it can hold entries that are not apps. A
        module name has no dot and no capital, which is what rules these out."""
        lines = ["frappe", "hrms", ".git", "README.md", "requirements.txt", "Procfile"]

        assert _parse_app_names(lines) == {"frappe", "hrms"}


class TestNewApps:
    def test_new_apps_returns_uninstalled_in_order(self):
        assert _new_apps(["frappe", "erpnext", "hrms"], {"frappe"}) == ["erpnext", "hrms"]

    def test_new_apps_empty_when_all_installed(self):
        assert _new_apps(["frappe", "erpnext"], {"frappe", "erpnext", "payments"}) == []

    def test_new_apps_empty_when_nothing_wanted(self):
        assert _new_apps([], {"frappe"}) == []
