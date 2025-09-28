FROM nginx:latest

LABEL org.opencontainers.image.source="https://github.com/rtcamp/Frappe-Manager" \
	org.opencontainers.image.description="Nginx reverse proxy for Frappe Manager with templated configuration and custom entrypoint"

RUN apt update && DEBIAN_FONTEND=noninteractive apt install -y --no-install-recommends wait-for-it gosu python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -U jinja2-cli --break-system-packages

COPY template.conf /config/
COPY ./entrypoint.sh /
RUN chmod +x /entrypoint.sh
RUN mkdir -p /etc/nginx/custom && rm -rf /etc/nginx/conf.d/default.conf

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]

