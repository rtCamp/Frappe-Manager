"""HTTP basic auth conf rendering for the bench nginx (ngx_http_auth_basic_module).

One credential pair per bench, two independent surfaces:

- ``web``: ``auth_basic`` at server level, so every location inherits it (frappe,
  socketio, ``/api/*``). The ACME challenge location opts out inside the image
  template, so certificate renewal keeps working.
- ``tools``: the admin tools locations (``/adminer/``, ``/mailpit/``) only, which
  either carry the directives themselves or opt out of an inherited server-level
  gate with ``auth_basic off``.

Both are enforced by the bench nginx, the only route into a bench (it publishes no
host ports), against one htpasswd file written host-side with passlib because the
nginx image ships no ``htpasswd`` binary.
"""

import re
from pathlib import Path

_MARKER = "# fm:auth"

REALM = "Restricted"

# Server-context include (custom/*.conf) and the http-context map that backs
# --allow-path. The map file is named to sort before default.conf so the variable
# is registered before the server block that reads it.
SERVER_CONF_NAME = "auth.conf"
MAP_CONF_NAME = "00-fm-auth-map.conf"

_REALM_VAR = "$fm_auth_realm"
_IP_EXEMPT_VAR = "$fm_auth_ip_exempt"
_PATH_EXEMPT_VAR = "$fm_auth_path_exempt"


def site_var_suffix(site: str) -> str:
    """A legal nginx variable suffix identifying one site.

    nginx variable names are ``[A-Za-z0-9_]`` and a site name is a hostname full of dots, so the
    name cannot be used directly. A slug alone is not enough either: ``a.b`` and ``a-b`` both slug
    to ``a_b``, and two sites sharing a realm variable would silently share one prompt. The exact
    name is hashed in, so the suffix is unique to the site whatever its punctuation.
    """
    import hashlib

    slug = re.sub(r"[^a-z0-9]+", "_", site.lower()).strip("_")
    digest = hashlib.blake2s(site.encode(), digest_size=3).hexdigest()
    return f"{slug}_{digest}"


def auth_vars(suffix: str = "") -> tuple[str, str, str]:
    """The (realm, ip-exempt, path-exempt) variable names for one auth scope.

    An empty suffix is the BENCH's scope and keeps the names byte-identical to what every existing
    bench already has on disk, so a bench-wide `fm auth` renders exactly what it rendered before.

    A site needs its own set because ``geo`` and ``map`` are http context: every site of a bench
    shares one map file, so a single ``$fm_auth_realm`` cannot hold two sites' values. One set per
    scope is what makes per-site prompts expressible at all.
    """
    if not suffix:
        return _REALM_VAR, _IP_EXEMPT_VAR, _PATH_EXEMPT_VAR
    return f"{_REALM_VAR}_{suffix}", f"{_IP_EXEMPT_VAR}_{suffix}", f"{_PATH_EXEMPT_VAR}_{suffix}"


def site_htpasswd_name(bench_name: str, site: str) -> str:
    """Basename of a site's own htpasswd file.

    Its own, not the bench's: per-site auth carries per-site credentials, so a password handed out
    for one site is not a password to another. The bench's file keeps its existing name so a
    bench-wide `fm auth` is unchanged.
    """
    return f"{bench_name}-{site}.htpasswd"


def container_site_htpasswd_path(bench_name: str, site: str) -> str:
    """Where a site's htpasswd appears inside the container."""
    return f"/etc/nginx/http_auth/{site_htpasswd_name(bench_name, site)}"


def htpasswd_name(bench_name: str) -> str:
    """Basename of the bench's single htpasswd file, shared by both surfaces."""
    return f"{bench_name}.htpasswd"


def container_htpasswd_path(bench_name: str) -> str:
    """Where that file appears inside the container (configs/nginx/conf is /etc/nginx)."""
    return f"/etc/nginx/http_auth/{htpasswd_name(bench_name)}"


def generate_password() -> str:
    import secrets

    return secrets.token_urlsafe(16)


def validate_credentials(user: str, password: str) -> None:
    """Raise ValueError naming the offending field.

    ``:`` separates user from hash in the htpasswd file and user from password in the
    ``Authorization`` header, so it cannot appear in a username; it is legal inside a
    password.
    """
    if not user:
        raise ValueError("username cannot be empty")
    if ":" in user:
        raise ValueError("username cannot contain ':'")
    if any(c.isspace() for c in user):
        raise ValueError("username cannot contain whitespace")
    if not password:
        raise ValueError("password cannot be empty")
    if not password.strip():
        raise ValueError("password cannot be whitespace only")


def _auth_basic_line(realm: str, indent: str) -> str:
    """A variable realm is passed bare; a literal one is quoted."""
    if realm.startswith("$"):
        return f"{indent}auth_basic {realm};"
    return f'{indent}auth_basic "{realm}";'


def _access_lines(allow_ips: list[str], indent: str) -> list[str]:
    """``satisfy any`` plus an allow list, so listed addresses skip the prompt.

    The trailing ``deny all`` is required: without it every address satisfies the
    access module and the password is never asked for at all.
    """
    if not allow_ips:
        return []
    lines = [f"{indent}satisfy any;"]
    lines += [f"{indent}allow {ip};" for ip in allow_ips]
    lines.append(f"{indent}deny all;")
    return lines


def build_auth_directives(auth_file: str, allow_ips: list[str], realm: str = REALM, indent: str = "") -> str:
    """The directive group that actually gates a context."""
    lines = _access_lines(allow_ips, indent)
    lines.append(_auth_basic_line(realm, indent))
    lines.append(f"{indent}auth_basic_user_file {auth_file};")
    return "\n".join(lines) + "\n"


