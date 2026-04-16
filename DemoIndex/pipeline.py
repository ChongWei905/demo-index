"""Main pipeline for DemoIndex."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .env import PAGEINDEX_ROOT, REPO_ROOT, ensure_pageindex_import_path, load_dashscope_api_key
from .llm import DashScopeVisionClient
from .models import OutlineEntry, PageArtifact, PageTranscription
from .pdf import (
    extract_outline_entries,
    extract_page_artifacts,
    layout_heading_candidates,
    normalize_text,
    outline_window_for_page,
)


def build_pageindex_tree(
    pdf_path: str,
    output_json: str | None = None,
    artifacts_dir: str | None = None,
    model: str = "dashscope/qwen3.6-plus",
    fallback_model: str = "dashscope/qwen3.5-plus",
) -> dict[str, Any]:
    """Build a PageIndex-style tree JSON from one PDF."""
    ensure_pageindex_import_path()
    from pageindex.page_index_md import md_to_tree

    resolved_pdf_path = Path(pdf_path).expanduser().resolve()
    if not resolved_pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {resolved_pdf_path}")

    artifact_root = (
        Path(artifacts_dir).expanduser().resolve()
        if artifacts_dir
        else REPO_ROOT / "DemoIndex" / "artifacts" / resolved_pdf_path.stem
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "transcriptions").mkdir(parents=True, exist_ok=True)
    (artifact_root / "markdown_pages").mkdir(parents=True, exist_ok=True)

    api_key = load_dashscope_api_key()
    pages = extract_page_artifacts(resolved_pdf_path, artifact_root)
    toc_page_number, outline_entries = extract_outline_entries(pages)
    _save_json(artifact_root / "outline_entries.json", [asdict(entry) for entry in outline_entries])

    client = DashScopeVisionClient(
        api_key=api_key,
        model=model,
        fallback_model=fallback_model,
        timeout_seconds=75.0,
        max_retries=2,
    )

    transcriptions_by_page: dict[int, PageTranscription] = {}
    pages_to_transcribe: list[PageArtifact] = []
    for page in pages:
        cached = _load_cached_transcription(artifact_root, page.page_number)
        if cached is not None:
            print(f"[cache] page {page.page_number}/{len(pages)}", flush=True)
            transcriptions_by_page[page.page_number] = cached
            continue
        rule_based_transcription = _build_rule_based_transcription(
            page=page,
            toc_page_number=toc_page_number,
            outline_entries=outline_entries,
        )
        if rule_based_transcription is not None:
            print(f"[rule] page {page.page_number}/{len(pages)}", flush=True)
            _save_json(
                artifact_root / "transcriptions" / f"page_{page.page_number:03d}.json",
                {
                    "page_number": rule_based_transcription.page_number,
                    "page_markdown": rule_based_transcription.page_markdown,
                    "headings": rule_based_transcription.headings,
                    "model": rule_based_transcription.model,
                },
            )
            transcriptions_by_page[page.page_number] = rule_based_transcription
            continue
        pages_to_transcribe.append(page)

    if pages_to_transcribe:
        max_workers = min(3, len(pages_to_transcribe))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _transcribe_page,
                    client=client,
                    page=page,
                    toc_page_number=toc_page_number,
                    outline_entries=outline_entries,
                ): page
                for page in pages_to_transcribe
            }
            for future in concurrent.futures.as_completed(futures):
                page = futures[future]
                transcription = future.result()
                print(f"[done] page {page.page_number}/{len(pages)}", flush=True)
                _save_json(
                    artifact_root / "transcriptions" / f"page_{page.page_number:03d}.json",
                    {
                        "page_number": transcription.page_number,
                        "page_markdown": transcription.page_markdown,
                        "headings": transcription.headings,
                        "model": transcription.model,
                    },
                )
                transcriptions_by_page[page.page_number] = transcription

    transcriptions = [transcriptions_by_page[page.page_number] for page in pages]

    combined_markdown, line_to_page = _normalize_and_merge_markdown(
        pages=pages,
        transcriptions=transcriptions,
        outline_entries=outline_entries,
        toc_page_number=toc_page_number,
        artifact_root=artifact_root,
    )
    combined_markdown_path = artifact_root / "combined_document.md"
    combined_markdown_path.write_text(combined_markdown, encoding="utf-8")

    tree_result = asyncio.run(
        md_to_tree(
            md_path=str(combined_markdown_path),
            if_thinning=False,
            if_add_node_summary="no",
            if_add_doc_description="no",
            if_add_node_text="yes",
            if_add_node_id="yes",
        )
    )
    tree = tree_result["structure"]
    output = {
        "doc_id": _stable_doc_id(resolved_pdf_path),
        "status": "completed",
        "retrieval_ready": False,
        "result": _convert_tree(tree, line_to_page),
    }

    target_output_path = (
        Path(output_json).expanduser().resolve()
        if output_json
        else artifact_root / f"{resolved_pdf_path.stem}_pageindex_tree.json"
    )
    _save_json(target_output_path, output)
    return output


def compare_tree(actual_json: str, expected_json: str) -> dict[str, Any]:
    """Compare two PageIndex-style tree JSON files."""
    actual = json.loads(Path(actual_json).expanduser().resolve().read_text(encoding="utf-8"))
    expected = json.loads(Path(expected_json).expanduser().resolve().read_text(encoding="utf-8"))

    actual_nodes = _flatten_tree(actual.get("result") or [])
    expected_nodes = _flatten_tree(expected.get("result") or [])
    actual_titles = [node["title"] for node in actual_nodes]
    expected_titles = [node["title"] for node in expected_nodes]

    actual_title_set = set(actual_titles)
    expected_title_set = set(expected_titles)
    matched_titles = sorted(actual_title_set & expected_title_set)

    page_matches = 0
    page_examples: list[dict[str, Any]] = []
    expected_by_title = {node["title"]: node for node in expected_nodes}
    for title in matched_titles:
        actual_page = next(node["page_index"] for node in actual_nodes if node["title"] == title)
        expected_page = expected_by_title[title]["page_index"]
        is_match = (
            actual_page is not None
            and expected_page is not None
            and abs(int(actual_page) - int(expected_page)) <= 1
        )
        if is_match:
            page_matches += 1
        if len(page_examples) < 20 and not is_match:
            page_examples.append(
                {
                    "title": title,
                    "actual_page_index": actual_page,
                    "expected_page_index": expected_page,
                }
            )

    report = {
        "actual_root_titles": [node["title"] for node in actual.get("result") or []],
        "expected_root_titles": [node["title"] for node in expected.get("result") or []],
        "top_level_schema_match": list(actual.keys()) == list(expected.keys()),
        "actual_node_count": len(actual_nodes),
        "expected_node_count": len(expected_nodes),
        "matched_title_count": len(matched_titles),
        "title_precision": _ratio(len(matched_titles), len(actual_title_set)),
        "title_recall": _ratio(len(matched_titles), len(expected_title_set)),
        "page_match_ratio_within_one": _ratio(page_matches, len(matched_titles)),
        "missing_titles": sorted(expected_title_set - actual_title_set)[:30],
        "unexpected_titles": sorted(actual_title_set - expected_title_set)[:30],
        "page_mismatches": page_examples,
        "text_length_examples": [
            {
                "title": title,
                "actual_text_length": len(next(node["text"] for node in actual_nodes if node["title"] == title) or ""),
                "expected_text_length": len(expected_by_title[title]["text"] or ""),
            }
            for title in matched_titles[:15]
        ],
    }
    return report


def _normalize_and_merge_markdown(
    *,
    pages: list[PageArtifact],
    transcriptions: list[PageTranscription],
    outline_entries: list[OutlineEntry],
    toc_page_number: int | None,
    artifact_root: Path,
) -> tuple[str, dict[int, int]]:
    """Normalize heading levels, save per-page markdown, and merge into one document."""
    base_offset = 1 if toc_page_number and toc_page_number > 1 else 0
    toc_by_title: dict[str, OutlineEntry] = {normalize_text(entry.title): entry for entry in outline_entries}
    top_level_entries = sorted(
        [entry for entry in outline_entries if entry.level_hint == 1 and entry.physical_page is not None],
        key=lambda item: int(item.physical_page or 0),
    )
    cover_child_root_title = normalize_text(top_level_entries[0].title) if base_offset and top_level_entries else None
    line_to_page: dict[int, int] = {}
    markdown_chunks: list[str] = []

    for page, transcription in zip(pages, transcriptions, strict=True):
        normalized_markdown = _normalize_page_markdown(
            page=page,
            transcription=transcription,
            toc_page_number=toc_page_number,
            toc_by_title=toc_by_title,
            base_offset=base_offset,
            top_level_entries=top_level_entries,
            cover_child_root_title=cover_child_root_title,
        )
        page_output_path = artifact_root / "markdown_pages" / f"page_{page.page_number:03d}.md"
        page_output_path.write_text(normalized_markdown + "\n", encoding="utf-8")
        if normalized_markdown.strip():
            markdown_chunks.append(f"<!-- page:{page.page_number} -->\n{normalized_markdown}")

    combined_markdown = "\n\n".join(markdown_chunks)
    current_page: int | None = None
    for line_number, line in enumerate(combined_markdown.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("<!-- page:") and stripped.endswith("-->"):
            try:
                current_page = int(stripped.removeprefix("<!-- page:").removesuffix(" -->"))
            except ValueError:
                current_page = None
            continue
        if stripped.startswith("#") and current_page is not None:
            line_to_page[line_number] = current_page

    return combined_markdown, line_to_page


def _normalize_page_markdown(
    *,
    page: PageArtifact,
    transcription: PageTranscription,
    toc_page_number: int | None,
    toc_by_title: dict[str, OutlineEntry],
    base_offset: int,
    top_level_entries: list[OutlineEntry],
    cover_child_root_title: str | None,
) -> str:
    """Rewrite heading levels in one page markdown according to generic rules."""
    markdown_lines = transcription.page_markdown.splitlines()
    layout_candidates = layout_heading_candidates(page)
    layout_sizes = {normalize_text(item["title"]): float(item["size"]) for item in layout_candidates}
    heading_titles = _extract_heading_titles(markdown_lines)
    metadata_titles = [
        str(item.get("title", "")).strip()
        for item in transcription.headings
        if str(item.get("title", "")).strip()
    ]
    if metadata_titles:
        heading_titles = metadata_titles

    page_role = "content"
    if toc_page_number and page.page_number < toc_page_number:
        page_role = "cover"
    elif toc_page_number and page.page_number == toc_page_number:
        page_role = "toc"

    normalized_levels: dict[str, int] = {}
    previous_level: int | None = None
    for index, title in enumerate(heading_titles):
        normalized = normalize_text(title)
        toc_entry = toc_by_title.get(normalized)
        active_root = _active_top_level_entry(top_level_entries, page.page_number)
        root_offset = int(
            bool(
                cover_child_root_title
                and active_root is not None
                and normalize_text(active_root.title) == cover_child_root_title
            )
        )
        if page_role == "cover" and index == 0:
            level = 1
        elif page_role == "toc" and "目录" in title:
            level = 2 if base_offset else 1
        elif toc_entry is not None:
            heading_size = layout_sizes.get(normalized, 0.0)
            if toc_entry.level_hint == 1:
                if index > 0 and previous_level is not None:
                    level = previous_level
                else:
                    level = 2 if root_offset else 1
            elif index == 0 and heading_size >= 32.0:
                level = 1
            else:
                level = min(6, toc_entry.level_hint + root_offset)
        elif previous_level is not None:
            level = min(6, previous_level + 1)
        else:
            level = 2 if base_offset else 1
        normalized_levels[normalized] = level
        previous_level = level

    rewritten: list[str] = []
    for line in markdown_lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            normalized = normalize_text(title)
            toc_entry = toc_by_title.get(normalized)
            if _should_demote_heading(title, toc_entry):
                rewritten.append(title)
                continue
            level = normalized_levels.get(normalized, 2 if base_offset else 1)
            rewritten.append("#" * level + " " + title)
        else:
            rewritten.append(line)
    return "\n".join(rewritten).strip()


def _extract_heading_titles(lines: list[str]) -> list[str]:
    """Collect markdown heading titles in order."""
    titles: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            titles.append(stripped.lstrip("#").strip())
    return titles


def _convert_tree(tree: list[dict[str, Any]], line_to_page: dict[int, int]) -> list[dict[str, Any]]:
    """Convert a markdown tree into the target JSON structure."""
    counter = 0

    def convert_node(node: dict[str, Any]) -> dict[str, Any]:
        nonlocal counter
        converted = {
            "title": _sanitize_output_title(str(node.get("title") or "")),
            "node_id": f"{counter:04d}",
            "page_index": line_to_page.get(int(node.get("line_num", 0))),
            "text": node.get("text", ""),
        }
        counter += 1
        children = [convert_node(child) for child in node.get("nodes") or []]
        if children:
            converted["nodes"] = children
        return converted

    return [convert_node(node) for node in tree]


def _flatten_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a tree into a list while preserving title, page, and text."""
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        flattened.append(
            {
                "title": node.get("title"),
                "page_index": node.get("page_index"),
                "text": node.get("text", ""),
            }
        )
        flattened.extend(_flatten_tree(node.get("nodes") or []))
    return flattened


