"""Upload-limit content and domain-selection policy for the proxy's `client_max_body_size`.

Writing/removal mechanics live in `ProxyDropins`; this module only decides WHAT to write and
WHICH domains get their own fragment.
"""

import re
from pathlib import Path

_CLIENT_MAX_BODY_SIZE_RE = re.compile(r"^[ \t]*client_max_body_size[ \t]+(\S+);[ \t]*\n?", re.MULTILINE)


def upload_limit_conf(size: str) -> str:
    """The fragment body for `size` (nginx format, e.g. "50m", "1g")."""
    return f"client_max_body_size {size.lower()};\n"


def claim_foreign_upload_limit(vhost_file: Path) -> str | None:
    """Remove a foreign `client_max_body_size` directive from `vhost_file`, returning its value.

    A second `client_max_body_size` in the same nginx server context is FATAL -- "directive is
    duplicate" -- even when the two copies arrive through different `include`d files, because
    `include` does not open a new context. fm's own upload-limit fragment is included into every
    domain's server block, so an operator's hand-written copy in this shared `vhostd/<domain>`
    file and fm's fragment can never coexist: leaving both would pass this write cleanly and only
    fail on the NEXT proxy reload, taking down every bench on the host, not just this domain.
    Removing it here -- and the caller warning so the value is not silently lost -- is the only
    outcome that is both loadable and lossless; everything else in the file, including the
    `# fm:include` bootstrap block, is left untouched.
    """
    if not vhost_file.is_file():
        return None
    text = vhost_file.read_text()
    match = _CLIENT_MAX_BODY_SIZE_RE.search(text)
    if match is None:
        return None
    vhost_file.write_text(text[: match.start()] + text[match.end() :])
    return match.group(1)


def domains_needing_upload_limit(domains: list[str]) -> list[str]:
    """Domains that need their own upload-limit fragment.

    A `*.example.com` wildcard entry covers every non-wildcard subdomain of that base via nginx's
    own matching, so writing a fragment for the subdomain too would duplicate the directive:
    skipped here, not at the `ProxyDropins` layer, which has no notion of wildcard overlap.
    """
    wildcards = [d for d in domains if d.startswith("*.")]
    non_wildcards = [d for d in domains if not d.startswith("*.")]

    result = list(wildcards)
    for domain in non_wildcards:
        matches_wildcard = False
        for wildcard in wildcards:
            wildcard_base = wildcard[2:]
            if domain.endswith(wildcard_base) and domain != wildcard_base:
                matches_wildcard = True
                break
        if not matches_wildcard:
            result.append(domain)

    return result
