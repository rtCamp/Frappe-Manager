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

# The site map is rendered on EVERY boot, not only when the file is absent.
#
# default.conf lives on a host-mounted volume, so a render-once guard made it outlive its own
# input: the container could be recreated with a correct SITE_MAPPINGS and still serve last
# render's map. nginx answers a Host it does not recognise with the FIRST server block, so an
# unmapped domain was served the primary site's data with an ordinary 200 -- wrong data, no error,
# nothing in the logs. Every path in fm that changed routing had to remember to delete this file
# first, and the ones that did not (an older fm, a restored configs/ directory) needed a separate
# healer host-side to notice. Rendering unconditionally makes the file output rather than state,
# so recreating the container is sufficient by itself.
#
# A domain with a `conf.d/<domain>.server.conf` is CLAIMED by the operator and left out of the
# render entirely: no map entry, no server block, so that file's own `server` owns the hostname
# with nothing of ours to collide with. Every other domain keeps being regenerated. The file has
# to live in `conf.d/` rather than `custom/`, because `custom/*.conf` is included INSIDE each
# server block, where a `server` directive does not parse.

# `nginx -t` on this config is NOT a pure syntax check: nginx resolves every `upstream` server name
# while it parses, so a test run before the bench's frappe container is up fails with `host not
# found in upstream` -- and that error is reported INSTEAD of a real syntax error further down the
# same file (measured: junk appended to a valid render was masked by it), so classifying a failure
# by its message is unsound. Pointing the unresolvable names at localhost for the duration of the
# test removes the confound, and a failure then means the config, whatever else is running.
#
# glibc's HOSTALIASES rather than /etc/hosts: the container runs as uid 1000 and cannot write
# /etc/hosts. It only rewrites names carrying no dot, which covers the compose service names this
# template uses (`frappe-site`, `socketio-site`); a dotted upstream someone adds themselves is left
# to resolve on its own, and if it cannot, the failure is reported against their own files.
validate_nginx_config() {
	local aliases names rc
	aliases=$(mktemp)
	names=$(sed -n 's/^[[:space:]]*server[[:space:]]\+\([A-Za-z0-9._-]\+\):[0-9]\+.*/\1/p' "$1" | sort -u)
	for name in $names; do
		getent hosts "$name" >/dev/null 2>&1 || echo "$name localhost" >>"$aliases"
	done
	HOSTALIASES="$aliases" nginx -t >/dev/null 2>&1
	rc=$?
	rm -f "$aliases"
	return $rc
}

render_site_map() {
	local rendered previous mappings
	rendered=$(mktemp)
	previous=$(mktemp)
	mappings=${SITE_MAPPINGS:-'{}'}

	printf '%s' "$mappings" | python3 -c '
import json, os, sys
try:
    sites = json.load(sys.stdin)
except Exception:
    sites = {}
kept = {d: s for d, s in sites.items() if not os.path.exists(f"/etc/nginx/conf.d/{d}.server.conf")}
json.dump({"site_map": kept}, sys.stdout)
' | jinja2 -f json /config/template.conf >"$rendered" || {
		echo "fm: could not render the site map, keeping the existing one" >&2
		rm -f "$rendered" "$previous"
		return 0
	}

	cp /etc/nginx/conf.d/default.conf "$previous" 2>/dev/null
	cp "$rendered" /etc/nginx/conf.d/default.conf

	# Checked before it is trusted, the way Caddy refuses to load a config it cannot parse. The
	# case this exists for is a template bug shipped in a new image: without it, every bench that
	# recreates its nginx loses routing at once, with it they keep serving the previous render.
	if validate_nginx_config /etc/nginx/conf.d/default.conf; then
		rm -f "$rendered" "$previous"
		return 0
	fi

	# `nginx -t` tests EVERY included file, so a failure is not necessarily ours: putting the old
	# render back tells the two apart, and the message has to name the right half or it sends the
	# operator looking in the wrong file.
	if [[ -s "$previous" ]]; then
		cp "$previous" /etc/nginx/conf.d/default.conf
		if validate_nginx_config /etc/nginx/conf.d/default.conf; then
			echo "fm: the generated site map is invalid, so the previous one is still in place. Routing may be stale." >&2
			rm -f "$rendered" "$previous"
			return 0
		fi
	fi

	# Either there was no previous render to fall back to, or it fails the same way: the fault is
	# not something reverting fixes. Serve the current map and say where to look.
	cp "$rendered" /etc/nginx/conf.d/default.conf
	echo "fm: nginx config is invalid -- check your own files in conf.d/ and custom/." >&2
	rm -f "$rendered" "$previous"
}

render_site_map

# A drop-in directory per site, so the per-site `include` each server block carries has a visible
# home an operator can put a `.conf` in. Built from the SAME input the blocks are rendered from, so
# the directories and the include lines cannot disagree; doing it host-side in fm instead meant two
# readers of two copies of the site list, which can drift when a config is saved but the container
# has not been recreated yet.
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
