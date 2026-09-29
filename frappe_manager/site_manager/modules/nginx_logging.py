"""Structured JSON access-log format shared by both nginx layers.

One identical format at the global nginx-proxy and at bench nginx, so a single
ingestion pipeline parses everything. Fields that nginx renders as ``-`` or as
comma-separated lists on retries (upstream_*) are quoted, keeping every line
valid JSON (a bare 503 from the maintenance gate has no upstream, for
example); genuinely numeric fields stay unquoted.

Neither layer is configured from here: the proxy reads the format from the
LOG_FORMAT / LOG_FORMAT_ESCAPE environment in the services compose template,
bench nginx has it in its own image template (``Docker/nginx/template.conf``,
rendered to ``conf.d/default.conf`` by the container entrypoint). This constant
is the single source both are checked against, so drift fails a unit test
instead of silently splitting the two log streams.

``scheme`` is the CLIENT's scheme, not the connection the logging nginx received: behind a
trusted front every hop inside fm is plain HTTP, so ``$scheme`` logged ``http`` for every
request on a fully HTTPS site and the field carried no information at all. Both layers define
``$fm_client_scheme`` -- the proxy from ``fm-forwarded-trust.conf`` (see ``realip.py``), bench
nginx from a map in its own image template -- so the field means the same thing at both hops.
"""

FM_JSON_LOG_FORMAT = (
    '{"time":"$time_iso8601","request_id":"$request_id","client":"$remote_addr",'
    '"xff":"$http_x_forwarded_for","host":"$host","scheme":"$fm_client_scheme","method":"$request_method",'
    '"path":"$request_uri","status":$status,"bytes":$body_bytes_sent,"request_time":$request_time,'
    '"upstream":"$upstream_addr","upstream_status":"$upstream_status",'
    '"upstream_time":"$upstream_response_time","referer":"$http_referer","ua":"$http_user_agent"}'
)
