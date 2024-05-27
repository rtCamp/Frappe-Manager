#!/bin/bash

PS4='+\[\033[0;33m\](\[\033[0;36m\]${BASH_SOURCE##*/}:${LINENO}\[\033[0;33m\])\[\033[0m\] '

set -xe


source ${PWD}/fm.sh
source ${PWD}/helpers.sh

cd ~
echo "Frappe executing $PWD"
source env/bin/activate # make sure the envirnment variable is set

oldToNew() {
	echo "
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃      Migration Test from v0.9.0 to latest         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
"
	Prequisites
	Cleanup
	InstallFrappe "v0.9.0"
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
	echo "
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃      Migration Test from v0.9.0 to latest         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
"
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
