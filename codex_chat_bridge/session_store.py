"""Session store — re-export facade.

The real implementation now lives in protocol/session.py.
This module re-exports all symbols for backward compatibility.
"""
from .protocol.session import (
    SessionRecord,
    SessionStore,
    get_session_store,
    resolve_session,
    save_session,
    _assistant_message_from_chat_body,
)

__all__ = [
    "SessionRecord",
    "SessionStore",
    "get_session_store",
    "resolve_session",
    "save_session",
    "_assistant_message_from_chat_body",
]
