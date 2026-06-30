from __future__ import annotations

# Stable export facade for systemd / README / uvicorn import target.
from .api.routes import app
from .config import validate_config

__all__ = ["app", "validate_config"]
