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
"""

FM_JSON_LOG_FORMAT = (
    '{"time":"$time_iso8601","request_id":"$request_id","client":"$remote_addr",'
    '"xff":"$http_x_forwarded_for","host":"$host","scheme":"$scheme","method":"$request_method",'
    '"path":"$request_uri","status":$status,"bytes":$body_bytes_sent,"request_time":$request_time,'
    '"upstream":"$upstream_addr","upstream_status":"$upstream_status",'
    '"upstream_time":"$upstream_response_time","referer":"$http_referer","ua":"$http_user_agent"}'
)