def _load_cached_transcription(artifact_root: Path, page_number: int) -> PageTranscription | None:
    """Load a cached page transcription if it exists."""
    path = artifact_root / "transcriptions" / f"page_{page_number:03d}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PageTranscription(
        page_number=int(payload["page_number"]),
        page_markdown=str(payload["page_markdown"]),
        headings=list(payload.get("headings") or []),
        model=str(payload.get("model") or ""),
    )


def _save_json(path: Path, payload: Any) -> None:
    """Write a JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ratio(numerator: int, denominator: int) -> float:
    """Return a safe ratio."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _stable_doc_id(pdf_path: Path) -> str:
    """Build a stable document id from PDF content."""
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, digest))


def _active_top_level_entry(
    top_level_entries: list[OutlineEntry], page_number: int
) -> OutlineEntry | None:
    """Return the active top-level TOC entry for one physical page."""
    active: OutlineEntry | None = None
    for entry in top_level_entries:
        if entry.physical_page is None:
            continue
        if int(entry.physical_page) <= page_number:
            active = entry
        else:
            break
    return active


def _sanitize_output_title(title: str) -> str:
    """Apply lightweight title cleanup for final tree output."""
    cleaned = " ".join(title.split())
    cleaned = re.sub(r"(?<=\d)\s+(?=[年月日])", "", cleaned)
    cleaned = re.sub(r"^前言[:：]\s*", "", cleaned)
    return cleaned


