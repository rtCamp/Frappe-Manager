"""Collect keys a config model does not recognise, and read a field without letting one masquerade
as another -- the write-side and read-side halves of the same `extra="allow"` hazard.

The models `import_from_toml` splats a TOML table into (bench-side and SSL config, see
`site_manager/bench_config.py` and `ssl_manager/certificate.py`/`dns_provider.py`) are
`extra="allow"`. An unknown key inside one of them is therefore never a load-time error any more:
pydantic keeps it in `BaseModel.model_extra` instead of raising. Something still has to surface it
though, so a caller can refuse it on a write path or warn about it on a plain read -- that decision
is deliberately NOT made here (see the design note in the flipped models' `model_config` comments).
`collect_unknown_keys` finds those keys and names their location; `declared_field` is the other
half, for a caller reading a field a stray could shadow (see its own docstring). `unwrap_toml_value`
is a third, unrelated guard that lives here for the same reason: it strips the tomlkit `Item`
wrapper off a value on its way into one of the retained-extra dicts the two readers build by hand
(see its own docstring for why that wrapper cannot be left on). `declared_field` started as a
one-file helper in `ssl_manager/certificate.py` and moved here once a second, unrelated subsystem
(`site_manager/hooks.py`) needed the identical guard, which is exactly the shared-utility shape
this module already exists for.

Lives under `frappe_manager.utils` rather than next to either family of models it walks: it is
needed by `site_manager/bench_config.py` today and by `metadata_manager.py` in a later phase, and
those two modules do not import each other (`bench_config.py` cannot import from
`metadata_manager.py`: `dns_provider.py`'s own module docstring records that direction as the one
with no circular import). A home in either model module would force the walk to import model
classes it should never need to know about; `frappe_manager.utils` already has no dependency on
either and both already import from it, so this adds no new edge to the import graph.

The recursion is entirely runtime-type driven -- it looks at what a field actually holds, never at
its declared annotation -- which is what makes it skip free-form regions on its own, without a
naming convention or an exclude list. A `dict[str, Any]` field like `switch.common_site_config`
holds plain values (strings, ints, nested plain dicts), never `BaseModel` instances, so the walk
never descends into it: there is no schema on the other side to check its keys against. The same
is true of `deploy_state.history[].backups` (`dict[str, str]`, keyed by site name) and of a
`dict[str, str]` like `[output.colors]` in the global config, whose keys are rich style tokens that
already CONTAIN dots (e.g. `'fm.env.prod'`). That last shape is exactly why paths are built by
joining known segments as the walk descends, never by splitting a key string: a naive path-splitter
would mistake `'fm.env.prod'` for three path segments instead of one opaque dict key, and it would
do so for a key that was never even visited, because a `dict[str, str]` value has nothing under it
for the walk to reach.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def collect_unknown_keys(model: BaseModel) -> list[str]:
    """Every key pydantic retained under `extra="allow"` anywhere inside `model`.

    Each result is a dotted path from `model`'s own root to the unknown key, e.g.
    `"switch.hooks.host.before_restrat"` or `"sites.shop.database.prot"`. A `dict[str, Model]`
    field (`[sites."<name>"]`, `[ssl.dns_providers."<label>"]`) uses the user's own table key as
    the path segment, since that key is meaningful to them and is never itself in question --
    only keys found INSIDE that entry are unknown.

    A `list[Model]` field uses the element's index (`ssl_certificates[0]`), not an identity drawn
    from one of its fields. An identity would have to be picked per model (`domain` for a
    certificate, `name` for an app, and `history` entries have no field that is unique across
    retries or rollbacks), which would make this walk special-case the very models it is meant to
    stay ignorant of. The index is always available, is stable for the message's lifetime (one
    validated load), and reads fine paired with the field name.

    Returns a sorted, deduplicated list; the same key can otherwise appear twice, e.g. once from
    `model_extra` and once by a caller union-ing results from two related models.
    """
    found: set[str] = set()
    _walk(model, "", found)
    return sorted(found)


def _walk(value: Any, prefix: str, found: set[str]) -> None:
    if isinstance(value, BaseModel):
        extra = value.model_extra
        if extra:
            for key in extra:
                found.add(f"{prefix}.{key}" if prefix else str(key))
        # Class-level access (not `value.model_fields`): pydantic 2.11+ deprecates the instance
        # form and warns on every call.
        for field_name in type(value).model_fields:
            _walk(getattr(value, field_name), f"{prefix}.{field_name}" if prefix else field_name, found)
    elif isinstance(value, dict):
        for key, item in value.items():
            # Only a `Model` value has a schema to check; a plain value (str, int, nested plain
            # dict/list) is free-form data the user is entitled to shape however they like.
            if isinstance(item, BaseModel):
                _walk(item, f"{prefix}.{key}" if prefix else str(key), found)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            if isinstance(item, BaseModel):
                _walk(item, f"{prefix}[{index}]", found)
    # Scalars, None, enums, and free-form containers whose items are not models: nothing to walk.


def declared_field(model: BaseModel, name: str, default: Any = None) -> Any:
    """`model.<name>` if `model`'s CONCRETE type declares that field, else `default`.

    A capability probe against an `extra="allow"` model cannot use a plain `getattr(obj, name,
    default)`: `BaseModel.__getattr__` resolves `__pydantic_extra__` for any name pydantic did not
    recognise on THIS type, so a stray key that merely shares a name with a field declared on a
    DIFFERENT sibling type reads back through plain `getattr` as if it were that real, validated
    field -- e.g. a `dns_provider` stray retained on a `DevCertificate`, which declares no such
    field (only `LetsencryptSSLCertificate` does), or a `host` stray retained on a bare
    `BuildHookScripts`, when only its subclass `AppBuildHooks` declares `host`. That turns "probe
    whether this model is capable of X" into "read whatever the user happened to type", silently --
    and when the read value becomes a hook script or a file path fed to a subprocess rather than a
    display string, that silence is a code-execution / arbitrary-read hazard, not just a wrong
    answer.

    Checking `type(model).model_fields` first asks the TYPE whether the field is real for this
    concrete class before the instance is ever read, so the stray stays retained (tolerating an
    unrecognised key at load, never raising on it, is the whole point of `extra="allow"`) without
    ever becoming live input to a probe.
    """
    if name in type(model).model_fields:
        return getattr(model, name)
    return default


def unwrap_toml_value(value: Any) -> Any:
    """`value` as a plain Python object, never a tomlkit-internal `Item`.

    Guards a retained remainder specifically -- the dict a reader builds by hand from a set of
    key names `collect_unknown_keys` (or a hand-read table with no model of its own, like `[ssl]`)
    found at runtime, not a value a typed field already coerces on its own. A retained key has no
    declared field to coerce it the way `str`/`int`/a nested model field would, so whatever
    `import_from_toml` read off the parsed `tomlkit` document is what the key keeps verbatim, and
    that value has to survive `model_dump()` on this load and a later `tomlkit.dumps()` on the
    next export unchanged. Leaving a `tomlkit.items.Item` (`String`, `Table`, `AoT`, `DateTime`,
    ...) in an `extra="allow"` model happens to round-trip today, because tomlkit's `Item` types
    subclass their plain equivalents -- but that is a coincidence of tomlkit's own class hierarchy,
    not a guarantee this module controls, and a raw `Item` can also carry document-attachment
    state (e.g. a comment, a source position) that pydantic never asked for and a fresh export
    should not inherit. Calling this at the point a remainder is built removes the dependency on
    that coincidence for every value it touches, instead of hoping it keeps holding.

    Called at every hand-built retained-extra dict: in `site_manager/bench_config.py`, the
    top-level remainder, the `[ssl]` hand-read remainder, the `[deploy_state]` remainder, and the
    per-site remainder; in `metadata_manager.py`, the top-level remainder and the pre-0.20.0
    `[cloudflare]` legacy splat. It is NOT called on a splat into a typed field
    (`AuthConfig(**dict(...))`, `AppConfig(**dict(...))`, and similar elsewhere in
    `bench_config.py`): pydantic's own field coercion already handles those, so wrapping them
    here would be redundant, not protective. A future reader must NOT assume every retained extra
    reaches its model through one of the six call sites named above: a new hand-built
    retained-extra dict needs its own explicit call here, not an assumption that some existing
    splat already covers it.
    """
    return value.unwrap() if hasattr(value, "unwrap") else value
