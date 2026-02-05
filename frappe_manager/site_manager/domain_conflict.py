from pathlib import Path

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_config import BenchConfig


class DomainConflict:
    def __init__(self, domain: str, owner_bench: str, is_primary: bool = False):
        self.domain = domain
        self.owner_bench = owner_bench
        self.is_primary = is_primary

    def __str__(self):
        type_str = "primary domain" if self.is_primary else "alias"
        return f"'{self.domain}' → already used as {type_str} by bench '{self.owner_bench}'"


class DomainConflictError(Exception):
    def __init__(self, conflicts: list[DomainConflict]):
        self.conflicts = conflicts
        conflict_msgs = "\n  - ".join(str(c) for c in conflicts)
        super().__init__(f"Domain conflicts detected:\n  - {conflict_msgs}")


def build_global_domain_map(
    benches_root: Path = CLI_BENCHES_DIRECTORY, exclude_bench: str | None = None,
) -> dict[str, tuple[str, bool]]:
    domain_map = {}

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
            bench_name = config.name

            domain_map[config.name.lower()] = (bench_name, True)

            for alias in config.alias_domains or []:
                domain_map[alias.lower()] = (bench_name, False)

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
            owner_bench, is_primary = domain_map[normalized]
            conflicts.append(DomainConflict(domain, owner_bench, is_primary))

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
