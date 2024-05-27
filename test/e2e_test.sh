#!/bin/bash

PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe

source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"
source env/bin/activate # make sure the envirnment variable is set

main() {
	echo "
┏━━━━━━━━━━━━━━━━━━━━━━━┓
┃      E2E Test         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━┩
"
	Prequisites
	Cleanup
	InstallFrappe "main"

	CreateSite "test-site.prod.local" prod
	CreateSite "test-site.dev.local" dev

	ListSites

	StopSite "test-site.prod.local"
	StopSite "test-site.dev.local"

	StartSite "test-site.prod.local"
	StartSite "test-site.dev.local"

	TestSiteReachability "test-site.prod.local"
	TestSiteReachability "test-site.dev.local"

	GetInfoSite "test-site.prod.local"
	GetInfoSite "test-site.dev.local"

	DeleteSite "test-site.prod.local"
	DeleteSite "test-site.dev.local"

	RemoveDanglingDockerStuff
}

time main
