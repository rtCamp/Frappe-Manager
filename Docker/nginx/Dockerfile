FROM nginx:alpine

RUN apk update && DEBIAN_FONTEND=noninteractive apk add --no-cache python3 py3-pip bash && \
    curl -o /usr/local/bin/wait-for-it https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh && \
    chmod +x /usr/local/bin/wait-for-it
RUN pip install --no-cache-dir -U jinja2-cli --break-system-packages

COPY template.conf /config/
COPY ./entrypoint.sh /
RUN chmod +x /entrypoint.sh
RUN mkdir -p /etc/nginx/custom && rm -rf /etc/nginx/conf.d/default.conf

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]

