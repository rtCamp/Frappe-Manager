FROM nginx:latest

RUN apt update && DEBIAN_FONTEND=noninteractive apt install -y --no-install-recommends wait-for-it && rm -rf /var/lib/apt/lists/*

COPY mysite-localhost.conf /config/
RUN mkdir -p /config
COPY mysite-localhost-ssl.conf /config/
COPY ./entrypoint.sh /
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
