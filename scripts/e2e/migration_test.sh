#!/bin/bash

PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -e  # Exit on error
trap 'handle_error $?' ERR

handle_error() {
    info_red "Test failed with exit code $1"
    RemoveDanglingDockerStuff
    exit $1
}


source ${PWD}/fm.sh
source ${PWD}/helpers.sh

oldToNew() {
	Prequisites
	CreateSite "migration-site.localhost"
	ListSites
	StopSite "migration-site.localhost"
	StartSite "migration-site.localhost"
	GetInfoSite "migration-site.localhost"
	MigrationToLatest
	StartSite "migration-site.localhost"
	TestSiteReachability "migration-site.localhost"
	DeleteSite "migration-site.localhost"
	RemoveDanglingDockerStuff
}

semiNewToNew() {
	Prequisites
	CreateSite "migration-site.dev.local" dev
	ListSites
	StopSite "migration-site.dev.local"
	StartSite "migration-site.dev.local"
	GetInfoSite "migration-site.dev.local"
	MigrationToLatest
	StartSite "migration-site.dev.local"
	TestSiteReachability "migration-site.dev.local"
	DeleteSite "migration-site.dev.local"
	RemoveDanglingDockerStuff
}

rollbackDrill() {
	# Prove the rollback machinery END TO END: a real bench on the previous release, a real
	# migration chain that fails at its END (after the real migrations ran), and the three
	# things rollback must deliver: the ledger rewound, the site still serving, and a state
	# clean enough that the SAME migration succeeds on retry once the failure is removed.
	Prequisites
	CreateSite "rollback-drill.localhost"
	LEDGER_BEFORE=$(LedgerVersion)
	info_blue "Ledger before upgrade: v$LEDGER_BEFORE"
	InstallLatestFM
	InjectFailingMigration
	ExpectServicesMigrateToFailAndRollBack
	AssertLedgerVersion "$LEDGER_BEFORE"
	# curl only, no fm command: the bench is legitimately stale relative to the new CLI
	# here, and observation via HTTP is exactly what a rolled-back host must still do.
	TestSiteReachability "rollback-drill.localhost"
	RemoveFailingMigration
	MigrateServices
	MigrateAllBenches
	StartSite "rollback-drill.localhost"
	TestSiteReachability "rollback-drill.localhost"
	DeleteSite "rollback-drill.localhost"
	RemoveDanglingDockerStuff
}

time $1
# time oldToNew
# time semiNewToNew
