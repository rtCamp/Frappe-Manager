from pathlib import Path

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.site_manager.bench_config import BenchConfig


class DomainConflict:
    """A candidate domain that some other bench already serves.

    Carries the owning SITE as well as the bench, because a bench can serve several and "which
    bench has it" no longer locates the clash.
    """

    def __init__(self, domain: str, owner_bench: str, owner_site: str):
        self.domain = domain
        self.owner_bench = owner_bench
        self.owner_site = owner_site

    @property
    def is_site_name(self) -> bool:
        """True when the clash is with a site's own name rather than one of its aliases."""
        return self.domain.lower() == self.owner_site.lower()

    def __str__(self):
        role = "the domain of" if self.is_site_name else "an alias of"
        return f"'{self.domain}' → already {role} site '{self.owner_site}' in bench '{self.owner_bench}'"


class DomainConflictError(FrappeManagerException):
    def __init__(self, conflicts: list[DomainConflict]):
        self.conflicts = conflicts
        conflict_msgs = "\n  - ".join(str(c) for c in conflicts)
        super().__init__(f"Domain conflicts detected:\n  - {conflict_msgs}")


def build_global_domain_map(
    benches_root: Path = CLI_BENCHES_DIRECTORY,
    exclude_bench: str | None = None,
) -> dict[str, tuple[str, str]]:
    """Every domain any bench serves -> (bench, the site serving it).

    Built from `get_site_mappings`, which is the same table the nginx entrypoint routes by, so this
    check and the routing agree by construction. It used to register `config.name` plus a
    bench-level alias list, which left every non-primary site's own domain OUT of the map: two
    benches could each serve `b.example.com` with no conflict reported, and the clash surfaced as
    whichever container nginx happened to route to.
    """
    domain_map: dict[str, tuple[str, str]] = {}

    if not benches_root.exists():
        return domain_map

    for bench_dir in benches_root.iterdir():
        if not bench_dir.is_dir():
            continue

        if exclude_bench and bench_dir.name == exclude_bench:
            continue

        config_file = bench_dir / "bench_config.toml"
        if not config_file.exists():
            continue

        try:
            config = BenchConfig.import_from_toml(config_file)
            for domain, site in config.get_site_mappings().items():
                domain_map[domain.lower()] = (config.name, site)
        except Exception:
            continue

    return domain_map


def check_domain_conflicts(
    candidate_domains: set[str] | list[str],
    benches_root: Path = CLI_BENCHES_DIRECTORY,
    exclude_bench: str | None = None,
) -> list[DomainConflict]:
    domain_map = build_global_domain_map(benches_root, exclude_bench)

    conflicts = []
    for domain in candidate_domains:
        normalized = domain.lower()
        if normalized in domain_map:
            owner_bench, owner_site = domain_map[normalized]
            conflicts.append(DomainConflict(domain, owner_bench, owner_site))

    return conflicts


def validate_domains_unique(
    candidate_domains: set[str] | list[str],
    benches_root: Path = CLI_BENCHES_DIRECTORY,
    exclude_bench: str | None = None,
    skip_check: bool = False,
) -> None:
    if skip_check:
        return

    conflicts = check_domain_conflicts(candidate_domains, benches_root, exclude_bench)

    if conflicts:
        raise DomainConflictError(conflicts)
