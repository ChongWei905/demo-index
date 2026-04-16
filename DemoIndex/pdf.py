"""PDF extraction and TOC parsing for DemoIndex."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

from .models import OutlineEntry, PageArtifact, TextSpan, VisualRegion


_TITLE_CLEAN_RE = re.compile(r"\s+")
def normalize_text(text: str) -> str:
    """Normalize text for fuzzy title matching."""
    compact = _TITLE_CLEAN_RE.sub("", text or "")
    compact = compact.replace("：", ":").replace("·", "").replace(".", "")
    compact = compact.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
    return compact.lower()


def extract_page_artifacts(pdf_path: str | Path, artifacts_dir: str | Path) -> list[PageArtifact]:
    """Extract page images, text spans, and visual region crops from a PDF."""
    pdf_path = Path(pdf_path).expanduser().resolve()
    artifacts_dir = Path(artifacts_dir).expanduser().resolve()
    pages_dir = artifacts_dir / "pages"
    visuals_dir = artifacts_dir / "visuals"
    pages_dir.mkdir(parents=True, exist_ok=True)
    visuals_dir.mkdir(parents=True, exist_ok=True)

    document = pymupdf.open(pdf_path)
    visual_counter = 0
    page_artifacts: list[PageArtifact] = []
    try:
        for page_index, page in enumerate(document, start=1):
            page_image_path = pages_dir / f"page_{page_index:03d}.png"
            if not page_image_path.exists():
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                pixmap.save(page_image_path)

            text_dict = page.get_text("dict")
            spans: list[TextSpan] = []
            lines: list[tuple[str, tuple[float, float, float, float]]] = []
            image_block_bboxes: list[tuple[float, float, float, float]] = []
            text_block_count = 0

            for block in text_dict.get("blocks", []):
                block_type = block.get("type")
                bbox = _to_bbox(block.get("bbox"))
                if block_type == 1:
                    image_block_bboxes.append(bbox)
                    continue
                if block_type != 0:
                    continue
                text_block_count += 1
                for line in block.get("lines", []):
                    line_parts: list[str] = []
                    for span in line.get("spans", []):
                        text = " ".join(str(span.get("text", "")).split())
                        if not text:
                            continue
                        line_parts.append(str(span.get("text", "")))
                        font_name = str(span.get("font", ""))
                        flags = int(span.get("flags", 0))
                        spans.append(
                            TextSpan(
                                text=text,
                                size=float(span.get("size", 0.0)),
                                font=font_name,
                                is_bold=("bold" in font_name.lower()) or bool(flags & 16),
                                bbox=_to_bbox(span.get("bbox")),
                            )
                        )
                    line_text = "".join(line_parts).strip()
                    if line_text:
                        lines.append((line_text, _to_bbox(line.get("bbox"))))

            plain_text = " ".join(page.get_text().split())
            drawing_bboxes = [
                _to_bbox(drawing["rect"])
                for drawing in page.get_drawings()
                if _rect_area(_to_bbox(drawing["rect"])) >= 400.0
            ]

            visual_bboxes = cluster_visual_bboxes(
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
                image_bboxes=image_block_bboxes,
                drawing_bboxes=drawing_bboxes,
            )
            visual_regions: list[VisualRegion] = []
            for bbox in visual_bboxes:
                placeholder_name = f"img-{visual_counter}.jpeg"
                region_path = visuals_dir / placeholder_name
                if not region_path.exists():
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(2, 2),
                        clip=pymupdf.Rect(*bbox),
                        alpha=False,
                    )
                    pixmap.save(region_path)
                visual_regions.append(
                    VisualRegion(
                        bbox=bbox,
                        placeholder_name=placeholder_name,
                        image_path=region_path,
                    )
                )
                visual_counter += 1

            page_artifacts.append(
                PageArtifact(
                    page_number=page_index,
                    page_image_path=page_image_path,
                    plain_text=plain_text,
                    spans=spans,
                    lines=lines,
                    page_width=float(page.rect.width),
                    page_height=float(page.rect.height),
                    text_block_count=text_block_count,
                    image_block_bboxes=image_block_bboxes,
                    drawing_bboxes=drawing_bboxes,
                    visual_regions=visual_regions,
                )
            )
    finally:
        document.close()

    return page_artifacts


def detect_toc_page(pages: list[PageArtifact]) -> int | None:
    """Return the likely physical TOC page number, if one is found."""
    best_page: int | None = None
    best_score = 0
    for page in pages[: min(20, len(pages))]:
        score = 0
        text = page.plain_text
        if "目录" in text:
            score += 10
        score += len(re.findall(r"(?:\.{4,}|…{2,}|：|:)\s*\d+", text))
        if page.text_block_count <= 20:
            score += 1
        if score > best_score:
            best_score = score
            best_page = page.page_number
    return best_page if best_score > 0 else None


def extract_outline_entries(pages: list[PageArtifact]) -> tuple[int | None, list[OutlineEntry]]:
    """Extract TOC entries with indentation-based level hints."""
    toc_page_number = detect_toc_page(pages)
    if toc_page_number is None:
        return None, []

    toc_page = pages[toc_page_number - 1]
    candidates: list[tuple[float, str, int, tuple[float, float, float, float]]] = []
    for line_text, bbox in toc_page.lines:
        cleaned = " ".join(line_text.split())
        parsed = _parse_toc_line(cleaned)
        if parsed is None:
            continue
        title, printed_page = parsed
        if not title or title == "目录":
            continue
        candidates.append((bbox[0], title, printed_page, bbox))

    if not candidates:
        return toc_page_number, []

    x_to_level = _build_toc_level_map([item[0] for item in candidates])
    raw_entries = [
        OutlineEntry(
            title=title,
            printed_page=printed_page,
            physical_page=None,
            level_hint=x_to_level[round(x0, 1)],
            toc_page_number=toc_page_number,
            bbox=bbox,
        )
        for x0, title, printed_page, bbox in candidates
    ]
    page_offset = infer_page_offset(raw_entries, pages)
    for entry in raw_entries:
        if entry.printed_page is None:
            continue
        physical = entry.printed_page + page_offset
        if 1 <= physical <= len(pages):
            entry.physical_page = physical
    return toc_page_number, raw_entries


def infer_page_offset(outline_entries: list[OutlineEntry], pages: list[PageArtifact]) -> int:
    """Infer the offset from printed TOC pages to physical PDF pages."""
    best_offset = 0
    best_score = -1
    for offset in range(-3, 6):
        score = 0
        for entry in outline_entries[: min(8, len(outline_entries))]:
            if entry.printed_page is None:
                continue
            physical_page = entry.printed_page + offset
            if not 1 <= physical_page <= len(pages):
                continue
            page_text = normalize_text(pages[physical_page - 1].plain_text)
            title_text = normalize_text(entry.title)
            if title_text and title_text in page_text:
                score += 1
        if score > best_score:
            best_score = score
            best_offset = offset
    return best_offset


def layout_heading_candidates(page: PageArtifact, limit: int = 10) -> list[dict]:
    """Return heading-like span candidates for one page."""
    if not page.spans:
        return []
    body_size = _median([span.size for span in page.spans if len(span.text) >= 2]) or 12.0
    candidates: list[dict] = []
    seen: set[str] = set()
    for span in sorted(page.spans, key=lambda item: (item.bbox[1], item.bbox[0])):
        normalized = normalize_text(span.text)
        if normalized in seen:
            continue
        if len(span.text) > 80:
            continue
        if span.size >= body_size * 1.2 or (span.is_bold and span.size >= body_size * 0.95):
            seen.add(normalized)
            candidates.append(
                {
                    "title": span.text,
                    "size": round(span.size, 2),
                    "bold": span.is_bold,
                    "bbox": [round(value, 1) for value in span.bbox],
                }
            )
        if len(candidates) >= limit:
            break
    return candidates


def outline_window_for_page(
    outline_entries: list[OutlineEntry], page_number: int, radius: int = 2
) -> list[OutlineEntry]:
    """Return nearby TOC entries for one physical page."""
    nearby = [
        entry
        for entry in outline_entries
        if entry.physical_page is not None and abs(entry.physical_page - page_number) <= radius
    ]
    if nearby:
        return nearby
    return [
        entry
        for entry in outline_entries
        if entry.physical_page is not None
    ][: min(6, len(outline_entries))]


def cluster_visual_bboxes(
    *,
    page_width: float,
    page_height: float,
    image_bboxes: list[tuple[float, float, float, float]],
    drawing_bboxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Merge raw image and drawing boxes into larger visual regions."""
    raw_boxes = list(image_bboxes) + list(drawing_bboxes)
    if not raw_boxes:
        return []

    merged: list[list[float]] = []
    for bbox in sorted(raw_boxes, key=lambda item: (item[1], item[0])):
        expanded = _expand_bbox(bbox, page_width, page_height, padding=8.0)
        attached = False
        for current in merged:
            if _boxes_touch(tuple(current), expanded, gap=24.0):
                current[:] = list(_merge_bbox(tuple(current), expanded))
                attached = True
                break
        if not attached:
            merged.append(list(expanded))

    changed = True
    while changed:
        changed = False
        next_round: list[list[float]] = []
        for bbox in merged:
            attached = False
            for current in next_round:
                if _boxes_touch(tuple(current), tuple(bbox), gap=24.0):
                    current[:] = list(_merge_bbox(tuple(current), tuple(bbox)))
                    attached = True
                    changed = True
                    break
            if not attached:
                next_round.append(list(bbox))
        merged = next_round

    min_area = page_width * page_height * 0.015
    filtered = [
        tuple(bbox)
        for bbox in merged
        if _rect_area(tuple(bbox)) >= min_area and bbox[1] < page_height * 0.95
    ]
    filtered.sort(key=lambda item: (item[1], item[0]))
    return filtered[:4]


