#!/bin/bash

CreateSite() {
	local siteName="$1"
	local envN="$2"
	echo "Create SiteName: $siteName, Env: $envN"
	if [ ! "${envN:-}" ]; then
	   fm create $siteName
	else
	   fm create $siteName --environment $2
	fi

	echo "Get Request to the homepage of Site: $siteName, Env: $envN"
	TestSiteReachability "$siteName"
}

TestSiteReachability() {
	local siteName="$1"
	curl -f \
	    --retry 18 --retry-max-time 600 \
	    --head \
	    -H "Host: $siteName" \
	    -H "Cache-Control: no-cache,no-store" \
	    http://localhost:80
}

MigrationToLatest() {
    pip install -U frappe-manager
    echo "yes" | fm list
    fm --version
}

DeleteSite() {
    local siteName="$1"
    echo "Delete SiteName: $siteName"
    echo "yes" | fm delete $siteName
}


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
