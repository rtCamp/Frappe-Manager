#!/usr/bin/bash

cleanup() {
	echo "Received signal, performing cleanup..."
	if [ -n "$nginx_pid" ]; then
		nginx -s quit
		wait "$nginx_pid"
	fi
	exit 0
}

trap cleanup SIGQUIT SIGTERM

if ! [[ -f "/etc/nginx/conf.d/default.conf" ]]; then
	cat <<-JSON | jinja2 -f json /config/template.conf >/etc/nginx/conf.d/default.conf
		{"site_map": $SITE_MAPPINGS}
	JSON
fi

# A drop-in directory per site, so the per-site `include` each server block carries has a visible
# home an operator can put a `.conf` in. Built from the SAME input the blocks are rendered from, so
# the directories and the include lines cannot disagree; doing it host-side in fm instead meant two
# readers of two copies of the site list, which can drift when a config is saved but the container
# has not been recreated yet.
#
# Outside the render guard above on purpose: default.conf survives recreation on its host-mounted
# volume, so a site added later would never reach a mkdir that only ran on first render. This runs
# every boot and is idempotent.
#
# The container runs as the same uid as the host user (1000), so a directory it creates is one the
# operator can write to. An absent directory is harmless either way, because nginx treats a glob
# matching nothing as zero files.
if [[ -n "${SITE_MAPPINGS:-}" ]]; then
	printf '%s' "$SITE_MAPPINGS" | python3 -c '
import json, sys
try:
    sites = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for site in sorted(set(sites.values())):
    if site:
        print(site)
' | while read -r site; do
		mkdir -p "/etc/nginx/custom/$site"
	done
fi

nginx -g 'daemon off;' &

nginx_pid=$!
wait $nginx_pid
