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

import os
import tempfile
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


def apply(doc: Any, desired: Mapping[str, Any], *, keep: frozenset[str] = frozenset()) -> None:
    """Make `doc` say exactly what `desired` says, in place, preserving everything else.

    Recurses into tables so a comment inside one survives a change to its neighbour. Anything that
    is not a mapping, notably the `[[ssl.certificates]]` array, is replaced wholesale: its entries
    are positional, so there is no stable identity to merge a comment against.

    `keep` names top-level keys fm READS as input but never writes back. Without it the prune below
    deletes them: a `[[apps]]` array that `fm bake --config` persisted, or an `admin_pass` recorded
    by an older fm, survived exactly one command and then vanished on an unrelated save. Keys in
    `keep` are left alone when present and never synthesised when absent.
    """
    for key in [key for key in doc if key not in desired and key not in keep]:
        del doc[key]

    for key, value in desired.items():
        current = doc.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            apply(current, value)
        else:
            doc[key] = value


def save_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically, at mode 0600.

    `open(path, "w")` truncates before the content is even serialised, so a serialisation error, a
    full disk, or a Ctrl-C (a BaseException, which no `except Exception` here catches) left the ONLY
    copy of the config empty: a 425-byte bench_config.toml became 0 bytes, no longer parsed, and had
    no backup beside it. An empty fm_config.toml breaks every fm command on the host, so that is not
    a recoverable state to leave a user in.

    The temp file goes in the SAME directory, so `os.replace` is a rename within one filesystem and
    therefore atomic: a reader sees either the old file or the new one, never a partial one. mkstemp
    creates it 0600, rather than chmod'ing after the secrets are already on disk, so the DNS tokens,
    GitHub token and basic-auth password are never briefly readable at the process umask. Every
    config writer goes through here for that reason: the raw `write_text` calls in the migrations
    left real hosts sitting at 664 with a Cloudflare token in the file.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def save(path: Path, doc: TOMLDocument) -> None:
    """Serialise `doc` and write it to `path` atomically, at mode 0600.

    Serialising first matters: it happens while nothing has been touched, so a `tomlkit` failure
    cannot leave the file damaged.
    """
    save_text(path, tomlkit.dumps(doc))
