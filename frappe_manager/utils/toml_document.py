"""Writing a config file without throwing away what the reader wrote in it.

`export_to_toml` used to build a fresh `tomlkit.document()` from the model and overwrite the file,
so every comment in it was lost on the next save. The migration that carefully preserved comments
with a tomlkit round-trip had them deleted moments later by `set_bench_migration_version`, which
stamps the version through the model.

Applying the model onto the document already on disk keeps comments, key order and quote style,
while still making the file say exactly what the model says. The pruning half is what makes that
true: a key the model no longer produces is deleted, so retired keys and removed tables still
disappear, which is what the whole-file overwrite achieved by accident.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.exceptions import TOMLKitError


def load_or_new(path: Path) -> TOMLDocument:
    """The document at `path`, or an empty one when it is missing or unreadable.

    A file that no longer parses must not block writes: the previous implementation overwrote
    whatever was there, and refusing to save because of an unrelated syntax error somewhere in the
    file would be a worse failure than losing its comments.
    """
    try:
        return tomlkit.parse(path.read_text())
    except (OSError, ValueError, TOMLKitError):
        return tomlkit.document()


def apply(doc: Any, desired: Mapping[str, Any]) -> None:
    """Make `doc` say exactly what `desired` says, in place, preserving everything else.

    Recurses into tables so a comment inside one survives a change to its neighbour. Anything that
    is not a mapping, notably the `[[ssl.certificates]]` array, is replaced wholesale: its entries
    are positional, so there is no stable identity to merge a comment against.
    """
    for key in [key for key in doc if key not in desired]:
        del doc[key]

    for key, value in desired.items():
        current = doc.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            apply(current, value)
        else:
            doc[key] = value
