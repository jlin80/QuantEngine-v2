"""Backend del dashboard: FastAPI + REST + WebSockets."""

from app.dashboard.api.main import create_app
from app.dashboard.api.service import ApiService

__all__ = ["ApiService", "create_app"]
