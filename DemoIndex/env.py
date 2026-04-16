"""Environment helpers for DemoIndex."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - bootstrap fallback
    load_dotenv = None


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGEINDEX_ROOT = REPO_ROOT / "PageIndex"


def ensure_pageindex_import_path() -> None:
    """Add the local PageIndex package root to `sys.path` if needed."""
    pageindex_path = str(PAGEINDEX_ROOT)
    if pageindex_path not in sys.path:
        sys.path.insert(0, pageindex_path)


def load_dashscope_api_key() -> str:
    """Load and return the DashScope API key for DemoIndex."""
    env_path = PAGEINDEX_ROOT / ".env"
    if env_path.exists() and load_dotenv is not None:
        load_dotenv(env_path, override=False)
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing DashScope API key. Set DASHSCOPE_API_KEY or add it to PageIndex/.env."
        )
    return api_key
