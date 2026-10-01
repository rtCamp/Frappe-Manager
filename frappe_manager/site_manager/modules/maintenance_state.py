"""Reading which domains are in maintenance.

Lives here rather than in `commands/maintenance/` so `fm info` can report the same fact without a
command importing a command: `fm info` said `running` for a bench answering 503 to every visitor,
because the only code that knew was behind the CLI verb that writes it.

The marker is the contract. fm finds its own block by matching the literal, and unmarked content in
a shared `vhost.d/<domain>` file belongs to somebody else and is never touched, so the string here
and the one the writer emits must stay identical.
"""

from pathlib import Path

BLOCK_BEGIN_PREFIX = "# fm:maintenance BEGIN"


def has_fm_block(text: str) -> bool:
    """Whether fm's maintenance block is present in a vhost config."""
    return BLOCK_BEGIN_PREFIX in text


def domains_in_maintenance(vhostd_dir: Path, domains: list[str]) -> list[str]:
    """Those of ``domains`` whose vhost config currently carries fm's maintenance block.

    Reads the files rather than any recorded flag: the block in `vhost.d/<domain>` is what nginx
    actually serves from, so a config edited by hand or a half-finished enable is reported as it
    IS, not as fm last intended it.
    """
    if not vhostd_dir.exists():
        return []

    found: list[str] = []
    for domain in domains:
        conf = vhostd_dir / domain
        try:
            if conf.is_file() and has_fm_block(conf.read_text()):
                found.append(domain)
        except OSError:
            # An unreadable vhost file must not take down `fm info`, whose job is reporting.
            continue
    return found
