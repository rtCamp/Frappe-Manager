#!/bin/bash

PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe


source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"
source env/bin/activate # make sure the envirnment variable is set

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
