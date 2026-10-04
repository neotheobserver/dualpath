"""DualPath -- agentic RAG across structured and unstructured data."""

from .agent import DualPathAgent
from .config import Settings, get_settings
from .models import AnswerResponse, Citation, Passage, TableResult

__all__ = [
    "AnswerResponse",
    "Citation",
    "DualPathAgent",
    "Passage",
    "Settings",
    "TableResult",
    "get_settings",
]
__version__ = "1.0.0"
