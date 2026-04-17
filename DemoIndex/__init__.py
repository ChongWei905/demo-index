"""DemoIndex package for PDF-to-markdown PageIndex-style trees."""

from __future__ import annotations

from typing import Any


def build_pageindex_tree(*args: Any, **kwargs: Any) -> dict:
    """Build a PageIndex-style tree from a PDF."""
    from .pipeline import build_pageindex_tree as _build_pageindex_tree

    return _build_pageindex_tree(*args, **kwargs)


def compare_tree(*args: Any, **kwargs: Any) -> dict:
    """Compare two PageIndex-style tree JSON files."""
    from .pipeline import compare_tree as _compare_tree

    return _compare_tree(*args, **kwargs)


def parse_query(*args: Any, **kwargs: Any):
    """Parse one retrieval query into a structured understanding object."""
    from .retrieval import parse_query as _parse_query

    return _parse_query(*args, **kwargs)


def retrieve_candidates(*args: Any, **kwargs: Any):
    """Run Stage 1 and Stage 2 retrieval and return a rich handoff object."""
    from .retrieval import retrieve_candidates as _retrieve_candidates

    return _retrieve_candidates(*args, **kwargs)


__all__ = ["build_pageindex_tree", "compare_tree", "parse_query", "retrieve_candidates"]
