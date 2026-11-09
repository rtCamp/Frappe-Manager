"""Content for the proxy's `upstream-retry` fragment (see `ProxyDropins`).

Pure content function: no classes, no file IO, matching ssl_manager/proxy_conf.py and
site_manager/modules/upload_limit.py for the other per-domain concerns ProxyDropins owns.
"""


def upstream_retry_conf() -> str:
    """Per-domain body that turns a connect failure into an in-request retry on a live sibling.

    A rolling swap removes a replica's address from Docker DNS, but nginx-proxy's own template
    carries no connect timeout of its own, so it inherits nginx's 60s default: a request that
    lands on the just-removed address hangs in `connect()` for a minute instead of failing over,
    which is the `000` a rolling swap drops. `proxy_connect_timeout 1s` bounds that wait to well
    under a swap's own settle window, and `proxy_next_upstream error timeout` retries the failed
    connect against another server in the SAME upstream block, inside the SAME request -- no
    shared state across nginx-proxy's worker processes required, because the decision is made
    once, synchronously, while the request is still being served (unlike a passive `zone`-based
    health check, which only prevents a FUTURE request from choosing the same dead address).

    `non_idempotent` is deliberately omitted: nginx only withholds a retry for a non-idempotent
    method (POST, PATCH, LOCK, ...) once the request has actually been sent to a backend, so a
    pure connect failure -- nothing written yet -- already retries safely without it. Adding it
    would also retry a request that DID reach a backend and then timed out waiting on the
    response, where the backend may already have applied the write; blindly retrying that is a
    worse failure than the one this fragment fixes.
    """
    return "proxy_connect_timeout 1s;\nproxy_next_upstream error timeout;\n"
