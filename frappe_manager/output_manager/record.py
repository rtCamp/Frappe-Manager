"""What a command emits, before anybody decides how it looks."""

from dataclasses import dataclass, field
from typing import Any

# A card declares a secret where it builds one, by wrapping the value in the `fm.secret` theme
# token. That marker is the field name this sink masks by: it survives rewording, it is already
# what the terminal colours, and it needs no guess about what a value looks like.
SECRET_MARKUP = "fm.secret"  # noqa: S105 -- a theme token, not a credential

# The label list is the second net, for rows that name a secret without colouring one. A label is
# written for a human ("db password"), so matching is a lowercased substring: over-masking costs a
# reader one lookup, under-masking costs a rotation.
SECRET_LABELS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api key",
    "api_key",
    "license",
    "encryption key",
)

MASK = "****"


def is_secret_label(label: str) -> bool:
    lowered = label.lower()
    return any(marker in lowered for marker in SECRET_LABELS)


def mask_value(value: str) -> str:
    """Replace every `fm.secret`-marked span, leaving the rest of the row intact.

    The row reads `name / <secret>`, and only the marked half goes: a masked database NAME would
    make the log useless for the thing people read it for.
    """
    import re

    return re.sub(rf"\[{SECRET_MARKUP}\].*?\[/{SECRET_MARKUP}\]", MASK, value)


@dataclass(frozen=True)
class OutputRecord:
    """One thing a command said, as fields rather than as a rendering.

    Sinks receive this and decide what it means: the terminal draws it, the file writes it with
    secret-labelled fields masked, the event stream emits it as JSON. Handing sinks rendered text
    instead is what made a password unmaskable -- by then it was characters between a slash and a
    newline, matching no pattern.
    """

    kind: str
    text: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    data: Any = None
