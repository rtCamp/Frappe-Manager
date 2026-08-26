"""Generate the annotated example config files from the pydantic models.

Run with `just config-example`. The output is committed, and
`tests/unit/scripts/test_config_example.py` regenerates it and fails on any difference, so a field
added, renamed or re-described without regenerating breaks the build.

That gate is the whole point. `frappe_manager/templates/bench_config.toml` was a hand-written example
that nothing generated, nothing checked and nothing read, so it drifted through two schema redesigns
without anyone noticing, and its last accurate statement was several releases old. Deriving the
example from `Field(description=...)` means the schema documents itself and the documentation cannot
be wrong about a key's name, its default, or whether fm writes it at all.
"""

import sys
import textwrap
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import (
    NOT_WRITTEN_TO_DISK,
    AppConfig,
    BenchConfig,
)
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate

WRAP = 96

# Fields on the global config that describe fm's own state rather than anything a reader sets.
GLOBAL_NOT_WRITTEN = frozenset({"root_path", "version", "dns_providers"})

# Written to disk, but by fm and only by fm. Listing them as configurable would invite hand edits of
# fm's own bookkeeping, which is how a bench ends up claiming a migration it never ran.
FM_OWNED = frozenset({"migration_state", "deploy_state"})


def _unwrap_optional(annotation: Any) -> Any:
    """The inner type of `X | None`, or the annotation unchanged."""
    if get_origin(annotation) in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel this annotation resolves to, for `X`, `X | None` and `dict[str, X]`."""
    inner = _unwrap_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    if get_origin(inner) is dict:
        value = get_args(inner)[1] if len(get_args(inner)) == 2 else None
        if isinstance(value, type) and issubclass(value, BaseModel):
            return value
    if get_origin(inner) is list:
        item = get_args(inner)[0] if get_args(inner) else None
        if isinstance(item, type) and issubclass(item, BaseModel):
            return item
    return None


def _comment(text: str, indent: str = "") -> list[str]:
    return [f"{indent}# {line}" for line in textwrap.wrap(text, WRAP - len(indent))]


def _default_note(field: FieldInfo) -> str:
    if field.is_required():
        return "required"
    default = field.get_default(call_default_factory=True)
    if default is None:
        return "optional, unset by default"
    if default in ("", [], {}):
        return "optional, empty by default"
    return f"default: {default.value if hasattr(default, 'value') else default!r}"


def _scalar_lines(name: str, field: FieldInfo, indent: str = "") -> list[str]:
    lines = _comment(f"{field.description or 'No description.'} [{_default_note(field)}]", indent)
    lines.append(f"{indent}# {name} = ...")
    return lines


def _table_block(header: str, model: type[BaseModel], intro: str | None = None) -> list[str]:
    lines: list[str] = []
    if intro:
        lines += _comment(intro)
    doc = (model.__doc__ or "").strip().splitlines()
    if doc and not intro:
        lines += _comment(doc[0])
    lines.append(f"# [{header}]")
    for name, field in model.model_fields.items():
        if field.exclude:
            continue
        nested = _model_of(field.annotation)
        if nested is not None:
            lines.append("#")
            lines += _table_block(f"{header}.{name}", nested)
            continue
        lines += _scalar_lines(name, field, indent="")
    return lines


def bench_config_example() -> str:
    lines = [
        "# bench_config.toml, every key fm reads, generated from the models by",
        "# `just config-example`. Committed and drift-tested, so it cannot fall behind the schema.",
        "#",
        "# This is a REFERENCE, not a starting file: fm writes bench_config.toml itself when you run",
        "# `fm create`. Everything below is commented out. Copy a line, uncomment it, set a value.",
        "# Comments you add to a real bench_config.toml are preserved when fm saves it.",
        "#",
        "# fm's own bookkeeping tables are omitted: [migration_state] and [deploy_state] are written",
        "# by fm and are not settings.",
        "",
        "# ---- top-level keys " + "-" * (WRAP - 21),
        "",
    ]

    scalars, tables = [], []
    for name, field in BenchConfig.model_fields.items():
        if name in NOT_WRITTEN_TO_DISK or name in FM_OWNED or field.exclude:
            continue
        (tables if _model_of(field.annotation) is not None else scalars).append((name, field))

    lines += _comment("The bench's primary domain, and the directory name under ~/frappe/sites. [required]")
    lines.append("# name = ...")
    lines += _comment("Environment: dev or prod. Written as `environment`. [required]")
    lines.append("# environment = ...")
    for name, field in scalars:
        if name == "name":
            continue
        lines += _scalar_lines(name, field)

    for name, field in tables:
        model = _model_of(field.annotation)
        assert model is not None
        keyed = get_origin(_unwrap_optional(field.annotation)) is dict
        header = f"{name}.<key>" if keyed else name
        lines += ["", "# ---- [" + header + "] " + "-" * max(0, WRAP - 12 - len(header)), ""]
        lines += _table_block(header, model, intro=field.description)

    lines += ["", "# ---- [ssl] " + "-" * (WRAP - 13), ""]
    lines += _comment(
        "DNS-01 credential sets, keyed by a free-form label. The set labelled 'cloudflare' is the "
        "default a certificate gets when it names no label. The same table exists in "
        "~/frappe/fm_config.toml for credentials shared by every bench."
    )
    lines += _table_block("ssl.dns_providers.<label>", DNSProviderConfig)
    lines += ["#"]
    lines += _comment(
        "One entry per domain, written by `fm ssl add`. A certificate never holds a credential; it "
        "names one with dns_provider."
    )
    lines.append("# [[ssl.certificates]]")
    for name, field in LetsencryptSSLCertificate.model_fields.items():
        if field.exclude:
            continue
        lines += _scalar_lines(name, field)

    lines += ["", "# ---- [[apps]] " + "-" * (WRAP - 15), ""]
    lines += _comment("Apps installed in this bench, in install order.")
    lines.append("# [[apps]]")
    for name, field in AppConfig.model_fields.items():
        if field.exclude:
            continue
        nested = _model_of(field.annotation)
        if nested is not None:
            lines.append("#")
            lines += _table_block(f"apps.{name}", nested)
            continue
        lines += _scalar_lines(name, field)

    return "\n".join(lines).rstrip() + "\n"


def fm_config_example() -> str:
    lines = [
        "# ~/frappe/fm_config.toml, fm's own settings, generated from the models by",
        "# `just config-example`. Committed and drift-tested, so it cannot fall behind the schema.",
        "#",
        "# fm writes this file itself. Everything below is commented out; comments you add are kept.",
        "",
    ]
    scalars, tables = [], []
    for name, field in FMConfigManager.model_fields.items():
        if name in GLOBAL_NOT_WRITTEN or field.exclude:
            continue
        (tables if _model_of(field.annotation) is not None else scalars).append((name, field))

    for name, field in scalars:
        lines += _scalar_lines(name, field)

    for name, field in tables:
        model = _model_of(field.annotation)
        assert model is not None
        lines += ["", "# ---- [" + name + "] " + "-" * max(0, WRAP - 12 - len(name)), ""]
        lines += _table_block(name, model, intro=field.description)

    lines += ["", "# ---- [ssl] " + "-" * (WRAP - 13), ""]
    lines += _comment(
        "DNS-01 credential sets shared by every bench on this host, keyed by a free-form label. A "
        "bench-level set with the same label wins. Bind a certificate to one with "
        "`fm ssl add <bench> <domain> --dns-provider <label>`."
    )
    lines += _table_block("ssl.dns_providers.<label>", DNSProviderConfig)
    return "\n".join(lines).rstrip() + "\n"


TARGETS = {
    Path("docs/reference/bench_config.example.toml"): bench_config_example,
    Path("docs/reference/fm_config.example.toml"): fm_config_example,
}


def main() -> int:
    for relative, build in TARGETS.items():
        path = REPO_ROOT / relative
        path.write_text(build())
        print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
