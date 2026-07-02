from __future__ import annotations

from ..protocol.types import ResponsesInputItem, ImageURLPart, InputAudioPart

from .errors import UnsupportedResponsesInputItemError

# Only allow https:// and data: URI schemes for media URLs.
# Reject file://, http:// (SSRF risk), ftp://, etc.
_ALLOWED_MEDIA_SCHEMES = ("https://", "data:")


def _is_safe_media_url(url: object, *, allowed_data_prefix: str | None = None) -> bool:
    """Check if a media URL is safe (prevents SSRF and internal network leaks).

    Allowed schemes:
      - https://        — standard external links
      - data:image/ or data:audio/ — inline base64 media
         (when allowed_data_prefix is set, data: URIs must start with it)

    Rejected:
      - file://         — local file read
      - http://         — internal network / cloud metadata attack vector
      - ftp://, etc.
    """
    if not isinstance(url, str) or not url:
        return False
    if url.startswith("https://"):
        return True
    if url.startswith("data:"):
        if allowed_data_prefix and not url.startswith(allowed_data_prefix):
            return False
        return True
    return False


# Backward-compatible aliases


def is_safe_image_url(url: object) -> bool:
    """Check if an image URL is safe (prevents SSRF and internal network leaks)."""
    return _is_safe_media_url(url, allowed_data_prefix="data:image/")


def is_safe_audio_url(url: object) -> bool:
    """Check if an audio URL is safe (prevents SSRF and internal network leaks)."""
    return _is_safe_media_url(url, allowed_data_prefix="data:audio/")


def chat_image_part_from_input_item(item: ResponsesInputItem) -> ImageURLPart:
    """Convert a Responses input_image item to a Chat Completions image_url part."""
    image_value = item.get("image_url")
    if isinstance(image_value, str) and image_value:
        url = image_value
        payload: dict[str, object] = {"url": url}
    elif isinstance(image_value, dict) and isinstance(image_value.get("url"), str) and image_value.get("url"):
        url = image_value["url"]
        payload = dict(image_value)
    else:
        raise UnsupportedResponsesInputItemError(
            item.get("type") if isinstance(item.get("type"), str) else None, item,
        )
    if not is_safe_image_url(url):
        raise UnsupportedResponsesInputItemError(
            item.get("type") if isinstance(item.get("type"), str) else None,
            item,
            detail=f"Rejected unsafe image URL scheme (only https:// and data:image/ allowed): {url[:60]}",
        )
    detail = item.get("detail")
    if isinstance(detail, str) and detail and "detail" not in payload:
        payload["detail"] = detail
    return {"type": "image_url", "image_url": payload}


def chat_audio_part_from_input_item(item: ResponsesInputItem) -> InputAudioPart:
    """Convert a Responses input_audio item to a Chat Completions input_audio part.

    Supports:
      - audio_url: a URL string → {"type": "input_audio", "input_audio": {"url": ...}}
      - data + format: base64 data → {"type": "input_audio", "input_audio": {"data": data:audio/...}}
    """
    audio_url = item.get("audio_url")
    audio_data = item.get("data")

    if isinstance(audio_url, str) and audio_url:
        if not is_safe_audio_url(audio_url):
            raise UnsupportedResponsesInputItemError(
                item.get("type") if isinstance(item.get("type"), str) else None,
                item,
                detail=f"Rejected unsafe audio URL scheme (only https:// and data:audio/ allowed): {audio_url[:60]}",
            )
        return {"type": "input_audio", "input_audio": {"url": audio_url}}

    if isinstance(audio_data, str) and audio_data:
        fmt = item.get("format") or "wav"
        data_uri = f"data:audio/{fmt};base64,{audio_data}"
        return {"type": "input_audio", "input_audio": {"data": data_uri}}

    raise UnsupportedResponsesInputItemError(
        item.get("type") if isinstance(item.get("type"), str) else None, item,
    )
