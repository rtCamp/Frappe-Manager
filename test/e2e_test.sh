#!/bin/bash



source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"
source env/bin/activate # make sure the envirnment variable is set

main() {
	Prequisites
	Cleanup
	InstallFrappe "main"

	CreateSite "test-site.prod.local" prod
	CreateSite "test-site.dev.local" dev

	ListSites
	
	# StopSites "test-site"
	# StartSites "test-site"

	GetInfoSite "test-site.prod.local"
	GetInfoSite "test-site.dev.local"

	DeleteSite "test-site.prod.local"
	DeleteSite "test-site.dev.local"

	RemoveDanglingDockerStuff
}

time main