def _should_demote_heading(title: str, toc_entry: OutlineEntry | None) -> bool:
    """Return whether a markdown heading should be demoted to plain text."""
    if toc_entry is not None:
        return False
    if title in {"ADJUST 为您助力：", "游戏应用洞察报告"}:
        return True
    caption_terms = (
        "全球",
        "各国家",
        "各地区",
        "同比增长率",
        "每款",
        "每用户",
        "付费/自然",
        "会话时长",
        "合作伙伴数量",
        "ARP",
        "CPI",
        "CPM",
        "CPC",
        "IPM",
        "ATT",
    )
    return any(term in title for term in caption_terms) and any(char.isdigit() for char in title)


def _transcribe_page(
    *,
    client: DashScopeVisionClient,
    page: PageArtifact,
    toc_page_number: int | None,
    outline_entries: list[OutlineEntry],
) -> PageTranscription:
    """Transcribe one page with the shared client."""
    print(f"[transcribe] page {page.page_number}", flush=True)
    try:
        return client.transcribe_page(
            page=page,
            toc_page_number=toc_page_number,
            outline_entries=outline_window_for_page(outline_entries, page.page_number),
            layout_candidates=layout_heading_candidates(page),
        )
    except Exception as exc:
        print(f"[page-fallback] page {page.page_number}: {exc}", flush=True)
        return _build_plaintext_fallback_transcription(page)


