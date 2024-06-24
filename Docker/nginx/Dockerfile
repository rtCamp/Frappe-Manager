FROM python:3.11 as builder

LABEL author=rtCamp
LABEL org.opencontainers.image.source=https://github.com/rtcamp/Frappe-Manager

RUN pip install jinja2-cli pyinstaller frappe-bench
RUN pyinstaller --onefile --hidden-import __future__ $(which jinja2)
RUN cp -r "$(bench src)"/bench/config/templates /dist

FROM nginx:latest

RUN apt update && DEBIAN_FONTEND=noninteractive apt install -y --no-install-recommends wait-for-it gosu && rm -rf /var/lib/apt/lists/*

COPY --from=builder /dist/ /config/
COPY template.conf /config/
COPY ./entrypoint.sh /
RUN chmod +x /entrypoint.sh
RUN mkdir -p /etc/nginx/custom && rm -rf /etc/nginx/conf.d/default.conf

ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
