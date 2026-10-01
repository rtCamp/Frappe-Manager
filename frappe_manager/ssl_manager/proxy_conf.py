"""Nginx-proxy vhost.d fragment bodies for per-domain concerns.

Pure content functions: no classes, no file IO. `ProxyDropins` (services_manager/proxy_dropins.py)
writes these bodies into per-concern fragment files; a fragment's filename carries its ordering
and identity, so unlike the old marked-block scheme these bodies need no `# fm:<name> BEGIN/END`
markers of their own.
"""


def https_redirect_conf() -> str:
    """Per-domain HTTPS-redirect body.

    Allows internal services (socketio) to make HTTP API calls without redirect, since Node.js
    fetch() drops Cookie headers on cross-protocol redirects. Keyed on `$fm_client_scheme`, not
    `$scheme`, so ONE body works whether fm terminates TLS itself or a trusted front forwards
    the original scheme (fm-forwarded-trust.conf, always defined -- see realip.py) -- no more
    per-certificate variant. `$fm_https_suffix` carries the public HTTPS port when not 443.
    """
    return """set $redirect_to_https 0;
if ($fm_client_scheme = http) { set $redirect_to_https 1; }
if ($uri ~ ^/api/method/frappe\\.realtime\\.) { set $redirect_to_https 0; }
if ($redirect_to_https = 1) { return 301 https://$host$fm_https_suffix$request_uri; }
"""


def hsts_conf(value: str) -> str:
    """Per-domain Strict-Transport-Security override body for `value` (an STS header value, or "off").

    Lives in vhost.d rather than nginx-proxy's own `HSTS` env var because that env var's header
    emission is gated on `https_method != "noredirect"`, and fm sets `HTTPS_METHOD=noredirect` for
    every bench unconditionally (see `BenchConfig.export_to_compose_inputs`) so that fm's own
    per-domain redirect fragment -- not nginx-proxy's blunter, all-domains-or-none one -- decides
    when a domain force-redirects to HTTPS. That gate is permanently closed for every fm bench, so
    the env var never reaches a header at all. The bench nginx image, meanwhile, hardcodes
    `add_header Strict-Transport-Security` in every site's `listen 80` server block unconditionally,
    and nginx-proxy's `include /etc/nginx/vhost.d/<domain>` sits in SERVER context before any
    `location` block, so a directive written here applies to every response nginx-proxy sends for
    that domain, including ones proxied back from the bench -- which strips the bench's hardcoded
    header and, unless the operator asked for "off", replaces it with the configured one.

    `proxy_hide_header` is unconditional, "off" included, and NOT keyed on the scheme: the bench
    emits its hardcoded header on every response regardless of scheme, so hiding it must not become
    scheme-dependent either, or an operator who asked for no HSTS would still receive the bench's
    own two-year pin on an http:// response too.

    `add_header`, when added, IS keyed on `$fm_client_scheme` -- RFC 6797 requires a host not send
    this header over a non-secure connection, and this vhost's `server {}` block has both a
    `listen 80` and a `listen ... ssl` in it, so it answers both. Not `$https`: that is the PROXY's
    own connection, which is plain http for every fronted bench (a trusted peer terminates TLS and
    forwards plain HTTP), so keying on it left HSTS silently unsent behind any front.
    `$fm_client_scheme` is the visitor's real scheme -- forwarded-and-trusted, or the proxy's own
    connection otherwise -- always defined by fm's `fm-forwarded-trust.conf` (see `realip.py`), so
    this needs no `if` around the `add_header` itself -- an `if` inside a location is the classic
    nginx footgun; this one lives in server context, matching where nginx-proxy's own copy of the
    same idiom lives.
    """
    lines = ["proxy_hide_header Strict-Transport-Security;"]
    if value.strip().lower() != "off":
        lines += [
            'set $fm_hsts_value "";',
            "if ($fm_client_scheme = https) {",
            f'    set $fm_hsts_value "{value}";',
            "}",
            "add_header Strict-Transport-Security $fm_hsts_value always;",
        ]
    return "\n".join(lines) + "\n"
