"""
Drop-in fragment files for the shared nginx-proxy vhost.d files.

Before this, maintenance/https-redirect/hsts each owned a `# fm:<name> BEGIN/END` marked
region inside the single, shared `vhostd/<domain>` file, and upload-limit had no marker at
all -- it regexed for any `client_max_body_size`, so it matched and deleted an operator's own
directive the moment one existed. Marker-based writers also made ordering an accident of
whichever command last prepended its block, which demonstrably changes nginx's behaviour
(the first matching directive in a context wins for some, the last for others).

A fragment per concern removes both problems: enabling a concern is "write a file", disabling
is "unlink a file", ordering is the filename's numeric prefix (`ORDER`), and "is this concern
active" is "does its file exist" -- no text parsing of foreign content required.

Two traps this module exists to get right, both measured against a running nginx-proxy:

  * An UNESCAPED `*` in the include path globs every sibling domain's directory, not just this
    one -- `/etc/nginx/fm.d/vhost/*.example.com/*.conf` matches `foo.example.com` too, so one
    bench's fragments silently apply to another bench's domain. The bootstrap this module writes
    always escapes a wildcard domain's `*` to `\\*` before it reaches the include line.
  * nginx's `include` directive does NOT interpolate variables: `include .../$host/*.conf;`
    passes `nginx -t` without a single warning and then never matches a fragment at runtime,
    because `$host` is never expanded in an include path. The domain segment here is always the
    literal, already-resolved domain string -- never a variable reference.
"""

import re
from pathlib import Path

ORDER = {"upload-limit": 10, "maintenance": 20, "hsts": 30, "https-redirect": 40}

INCLUDE_BEGIN = "# fm:include BEGIN"
INCLUDE_END = "# fm:include END"
_INCLUDE_RE = re.compile(
    re.escape(INCLUDE_BEGIN) + r".*?" + re.escape(INCLUDE_END) + r"\n?", re.DOTALL
)


def _escape_domain(domain: str) -> str:
    return domain.replace("*", r"\*")


def _bootstrap(domain: str) -> str:
    return f"{INCLUDE_BEGIN}\ninclude /etc/nginx/fm.d/vhost/{_escape_domain(domain)}/*.conf;\n{INCLUDE_END}\n"


class ProxyDropins:
    """Owns `fmd/vhost/<domain>/<NN>-<name>.conf` fragments and the `vhostd/<domain>` bootstrap
    that includes them. Never reloads nginx -- a fragment has no effect until an explicit reload,
    and docker-gen does not watch `fmd/`, so callers batch their writes and reload once.
    """

    def __init__(self, vhostd_dir: Path, fmd_dir: Path) -> None:
        self.vhostd_dir = vhostd_dir
        self.fmd_dir = fmd_dir

    @classmethod
    def for_services_path(cls, services_path: Path) -> "ProxyDropins":
        nginx_proxy = services_path / "nginx-proxy"
        return cls(nginx_proxy / "vhostd", nginx_proxy / "fmd")

    def fragment_path(self, domain: str, name: str) -> Path:
        if name not in ORDER:
            raise ValueError(f"unregistered proxy drop-in concern: {name!r}")
        return self.fmd_dir / "vhost" / domain / f"{ORDER[name]:02d}-{name}.conf"

    def _ensure_bootstrap(self, domain: str) -> bool:
        vhost_file = self.vhostd_dir / domain
        desired = _bootstrap(domain)
        existing = vhost_file.read_text() if vhost_file.exists() else ""
        if existing.startswith(desired):
            return False
        remainder = _INCLUDE_RE.sub("", existing, count=1)
        self.vhostd_dir.mkdir(parents=True, exist_ok=True)
        vhost_file.write_text(desired + remainder)
        return True

    def set(self, domain: str, name: str, content: str) -> bool:
        path = self.fragment_path(domain, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = not path.exists() or path.read_text() != content
        if changed:
            path.write_text(content)
        bootstrap_changed = self._ensure_bootstrap(domain)
        return changed or bootstrap_changed

    def remove(self, domain: str, name: str) -> bool:
        path = self.fragment_path(domain, name)
        if not path.exists():
            return False
        path.unlink()

        domain_dir = path.parent
        if not any(domain_dir.iterdir()):
            domain_dir.rmdir()

            vhost_file = self.vhostd_dir / domain
            if vhost_file.exists():
                existing = vhost_file.read_text()
                remainder = _INCLUDE_RE.sub("", existing, count=1)
                # Truthiness of the exact remainder, not `.strip()`: whitespace a foreign
                # writer left in this shared file is still that writer's bytes, not ours to
                # discard just because it looks empty to a human.
                if remainder:
                    vhost_file.write_text(remainder)
                else:
                    vhost_file.unlink()
        return True

    def active(self, domain: str) -> list[str]:
        domain_dir = self.fmd_dir / "vhost" / domain
        try:
            present = {p.stem.split("-", 1)[1] for p in domain_dir.glob("*.conf")}
        except OSError:
            return []
        return [name for name, _ in sorted(ORDER.items(), key=lambda kv: kv[1]) if name in present]
