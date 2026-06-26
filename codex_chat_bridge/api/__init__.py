"""HTTP boundary package for codex-chat-bridge.

Exports the stable FastAPI application facade while keeping routing,
policy guards, and JSON error construction in dedicated submodules.
"""

from .routes import app

__all__ = ["app"]
