from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath


_CREDENTIAL_KEY = (
    r"(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token|client[_-]?secret|token|secret|"
    r"password|passwd|credential)"
)
_QUOTED_OR_TOKEN_VALUE = (
    r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,;]+)'
)
_JSON_CREDENTIAL = re.compile(
    rf"""(?i)(["']{_CREDENTIAL_KEY}["']\s*:\s*)"""
    rf"""({_QUOTED_OR_TOKEN_VALUE})"""
)
_CLI_CREDENTIAL = re.compile(
    rf"(?i)(--{_CREDENTIAL_KEY}(?:=|\s+))({_QUOTED_OR_TOKEN_VALUE})"
)
_CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*"
        rf"(?:bearer\s+|basic\s+)?{_QUOTED_OR_TOKEN_VALUE}"
    ),
    re.compile(
        rf"(?i)\b({_CREDENTIAL_KEY})\s*[:=]\s*{_QUOTED_OR_TOKEN_VALUE}"
    ),
    re.compile(
        rf"(?i)([?&]{_CREDENTIAL_KEY}=)"
        r"[^&#\s]+"
    ),
)
_URL_USERINFO = re.compile(
    r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_IPV4 = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
_HOME_PATH = re.compile(
    r"(?<![\w])(?:/home/[^/\s\"'`<>|]+|/Users/[^/\s\"'`<>|]+)"
    r"(?:/[^\s\"'`<>|]+)*"
)
_UNIX_PATH = re.compile(r"(?<![\w])/(?:[^\s\"'`<>|]+)")
_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[A-Z]:\\(?:[^\s\"'`<>|]+))")
_TRAILING_PATH_PUNCTUATION = ".,;:)]}"


def _credential_replacement(match: re.Match[str]) -> str:
    key = match.group(1)
    if key.startswith(("?", "&")):
        return f"{key}[REDACTED]"
    return f"{key}=[REDACTED]"


def _prefixed_credential_replacement(match: re.Match[str]) -> str:
    return f"{match.group(1)}[REDACTED]"


def _json_credential_replacement(match: re.Match[str]) -> str:
    return f'{match.group(1)}"[REDACTED]"'


def _path_replacement(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in _TRAILING_PATH_PUNCTUATION:
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    normalized = raw.replace("\\", "/")
    name = PurePosixPath(normalized).name
    if not name:
        return "[PATH]" + trailing
    return f"[PATH]/{name}{trailing}"


def _replace_literal(
    text: str,
    sensitive_value: str,
    replacement: str,
) -> str:
    if not sensitive_value:
        return text
    return re.sub(re.escape(sensitive_value), replacement, text, flags=re.IGNORECASE)


def redact_log(
    text: str,
    *,
    input_root: str | None = None,
    sample_identifiers: Iterable[str] = (),
) -> str:
    """Return a display/model-safe copy of a workflow log.

    The original log is never modified. Redaction is deliberately conservative:
    credentials, network identifiers, configured sample identifiers, usernames,
    and absolute paths are removed before the text leaves the server-side log
    boundary.
    """

    redacted = text
    if input_root:
        redacted = _replace_literal(redacted, input_root, "[INPUT_ROOT]")

    identifiers = sorted(
        {value.strip() for value in sample_identifiers if value and value.strip()},
        key=len,
        reverse=True,
    )
    for index, identifier in enumerate(identifiers, start=1):
        redacted = _replace_literal(
            redacted,
            identifier,
            f"[SAMPLE_{index:03d}]",
        )

    redacted = _JSON_CREDENTIAL.sub(
        _json_credential_replacement,
        redacted,
    )
    redacted = _CLI_CREDENTIAL.sub(_prefixed_credential_replacement, redacted)
    redacted = _URL_USERINFO.sub(r"\1[REDACTED]@", redacted)
    for pattern in _CREDENTIAL_PATTERNS:
        redacted = pattern.sub(_credential_replacement, redacted)
    redacted = _EMAIL.sub("[EMAIL]", redacted)
    redacted = _IPV4.sub("[IP]", redacted)
    redacted = _HOME_PATH.sub("[HOME]", redacted)
    redacted = _WINDOWS_PATH.sub(_path_replacement, redacted)
    redacted = _UNIX_PATH.sub(_path_replacement, redacted)
    return redacted


def tail_excerpt(text: str, max_chars: int = 12000) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return text
    truncated = text[-max_chars:]
    first_newline = truncated.find("\n")
    notice = "[... earlier log content omitted ...]\n"
    if first_newline < 0:
        return notice
    return notice + truncated[first_newline + 1 :]
