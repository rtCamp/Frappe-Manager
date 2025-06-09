FROM nginx:latest

LABEL author=rtCamp
LABEL org.opencontainers.image.source=https://github.com/rtcamp/Frappe-Manager

RUN apt update && DEBIAN_FONTEND=noninteractive apt install -y --no-install-recommends wait-for-it gosu && rm -rf /var/lib/apt/lists/*

COPY ./entrypoint.sh /
RUN chmod +x /entrypoint.sh
RUN mkdir -p /etc/nginx/custom && rm -rf /etc/nginx/conf.d/default.conf

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]

