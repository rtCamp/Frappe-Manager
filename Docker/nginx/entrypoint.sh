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
	# Support both old SITENAME format and new PRIMARY_DOMAIN + ALIAS_DOMAINS format
	if [[ -n "$PRIMARY_DOMAIN" ]]; then
		# New format: separate primary and aliases
		jinja2 -D PRIMARY_DOMAIN="$PRIMARY_DOMAIN" -D ALIAS_DOMAINS="${ALIAS_DOMAINS:-}" /config/template.conf >/etc/nginx/conf.d/default.conf
	else
		# Fallback to old SITENAME format for backward compatibility
		# Parse SITENAME: first domain is primary, rest are aliases
		PRIMARY=$(echo "$SITENAME" | cut -d',' -f1)
		ALIASES=$(echo "$SITENAME" | cut -d',' -f2- | sed 's/^,//')
		jinja2 -D PRIMARY_DOMAIN="$PRIMARY" -D ALIAS_DOMAINS="$ALIASES" /config/template.conf >/etc/nginx/conf.d/default.conf
	fi
fi

nginx -g 'daemon off;' &

nginx_pid=$!
wait $nginx_pid
