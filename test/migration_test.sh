#!/bin/bash

PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe


source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"
source env/bin/activate # make sure the envirnment variable is set

oldToNew() {
    Prequisites
	Cleanup
	InstallFrappe "v0.9.0"
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

semiNewToNew() {
    Prequisites
	Cleanup
	InstallFrappe $(curl --silent https://api.github.com/repos/rtCamp/Frappe-Manager/tags | jq -r '.[1].name')
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

time oldToNew
time semiNewToNew