def build_server_auth_conf(
    auth_file: str,
    allow_ips: list[str] | None = None,
    allow_paths: list[str] | None = None,
    realm: str = REALM,
    suffix: str = "",
) -> str:
    """Server-context conf gating the web surface; every location inherits it.

    Once any path is exempt the IP exemption moves out of the access module and into
    the realm map as well (see ``build_auth_map_conf``): ``satisfy any; ...; deny
    all;`` records a 403 before ``auth_basic`` runs, and a realm of ``off`` makes the
    auth_basic handler DECLINE rather than return OK, so nothing ever clears the
    recorded refusal and the exempt path would answer 403 to every client outside the
    allow list.

    ``suffix`` scopes the realm variable to one site (empty is the bench's); it must match the
    suffix the map conf was built with, or the server block reads a variable nobody defined and
    nginx refuses to start.
    """
    realm_var, _, _ = auth_vars(suffix)
    if allow_paths:
        return f"{_MARKER} generated by `fm auth`; do not edit\n" + build_auth_directives(auth_file, [], realm_var)
    return f"{_MARKER} generated by `fm auth`; do not edit\n" + build_auth_directives(auth_file, allow_ips or [], realm)


def build_auth_map_conf(
    allow_paths: list[str],
    allow_ips: list[str] | None = None,
    realm: str = REALM,
    suffix: str = "",
) -> str:
    """http-context maps turning the realm into the literal ``off`` for exempt requests.

    ``auth_basic`` accepts a variable and the value ``off`` disables it, which is how
    a path is exempted without redeclaring its location: a bare
    ``location /x { auth_basic off; }`` would inherit no ``proxy_pass`` and 404.
    Matching is on ``$uri`` (normalized, query string excluded) as a prefix.

    ``allow_ips`` is folded in here too, as a ``geo`` block (the only directive that
    matches a CIDR against a variable, and http-context only), because the access
    module cannot express the two exemptions together: a ``deny all`` refusal is
    recorded before ``auth_basic`` runs and an ``off`` realm never clears it. The
    combining map is keyed on the two flags concatenated, so any ``1`` -- exempt
    address OR exempt path -- disables the prompt, and the default stays the realm.

    ``suffix`` scopes the three variables to one site. Every scope's blocks land in the SAME file
    (http context is shared by every server block), so the names must not collide: that is what
    :func:`site_var_suffix` guarantees.
    """
    realm_var, ip_var, path_var = auth_vars(suffix)
    lines = [f"{_MARKER} generated by `fm auth`; do not edit"]
    if allow_ips:
        lines.append(f"geo {ip_var} {{")
        lines.append("    default 0;")
        lines += [f"    {ip} 1;" for ip in allow_ips]
        lines.append("}")
        lines.append(f"map $uri {path_var} {{")
        lines.append("    default 0;")
        lines += [f"    ~^{re.escape(path)} 1;" for path in allow_paths]
        lines.append("}")
        lines.append(f'map "{ip_var}{path_var}" {realm_var} {{')
        lines.append(f'    default "{realm}";')
        lines.append('    "~1" off;')
        lines.append("}")
    else:
        lines.append(f"map $uri {realm_var} {{")
        lines.append(f'    default "{realm}";')
        lines += [f"    ~^{re.escape(path)} off;" for path in allow_paths]
        lines.append("}")
    return "\n".join(lines) + "\n"


def build_tools_auth_block(
    web: bool,
    tools: bool,
    auth_file: str,
    allow_ips: list[str] | None = None,
    realm: str = REALM,
    indent: str = "    ",
) -> str:
    """Directives for each admin tools location, given the state of both surfaces.

    ``tools`` on: the locations carry the bench's gate themselves, ALWAYS, even when the web
    surface is gated too. Inheriting was sound only while one credential pair served both
    surfaces; per-site web auth broke that, and a site with its own password would otherwise have
    had that password open the bench-wide Adminer, which reaches every schema on the bench. A
    location-level ``auth_basic`` overrides the server-level one, so naming the bench's file here
    keeps the tools on bench credentials whatever a site does. It also stops a web-surface
    ``allow_paths`` prefix exempting ``/adminer/``, which the key was never meant to reach.

    ``web`` only: the locations opt out of the inherited gate -- of both halves of it: an inherited
    ``satisfy any; allow <ip>; deny all;`` would otherwise still 403 every client outside the allow
    list, because ``auth_basic off`` makes the auth handler DECLINE rather than clear the recorded
    refusal. A location-level ``allow all`` replaces the inherited rules and returns OK, which does
    clear it. Neither on: nothing.
    """
    if tools:
        return build_auth_directives(auth_file, allow_ips or [], realm, indent)
    if web:
        return f"{indent}auth_basic off;\n{indent}allow all;\n"
    return ""


def write_htpasswd(path: Path, user: str, password: str) -> bool:
    """Write the single-user htpasswd, returning True only when it changed.

    The desired credentials are verified against the existing file rather than
    byte-compared: the hash is salted, so comparing content would rewrite the file
    (and reload nginx) on every run. ``new=True`` truncates, which is what drops a
    renamed-away user instead of leaving them able to log in.
    """
    from passlib.apache import HtpasswdFile

    if path.exists():
        try:
            existing = HtpasswdFile(str(path))
            if existing.users() == [user] and existing.check_password(user, password):
                return False
        except Exception:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)
    ht = HtpasswdFile(str(path), new=True)
    ht.set_password(user, password)
    ht.save()
    return True


def is_fm_auth_conf(text: str) -> bool:
    return text.startswith(_MARKER)
