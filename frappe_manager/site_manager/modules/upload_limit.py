"""Upload-limit content and domain-selection policy for the proxy's `client_max_body_size`.

Writing/removal mechanics live in `ProxyDropins`; this module only decides WHAT to write and
WHICH domains get their own fragment.
"""


def upload_limit_conf(size: str) -> str:
    """The fragment body for `size` (nginx format, e.g. "50m", "1g")."""
    return f"client_max_body_size {size.lower()};\n"


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
