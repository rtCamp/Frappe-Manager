#!/usr/bin/bash
/config/jinja2 -D SITENAME="$SITENAME" /config/template.conf > /etc/nginx/conf.d/default.conf

nginx -g 'daemon off;'
