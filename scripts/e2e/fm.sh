#!/bin/bash

CreateSite() {
	local siteName="$1"
	local envN="$2"
	info_blue "Creating site: $siteName (Environment: ${envN:-default})"

	if [ ! "${envN:-}" ]; then
		fm --non-interactive create "$siteName" || {
			info_red "Failed to create site $siteName"
			exit 1
		}
	else
		fm --non-interactive create "$siteName" --environment "$envN" || {
			info_red "Failed to create site $siteName with environment $envN"
			exit 1
		}
	fi

	info_green "Site created successfully"
	TestSiteReachability "$siteName"
}

TestSiteReachability() {
	local siteName="$1"
	info_blue "Testing reachability for $siteName..."

	if curl -f --retry 20 --retry-max-time 120 --retry-delay 5 --head \
		-H "Host: $siteName" \
		-H "Cache-Control: no-cache,no-store" \
		http://localhost:80; then
		info_green "Site $siteName is reachable"
	else
		info_red "Site $siteName is not reachable"
		exit 1
	fi
}

InstallLatestFM() {
	if [ -n "${GITHUB_REF_TYPE}" ] && [ -n "${GITHUB_REF_NAME}" ]; then
		pip install -U "git+https://github.com/rtCamp/Frappe-Manager.git@${GITHUB_REF_NAME}"
	else
		# Fallback for local testing
		pip install -U frappe-manager
	fi
	fm --version
}

MigrateServices() {
	info_blue "Migrating fm's global services & configuration"
	fm --non-interactive services migrate --yes --on-failure rollback || {
		info_red "fm services migrate failed"
		exit 1
	}
}

MigrateAllBenches() {
	info_blue "Migrating all benches"
	fm --non-interactive migrate all --yes --on-failure rollback || {
		info_red "fm migrate all failed"
		exit 1
	}
}

MigrationToLatest() {
	InstallLatestFM
	# The command split: the services tier is a prerequisite fm migrate refuses to pull in
	# implicitly, so a full upgrade is exactly these two commands, in this order.
	MigrateServices
	MigrateAllBenches
	fm --non-interactive list
}

LedgerVersion() {
	# The services-tier ledger. Current spelling first, then the two legacy ones a
	# previous-release fm writes (system_migrated_to, top-level version).
	local cfg="$HOME/frappe/fm_config.toml"
	local v
	v=$(awk -F'"' '/^migrated_to/ {print $2; exit}' "$cfg")
	[ -n "$v" ] || v=$(awk -F'"' '/^system_migrated_to/ {print $2; exit}' "$cfg")
	[ -n "$v" ] || v=$(awk -F'"' '/^version/ {print $2; exit}' "$cfg")
	echo "$v"
}

AssertLedgerVersion() {
	local expected="$1"
	local actual
	actual=$(LedgerVersion)
	if [ "$actual" = "$expected" ]; then
		info_green "Ledger is at v$actual as expected"
	else
		info_red "Ledger mismatch: expected v$expected, found v${actual:-<none>}"
		exit 1
	fi
}

MigrationsDir() {
	python -c "import frappe_manager.migration_manager.migrations as m; print(m.__path__[0])"
}

InjectFailingMigration() {
	# A real migration class dropped into the installed package: discovery picks it up like
	# any shipped migration. Same version as the current release's base, module name sorting
	# AFTER the real ones ('zz'), so every real migration runs first and the failure lands at
	# the END of the chain -- the deepest rollback. No fm code knows this file exists.
	local target="$(MigrationsDir)/migrate_zz_rollback_drill.py"
	info_blue "Injecting failing migration: $target"
	cat > "$target" <<'PYEOF'
"""E2E rollback drill: an always-failing migration injected by scripts/e2e/fm.sh."""

from packaging.version import Version as PV

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import get_current_fm_version


class MigrationRollbackDrill(MigrationBase):
    version = Version(PV(get_current_fm_version()).base_version)

    def up(self):
        raise RuntimeError("e2e rollback drill: injected failure")

    def down(self):
        self.output.print("e2e rollback drill: nothing to undo for the injected failure")
PYEOF
}

RemoveFailingMigration() {
	rm -f "$(MigrationsDir)/migrate_zz_rollback_drill.py"
	info_green "Removed the injected failing migration"
}

ExpectServicesMigrateToFailAndRollBack() {
	info_blue "Running services migration expected to FAIL and roll back"
	if fm --non-interactive services migrate --yes --on-failure rollback; then
		info_red "fm services migrate SUCCEEDED with the failing migration injected"
		exit 1
	fi
	info_green "Migration failed and exited non-zero, as expected"
}

DeleteSite() {
	local siteName="$1"
	echo "Delete SiteName: $siteName"
	fm --non-interactive delete "$siteName" --yes
}

GetInfoSite() {
	local siteName="$1"
	echo "Info SiteName: $siteName"
	fm --non-interactive info "$siteName"
}

ListSites() {
	echo "List Sites"
	fm --non-interactive list
}

StartSite() {
	local siteName="$1"
	echo "Start SiteName: $siteName"
	fm --non-interactive start "$siteName"
}

StopSite() {
	local siteName="$1"
	echo "Stop SiteName: $siteName"
	fm --non-interactive stop "$siteName"
}
