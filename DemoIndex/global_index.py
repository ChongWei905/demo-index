"""Global chunk index builders for DemoIndex."""

from __future__ import annotations

import re
from typing import Any, Callable

from .postgres_store import (
    ChunkRecord,
    build_chunk_id,
    build_text_hash,
    flatten_tree_sections,
)


DEFAULT_GLOBAL_INDEX_MODEL = "text-embedding-v4"
DEFAULT_CHUNK_TOKEN_TARGET = 400
DEFAULT_CHUNK_TOKEN_OVERLAP = 80
DEFAULT_EMBEDDING_DIMENSION = 1024


def build_global_chunk_records(
    tree_payload: dict[str, Any],
    *,
    count_tokens: Callable[..., int],
    embedding_client,
    embedding_model: str = DEFAULT_GLOBAL_INDEX_MODEL,
    chunk_token_target: int = DEFAULT_CHUNK_TOKEN_TARGET,
    chunk_token_overlap: int = DEFAULT_CHUNK_TOKEN_OVERLAP,
) -> tuple[list[ChunkRecord], dict[str, Any]]:
    """Build global index chunk records from a final DemoIndex tree payload."""
    flattened_sections = flatten_tree_sections(tree_payload)
    leaf_sections = [section for section in flattened_sections if section.is_leaf]

    chunk_bases: list[dict[str, Any]] = []
    embedding_inputs: list[str] = []
    for section in leaf_sections:
        prepared_text = _prepare_section_text(section.text, section.title)
        if not prepared_text:
            continue
        chunk_parts = _chunk_section_text(
            prepared_text,
            count_tokens=count_tokens,
            target_tokens=chunk_token_target,
            overlap_tokens=chunk_token_overlap,
        )
        for chunk_index, chunk_info in enumerate(chunk_parts):
            contextual_text = _compose_contextual_text(
                title_path=section.title_path,
                title=section.title,
                body=chunk_info["chunk_text"],
            )
            chunk_bases.append(
                {
                    "doc_id": section.doc_id,
                    "section_id": section.section_id,
                    "node_id": section.node_id,
                    "chunk_index": chunk_index,
                    "title": section.title,
                    "title_path": section.title_path,
                    "page_index": section.page_index,
                    "chunk_text": chunk_info["chunk_text"],
                    "search_text": contextual_text,
                    "token_count": chunk_info["token_count"],
                    "text_hash": build_text_hash(chunk_info["chunk_text"]),
                }
            )
            embedding_inputs.append(contextual_text)

    vectors = embedding_client.embed_documents(embedding_inputs)
    records = [
        ChunkRecord(
            chunk_id=build_chunk_id(
                doc_id=base["doc_id"],
                section_id=base["section_id"],
                chunk_index=base["chunk_index"],
            ),
            doc_id=base["doc_id"],
            section_id=base["section_id"],
            node_id=base["node_id"],
            chunk_index=base["chunk_index"],
            title=base["title"],
            title_path=base["title_path"],
            page_index=base["page_index"],
            chunk_text=base["chunk_text"],
            search_text=base["search_text"],
            token_count=base["token_count"],
            text_hash=base["text_hash"],
            embedding=vector,
        )
        for base, vector in zip(chunk_bases, vectors, strict=True)
    ]

    report = {
        "model": embedding_model,
        "embedding_dimensions": getattr(embedding_client, "dimensions", DEFAULT_EMBEDDING_DIMENSION),
        "leaf_section_count": len(leaf_sections),
        "chunk_count": len(records),
        "records": [
            {
                "chunk_id": record.chunk_id,
                "section_id": record.section_id,
                "node_id": record.node_id,
                "chunk_index": record.chunk_index,
                "title": record.title,
                "title_path": record.title_path,
                "page_index": record.page_index,
                "token_count": record.token_count,
                "text_hash": record.text_hash,
            }
            for record in records[:5]
        ],
    }
    return records, report


