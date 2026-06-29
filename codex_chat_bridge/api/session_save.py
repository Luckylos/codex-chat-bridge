from __future__ import annotations

from collections.abc import AsyncIterator

from ..bridge_context import BridgeToolContext
from ..models import ChatMessage
from ..session_store import save_session


async def save_when_done_stream(
    raw_stream: AsyncIterator[bytes],
    response_id: str,
    messages: list[ChatMessage],
    tool_context: BridgeToolContext,
    model: str,
    assistant_message: ChatMessage | None = None,
) -> AsyncIterator[bytes]:
    """Wrap a raw SSE stream, yielding chunks and persisting session on completion.

    Used for non-upstream-streaming-to-SSE and other simple wrap+save paths.
    """
    async for chunk in raw_stream:
        yield chunk
    save_session(response_id, messages, tool_context, model,
                 assistant_message=assistant_message)
