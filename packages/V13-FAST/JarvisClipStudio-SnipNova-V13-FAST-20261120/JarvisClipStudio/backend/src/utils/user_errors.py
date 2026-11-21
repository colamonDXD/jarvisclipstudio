"""User-facing error text for failed tasks.

Pipeline exceptions are written to the task row (``progress_message``), pushed
to the progress stream and stored in the dead-letter payload, and the API
returns ``progress_message`` to the user verbatim. Raw provider / yt-dlp /
ffmpeg errors can embed signed URLs (tokens in query strings), proxy
credentials, API keys and local file paths, so anything stored for the user
goes through :func:`user_facing_error_message` first.

The full exception always stays in the logs (callers log with ``exc_info``);
this module only decides what the user sees. Messages that are already
user-authored sentences pass through unchanged — only secrets, URLs, paths,
stack traces and over-long dumps are scrubbed or replaced.
"""

import re
from typing import Optional

MAX_MESSAGE_LENGTH = 240

GENERIC_MESSAGE = (
    "Something went wrong while processing this video. Please try again."
)

_CATEGORY_MESSAGES = {
    "download_error": (
        "The video could not be downloaded. Please check the link and try again."
    ),
    "transcription_error": "We could not transcribe this video. Please try again.",
    "analysis_error": "We could not analyse this video. Please try again.",
}

_URL_RE = re.compile(r"https?://\S+")
_USERINFO_RE = re.compile(r"//[^/\s:@]+:[^/\s@]+@")
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(access_token|refresh_token|id_token|client_secret|api_key|apikey"
    r"|signature|token|x-amz-signature|x-amz-credential|x-amz-security-token)"
    r"=[^\s&\"'<>]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_UNIX_PATH_RE = re.compile(r"(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]*")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\s\\]+\\?)+")
_INTERNAL_RE = re.compile(r"traceback|File \"[^\"]+\", line \d+", re.IGNORECASE)


def _scrub(text: str) -> str:
    text = _URL_RE.sub("[link]", text)
    text = _USERINFO_RE.sub("//***:***@", text)
    text = _SECRET_PARAM_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SK_KEY_RE.sub("[redacted]", text)
    text = _AWS_KEY_RE.sub("[redacted]", text)
    text = _WINDOWS_PATH_RE.sub("[file]", text)
    text = _UNIX_PATH_RE.sub("[file]", text)
    text = re.sub(r"\s+", " ", text).strip(" :;-")
    return text


def user_facing_error_message(
    exc: BaseException | str | None, error_code: Optional[str] = None
) -> str:
    """Return the message safe to store on a task and show to its owner.

    Clean, user-authored messages pass through (whitespace-normalised).
    Anything carrying a stack trace, or left empty once secrets are scrubbed,
    falls back to a plain per-category sentence so the user still gets an
    actionable explanation instead of internals.
    """
    raw = "" if exc is None else str(exc)
    if _INTERNAL_RE.search(raw):
        return _CATEGORY_MESSAGES.get(error_code or "", GENERIC_MESSAGE)
    text = _scrub(raw)
    # A message that was only a URL/path/secret scrubs down to placeholders
    # and tells the user nothing — fall back like an empty message.
    meaningful = re.sub(r"\[(link|file|redacted)\]", "", text)
    if not re.search(r"[A-Za-z0-9]", meaningful):
        return _CATEGORY_MESSAGES.get(error_code or "", GENERIC_MESSAGE)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH].rstrip() + "…"
    return text
