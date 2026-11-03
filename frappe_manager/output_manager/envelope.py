"""The one shape a `--json` command answers in, succeeding or failing.

A program asked for structured output. Returning a bare array when it works and prose when it
does not makes the caller parse two things, one of which is English -- and the failure is the
case it most needs to understand. So both answers carry the same keys, and `ok` says which
happened.

The version is here because these names are a contract the moment anything reads them: fields
may be added, and no field already emitted may be renamed or change meaning.
"""

from typing import Any

SCHEMA_VERSION = 1


def json_envelope(result: Any) -> dict[str, Any]:
    return {"v": SCHEMA_VERSION, "ok": True, "result": result}


def json_error_envelope(
    error: str,
    subject: str | None = None,
    path: str | None = None,
    next_commands: list[str] | None = None,
) -> dict[str, Any]:
    """`subject` is what the error is about -- a bench, a site, a domain -- and `next` carries
    commands to run, which fm already computes for the human rendering as bullet points."""
    return {
        "v": SCHEMA_VERSION,
        "ok": False,
        "result": None,
        "error": error,
        "subject": subject,
        "path": path,
        "next": next_commands or [],
    }


def select_fields(result: Any, fields: list[str] | None) -> Any:
    """Keep only `fields`, in a list of records or in one record. Anything else passes through.

    A reader that pays by the token pays for every key it did not ask for, and fm's records are
    wide. An unknown name is ignored rather than refused: a caller asking for a key this version
    does not emit wants the rest of its answer, not an error.
    """
    if not fields:
        return result
    wanted = set(fields)
    if isinstance(result, list):
        return [{k: v for k, v in row.items() if k in wanted} if isinstance(row, dict) else row for row in result]
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k in wanted}
    return result
