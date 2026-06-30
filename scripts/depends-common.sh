#!/usr/bin/env bash
# Shared helpers for Dependabot PR management (sourced by Justfile_depends recipes)

# Resolve a git remote name or "auto" to an owner/repo string.
# If resolution fails, prints an error message and exits.
resolve_repo() {
    local input="$1"
    if [[ "$input" == "auto" || "$input" == "origin" || "$input" == "upstream" ]]; then
        local remote_url repo
        remote_url=$(git remote get-url "$input" 2>/dev/null || echo "")
        repo=$(echo "$remote_url" | sed -n 's|.*github.com[/:]\([^/]\+/[^/]\+\)|\1|p' | sed 's/\.git$//')
        [ -z "$repo" ] && repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo "")
        if [ -z "$repo" ]; then
            echo "Cannot determine repo. Specify: just depends owner/repo" >&2
            exit 1
        fi
        echo "$repo"
    else
        echo "$input"
    fi
}
