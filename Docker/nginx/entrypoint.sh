#!/usr/bin/bash
cleanup() {
    echo "Received signal, performing cleanup..."
    if [ -n "$nginx_pid" ]; then
        kill -s SIGQUIT "$nginx_pid"
    fi
    exit 0
}

# Trap SIGQUIT, SIGTERM, SIGINT
trap cleanup SIGQUIT

/config/jinja2 -D SITENAME="$SITENAME" /config/template.conf > /etc/nginx/conf.d/default.conf

nginx -g 'daemon off;' &
nginx_pid=$!
wait $nginx_pid