def _parse_toc_line(text: str) -> tuple[str, int] | None:
    """Parse one TOC line into a title and printed page number."""
    match = re.search(r"(?P<page>\d+)\s*$", text)
    if match is None:
        return None
    title = text[: match.start()].rstrip(" .…:：\t�")
    title = title.lstrip("•").strip()
    if not title:
        return None
    return title, int(match.group("page"))


def _build_toc_level_map(x_positions: list[float]) -> dict[float, int]:
    """Map TOC x positions to indentation levels while handling multi-column layouts."""
    if not x_positions:
        return {}
    ordered = sorted(round(value, 1) for value in x_positions)
    columns: list[list[float]] = []
    current_column: list[float] = [ordered[0]]
    for value in ordered[1:]:
        if value - current_column[-1] > 120.0:
            columns.append(current_column)
            current_column = [value]
        else:
            current_column.append(value)
    columns.append(current_column)

    mapping: dict[float, int] = {}
    for column in columns:
        buckets: list[list[float]] = [[column[0]]]
        for value in column[1:]:
            if value - buckets[-1][-1] <= 15.0:
                buckets[-1].append(value)
            else:
                buckets.append([value])
        for index, bucket in enumerate(buckets, start=1):
            for value in bucket:
                mapping[value] = index
    return mapping
def _to_bbox(value) -> tuple[float, float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(float(item) for item in value)


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    *,
    padding: float,
) -> tuple[float, float, float, float]:
    return (
        max(0.0, bbox[0] - padding),
        max(0.0, bbox[1] - padding),
        min(page_width, bbox[2] + padding),
        min(page_height, bbox[3] + padding),
    )


def _merge_bbox(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _boxes_touch(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    *,
    gap: float,
) -> bool:
    horizontal_gap = max(0.0, max(left[0], right[0]) - min(left[2], right[2]))
    vertical_gap = max(0.0, max(left[1], right[1]) - min(left[3], right[3]))
    return horizontal_gap <= gap and vertical_gap <= gap


def _rect_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
