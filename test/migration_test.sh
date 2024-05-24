#!/bin/bash

source env/bin/activate # make sure the envirnment variable is set


source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"

main() {
    Prequisites
	Cleanup
	InstallFrappe "v0.12.0"
	CreateSite "migration-site.dev.local" dev
	ListSites
	GetInfoSite "migration-site.dev.local"
	# UpgradeToLatestFm  TODO: need to add the upgrade to fm.sh
	TestSiteReachability "migration-site.dev.local"
	DeleteSite "migration-site.dev.local"
	RemoveDanglingDockerStuff
}

time main
