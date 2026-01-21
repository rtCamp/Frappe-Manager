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

nginx -g 'daemon off;' &

nginx_pid=$!
wait $nginx_pid
