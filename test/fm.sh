#!/bin/bash

CreateSite() {
    local siteName="$1"
    local envN="$2"
    info_blue "Creating site: $siteName (Environment: ${envN:-default})"
    
    if [ ! "${envN:-}" ]; then
        fm create "$siteName" || {
            info_red "Failed to create site $siteName"
            exit 1
        }
    else
        fm create "$siteName" --environment "$envN" || {
            info_red "Failed to create site $siteName with environment $envN"
            exit 1
        }
    fi

    info_green "Site created successfully"
    TestSiteReachability "$siteName"
}

TestSiteReachability() {
    local siteName="$1"
    info_blue "Testing reachability for $siteName..."
    
    if curl -f --retry 20 --retry-max-time 120  --retry-delay 5 --head \
        -H "Host: $siteName" \
        -H "Cache-Control: no-cache,no-store" \
        http://localhost:80; then
        info_green "Site $siteName is reachable"
    else
        info_red "Site $siteName is not reachable"
        exit 1
    fi
}

MigrationToLatest() {
    if [ -n "${GITHUB_REF_TYPE}" ] && [ -n "${GITHUB_REF_NAME}" ]; then
            pip install -U "git+https://github.com/rtCamp/Frappe-Manager.git@${GITHUB_REF_NAME}"
    else
        # Fallback for local testing
        pip install -U frappe-manager
    fi
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