def _prepare_section_text(text: str, title: str) -> str:
    """Normalize one leaf section text and drop duplicated leading titles."""
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return ""

    lines = [line.strip() for line in cleaned.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if lines and _normalize_for_compare(lines[0]) == _normalize_for_compare(title):
        lines.pop(0)
    return "\n".join(line for line in lines if line).strip()


def _chunk_section_text(
    text: str,
    *,
    count_tokens: Callable[..., int],
    target_tokens: int,
    overlap_tokens: int,
) -> list[dict[str, Any]]:
    """Chunk one section text with paragraph-aware overlap."""
    total_tokens = _count_tokens(count_tokens, text)
    if total_tokens <= target_tokens:
        return [{"chunk_text": text, "token_count": total_tokens}]

    blocks = _split_text_blocks(text, count_tokens=count_tokens, target_tokens=target_tokens)
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(blocks):
        end = start
        current_blocks: list[str] = []
        current_tokens = 0

        while end < len(blocks):
            candidate_blocks = [*current_blocks, blocks[end]]
            candidate_text = "\n\n".join(candidate_blocks).strip()
            candidate_tokens = _count_tokens(count_tokens, candidate_text)
            if current_blocks and candidate_tokens > target_tokens:
                break
            current_blocks = candidate_blocks
            current_tokens = candidate_tokens
            end += 1
            if current_tokens >= target_tokens:
                break

        if not current_blocks:
            current_blocks = [blocks[start]]
            current_tokens = _count_tokens(count_tokens, current_blocks[0])
            end = start + 1

        chunk_text = "\n\n".join(current_blocks).strip()
        chunks.append({"chunk_text": chunk_text, "token_count": current_tokens})

        if end >= len(blocks):
            break

        overlap_start = end
        overlap_token_count = 0
        while overlap_start > start and overlap_token_count < overlap_tokens:
            overlap_start -= 1
            overlap_token_count += _count_tokens(count_tokens, blocks[overlap_start])
        start = max(start + 1, overlap_start)

    return chunks


def _split_text_blocks(
    text: str,
    *,
    count_tokens: Callable[..., int],
    target_tokens: int,
) -> list[str]:
    """Split text into chunkable blocks, recursively shrinking oversized ones."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    blocks: list[str] = []
    for paragraph in paragraphs:
        if _count_tokens(count_tokens, paragraph) <= target_tokens:
            blocks.append(paragraph)
            continue
        blocks.extend(
            _split_oversized_block(
                paragraph,
                count_tokens=count_tokens,
                target_tokens=target_tokens,
            )
        )
    return [block for block in blocks if block.strip()]


def _split_oversized_block(
    text: str,
    *,
    count_tokens: Callable[..., int],
    target_tokens: int,
) -> list[str]:
    """Split one oversized block into smaller segments."""
    if _count_tokens(count_tokens, text) <= target_tokens:
        return [text.strip()]

    sentences = _split_sentences(text)
    if len(sentences) > 1:
        merged: list[str] = []
        current = ""
        for sentence in sentences:
            if _count_tokens(count_tokens, sentence) > target_tokens:
                if current.strip():
                    merged.append(current.strip())
                    current = ""
                merged.extend(
                    _split_by_character_budget(
                        sentence,
                        count_tokens=count_tokens,
                        target_tokens=target_tokens,
                    )
                )
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if current and _count_tokens(count_tokens, candidate) > target_tokens:
                merged.append(current.strip())
                current = sentence
            else:
                current = candidate
        if current.strip():
            merged.append(current.strip())
        return merged

    return _split_by_character_budget(
        text,
        count_tokens=count_tokens,
        target_tokens=target_tokens,
    )


def _split_by_character_budget(
    text: str,
    *,
    count_tokens: Callable[..., int],
    target_tokens: int,
) -> list[str]:
    """Fallback split for long text without safe paragraph or sentence boundaries."""
    pieces: list[str] = []
    start = 0
    text = text.strip()
    while start < len(text):
        best_end = start + 1
        low = start + 1
        high = len(text)
        while low <= high:
            mid = (low + high) // 2
            candidate = text[start:mid].strip()
            if not candidate:
                low = mid + 1
                continue
            token_count = _count_tokens(count_tokens, candidate)
            if token_count <= target_tokens:
                best_end = mid
                low = mid + 1
            else:
                high = mid - 1
        piece = text[start:best_end].strip()
        if not piece:
            break
        pieces.append(piece)
        start = best_end
    return pieces


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for mixed Chinese and English content."""
    parts = re.findall(r".+?(?:[。！？!?；;](?=\s|$)|$)", text, flags=re.S)
    return [part.strip() for part in parts if part.strip()]


def _compose_contextual_text(*, title_path: str, title: str, body: str) -> str:
    """Build the lexical and embedding text for one chunk."""
    lines: list[str] = []
    if title_path:
        lines.append(title_path)
    if title and not _path_ends_with_title(title_path, title):
        lines.append(title)
    if body:
        lines.append(body)
    return "\n".join(lines).strip()


def _path_ends_with_title(title_path: str, title: str) -> bool:
    """Return whether the current title path already ends with the section title."""
    if not title_path:
        return False
    parts = [part.strip() for part in title_path.split(" > ") if part.strip()]
    if not parts:
        return False
    return _normalize_for_compare(parts[-1]) == _normalize_for_compare(title)


def _normalize_for_compare(text: str) -> str:
    """Normalize text for title prefix comparisons."""
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _count_tokens(count_tokens: Callable[..., int], text: str) -> int:
    """Call the shared token counter with a stable `model=None` contract."""
    return int(count_tokens(text, model=None))
