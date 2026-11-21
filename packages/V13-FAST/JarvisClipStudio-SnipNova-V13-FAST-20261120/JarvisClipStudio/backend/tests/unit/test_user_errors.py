"""User-facing error messages: clean sentences pass, secrets never do."""

from src.utils.user_errors import (
    GENERIC_MESSAGE,
    MAX_MESSAGE_LENGTH,
    user_facing_error_message,
)
from src.youtube_proxy import YouTubeGeoRestrictedError


def test_user_authored_messages_pass_through_unchanged():
    assert (
        user_facing_error_message(
            ValueError("No usable clip segments were selected for this video.")
        )
        == "No usable clip segments were selected for this video."
    )
    assert (
        user_facing_error_message(ValueError("No clips could be rendered. Please try again."))
        == "No clips could be rendered. Please try again."
    )
    geo = YouTubeGeoRestrictedError()
    assert user_facing_error_message(geo, "youtube_geo_restricted") == str(geo)


def test_signed_url_is_removed():
    exc = RuntimeError(
        "download failed for https://cdn.example.com/v.mp4?"
        "X-Amz-Signature=deadbeefcafe&X-Amz-Credential=AKIAEXAMPLE HTTP 403"
    )
    message = user_facing_error_message(exc, "download_error")
    assert "http" not in message
    assert "deadbeefcafe" not in message
    assert "AKIA" not in message
    assert "download failed" in message


def test_bearer_and_api_keys_are_redacted():
    exc = RuntimeError(
        "provider rejected request: Authorization: Bearer abc123def456 "
        "api_key=sk-live-1234567890"
    )
    message = user_facing_error_message(exc, "analysis_error")
    assert "abc123def456" not in message
    assert "sk-live" not in message
    assert "provider rejected request" in message


def test_proxy_userinfo_and_file_paths_are_scrubbed():
    exc = RuntimeError(
        "proxy //user:passw0rd@proxy.example.com:8080 refused; "
        "ffmpeg input /tmp/supoclip/downloads/video.mp4 missing"
    )
    message = user_facing_error_message(exc, "download_error")
    assert "passw0rd" not in message
    assert "/tmp/supoclip" not in message


def test_traceback_falls_back_to_category_message():
    raw = (
        'Traceback (most recent call last):\n  File "/app/src/ai.py", line 42, '
        'in analyse\nValueError: boom'
    )
    assert (
        user_facing_error_message(RuntimeError(raw), "analysis_error")
        == "We could not analyse this video. Please try again."
    )
    assert user_facing_error_message(RuntimeError(raw)) == GENERIC_MESSAGE


def test_empty_message_falls_back():
    assert (
        user_facing_error_message(Exception(), "transcription_error")
        == "We could not transcribe this video. Please try again."
    )
    assert user_facing_error_message(None) == GENERIC_MESSAGE


def test_message_that_is_only_a_url_falls_back():
    exc = RuntimeError("https://cdn.example.com/x.mp4?token=abc")
    assert (
        user_facing_error_message(exc, "download_error")
        == "The video could not be downloaded. Please check the link and try again."
    )


def test_long_messages_are_capped():
    message = user_facing_error_message(RuntimeError("word " * 200))
    assert len(message) <= MAX_MESSAGE_LENGTH + 1
    assert message.endswith("…")
