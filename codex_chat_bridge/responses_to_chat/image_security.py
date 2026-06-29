from __future__ import annotations

from typing import Any

from .errors import UnsupportedResponsesInputItemError

# Only allow https:// and data:image/ URL schemes for images.
# Reject file://, http:// (SSRF risk), ftp://, etc.
_ALLOWED_IMAGE_SCHEMES = ("https://", "data:image/")


def is_safe_image_url(url: str | None) -> bool:
    """Check if an image URL is safe (prevents SSRF and internal network leaks).

    Allowed schemes:
      - https://        — standard external links
      - data:image/     — inline base64 images

    Rejected:
      - file://         — local file read
      - http://         — internal network / cloud metadata attack vector
      - ftp://, etc.
    """
    if not isinstance(url, str) or not url:
        return False
    return url.startswith(_ALLOWED_IMAGE_SCHEMES)


def chat_image_part_from_input_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a Responses input_image item to a Chat Completions image_url part."""
    image_value = item.get("image_url")
    if isinstance(image_value, str) and image_value:
        url = image_value
        payload: dict[str, Any] = {"url": url}
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
