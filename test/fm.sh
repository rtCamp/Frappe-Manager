#!/bin/bash

CreateSite() {
	local siteName="$1"
	local envN="$2"
	echo "Create SiteName: $siteName, Env: $envN"
	fm create $siteName --env $2

	echo "Get Request to the homepage of Site: $siteName, Env: $envN"
	TestSiteReachability "$siteName"
}

TestSiteReachability() {
	local siteName="$1"
	curl -f \
	   --head -H "Host: $siteName" \
			 http://localhost:80 || echo "Failed to curl $siteName"
}

MigrationToLatest() {
    pip install -U frappe-manager
    echo "yes" | fm list & sleep 10m ; kill $!
    fm --version
}

DeleteSite() {
    local siteName="$1"
    echo "Delete SiteName: $siteName"
    echo "yes" | fm delete $siteName
}

# pip install -U frappe-manager

GetInfoSite() {
    local siteName="$1"
    echo "Info SiteName: $siteName"
    fm info $siteName
}

ListSites() {
    echo "List Sites"
    fm list
}

StartSite() {
    local siteName="$1"
    echo "Start SiteName: $siteName"
    fm start $siteName
}

StopSite() {
    local siteName="$1"
    echo "Stop SiteName: $siteName"
    fm stop $siteName
}