def _build_rule_based_transcription(
    *,
    page: PageArtifact,
    toc_page_number: int | None,
    outline_entries: list[OutlineEntry],
) -> PageTranscription | None:
    """Return a rule-based transcription for pages that do not need an LLM call."""
    if page.page_number == toc_page_number and outline_entries:
        toc_lines = ["## 目录", ""]
        for entry in outline_entries:
            if entry.printed_page is None:
                toc_lines.append(f"- {entry.title}")
            else:
                toc_lines.append(f"- {entry.title} .... {entry.printed_page}")
        return PageTranscription(
            page_number=page.page_number,
            page_markdown="\n".join(toc_lines).strip(),
            headings=[{"title": "目录", "level_hint": 2, "role": "toc"}],
            model="rule-based",
        )

    if not page.plain_text.strip() and page.visual_regions:
        markdown = "\n\n".join(
            f"![{region.placeholder_name}]({region.placeholder_name})"
            for region in page.visual_regions
        )
        return PageTranscription(
            page_number=page.page_number,
            page_markdown=markdown,
            headings=[],
            model="rule-based",
        )

    if not page.plain_text.strip() and not page.visual_regions:
        return PageTranscription(
            page_number=page.page_number,
            page_markdown="",
            headings=[],
            model="rule-based",
        )
    return None


def _build_plaintext_fallback_transcription(page: PageArtifact) -> PageTranscription:
    """Build a lightweight fallback transcription from extracted text only."""
    candidates = layout_heading_candidates(page, limit=3)
    heading_title = candidates[0]["title"] if candidates else None
    text = page.plain_text.strip()
    parts: list[str] = []
    headings: list[dict[str, Any]] = []
    if heading_title:
        parts.append(f"# {heading_title}")
        headings.append({"title": heading_title, "level_hint": 1, "role": "section"})
        normalized_heading = normalize_text(heading_title)
        if text:
            filtered = text
            normalized_text = normalize_text(text)
            if normalized_heading and normalized_heading in normalized_text:
                filtered = filtered
            parts.append(filtered)
    elif text:
        parts.append(text)
    for region in page.visual_regions:
        parts.append(f"![{region.placeholder_name}]({region.placeholder_name})")
    return PageTranscription(
        page_number=page.page_number,
        page_markdown="\n\n".join(part for part in parts if part).strip(),
        headings=headings,
        model="plaintext-fallback",
    )
