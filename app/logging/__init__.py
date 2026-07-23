"""Sistema de logging profesional: rotación, separación por módulo, JSON opcional."""

from app.logging.recent import RecentErrorsBuffer, get_recent_errors
from app.logging.setup import configure_logging

__all__ = ["RecentErrorsBuffer", "configure_logging", "get_recent_errors"]
