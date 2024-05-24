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

DeleteSite() {
    local siteName="$1"
    echo "Delete SiteName: $siteName"
    fm delete $siteName # TODO: need to add -y/--yes flag
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
