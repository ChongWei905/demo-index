"""Data models for DemoIndex."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class TextSpan:
    """One extracted text span with layout metadata."""

    text: str
    size: float
    font: str
    is_bold: bool
    bbox: tuple[float, float, float, float]


@dataclass(slots=True)
class VisualRegion:
    """One clustered visual region that can map to an image placeholder."""

    bbox: tuple[float, float, float, float]
    placeholder_name: str
    image_path: Path


@dataclass(slots=True)
class PageArtifact:
    """All extracted artifacts needed to transcribe one PDF page."""

    page_number: int
    page_image_path: Path
    plain_text: str
    spans: list[TextSpan]
    lines: list[tuple[str, tuple[float, float, float, float]]]
    page_width: float
    page_height: float
    text_block_count: int
    image_block_bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    drawing_bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    visual_regions: list[VisualRegion] = field(default_factory=list)


@dataclass(slots=True)
class OutlineEntry:
    """One TOC-derived outline entry with a physical page mapping."""

    title: str
    printed_page: int | None
    physical_page: int | None
    level_hint: int
    toc_page_number: int
    bbox: tuple[float, float, float, float]


@dataclass(slots=True)
class PageTranscription:
    """Structured page transcription produced by the multimodal model."""

    page_number: int
    page_markdown: str
    headings: list[dict]
    model: str
