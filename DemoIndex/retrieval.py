"""Stage 1, Stage 2, and Stage 3 retrieval helpers for DemoIndex."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .debug import DebugRecorder
from .env import REPO_ROOT, load_dashscope_api_key
from .llm import DashScopeEmbeddingClient, QwenChatClient
from .postgres_store import resolve_database_url


DEFAULT_PARSE_MODEL = "dashscope/qwen3.6-plus"
DEFAULT_PARSE_FALLBACK_MODEL = "dashscope/qwen3.5-plus"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_TOP_K_DENSE = 60
DEFAULT_TOP_K_LEXICAL = 60
DEFAULT_TOP_K_FUSED_CHUNKS = 80
DEFAULT_TOP_K_DOCS = 10
DEFAULT_TOP_K_SECTIONS_PER_DOC = 3
DEFAULT_TOP_K_CHUNKS_PER_SECTION = 2
DEFAULT_DOC_SCORE_CHUNK_LIMIT = 5
DEFAULT_SECTION_SCORE_CHUNK_LIMIT = 3
DEFAULT_RRF_K = 60
DEFAULT_PROFILE_ENV_VAR = "DEMOINDEX_RETRIEVAL_PROFILE_PATH"

INTENT_PATTERNS = {
    "trend": ["trend", "trends", "趋势", "变化", "走势"],
    "comparison": ["compare", "comparison", "vs", "versus", "对比", "比较"],
    "benchmark": ["benchmark", "ranking", "rank", "排行", "榜单", "top"],
    "diagnosis": ["why", "reason", "impact", "原因", "影响", "为什么"],
    "strategy": ["how", "strategy", "strategies", "advice", "建议", "策略", "如何"],
}

STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "that",
    "this",
    "what",
    "which",
    "when",
    "where",
    "why",
    "how",
    "关于",
    "有关",
    "以及",
    "还有",
    "什么",
    "哪个",
    "哪些",
    "如何",
    "为什么",
    "一下",
    "看看",
    "一下子",
    "一个",
    "一种",
    "这个",
    "那个",
    "趋势",
    "分析",
    "问题",
    "报告",
    "研究",
}

LEADING_CJK_CONNECTOR_RE = re.compile(r"^[的和与及并就把将向对从在按为于]+")
TRAILING_CJK_CONNECTOR_RE = re.compile(r"(的|和|与|及|并|等|方面|情况)+$")
PROFILE_FIELD_NAMES = ("metrics", "regions", "platforms", "genres")
LEXICAL_SCORE_THRESHOLD = 0.18
STAGE3_RELATION_PRIOR = {
    "anchor": 4.0,
    "descendant": 2.75,
    "ancestor": 2.1,
    "sibling": 1.45,
    "doc_fallback": 0.55,
}
STAGE3_RELATION_ORDER = {
    "anchor": 5,
    "descendant": 4,
    "ancestor": 3,
    "sibling": 2,
    "doc_fallback": 1,
}
DEFAULT_STAGE3_SHORTLIST_SIZE = 8


@dataclass(frozen=True)
class QueryUnderstanding:
    """Structured understanding for one retrieval query."""

    raw_query: str
    normalized_query: str
    language: str
    intent: str
    terms: list[str]
    metrics: list[str]
    regions: list[str]
    platforms: list[str]
    genres: list[str]
    time_scope: dict[str, Any]
    llm_enriched: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class RetrievalChunkHit:
    """One fused chunk-level recall result."""

    chunk_id: str
    doc_id: str
    section_id: str
    node_id: str
    title: str
    title_path: str
    page_index: int | None
    chunk_index: int
    chunk_text: str
    dense_rank: int | None
    dense_score: float | None
    lexical_rank: int | None
    lexical_score: float | None
    rrf_score: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class SectionCandidate:
    """One section-level aggregation result."""

    doc_id: str
    section_id: str
    node_id: str
    title: str
    depth: int
    summary: str
    section_score: float
    matched_chunk_count: int
    supporting_chunks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class DocCandidate:
    """One document-level aggregation result."""

    doc_id: str
    doc_score: float
    matched_chunk_count: int
    matched_section_count: int
    top_section_ids: list[str]
    section_candidates: list[SectionCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload = asdict(self)
        payload["section_candidates"] = [section.to_dict() for section in self.section_candidates]
        return payload


@dataclass(frozen=True)
class RetrievalStage12Result:
    """Rich Stage 1 + Stage 2 retrieval handoff object."""

    query_understanding: QueryUnderstanding
    chunk_hits: list[RetrievalChunkHit]
    doc_candidates: list[DocCandidate]
    section_candidates: list[SectionCandidate]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "query_understanding": self.query_understanding.to_dict(),
            "chunk_hits": [item.to_dict() for item in self.chunk_hits],
            "doc_candidates": [item.to_dict() for item in self.doc_candidates],
            "section_candidates": [item.to_dict() for item in self.section_candidates],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class LocalizedSection:
    """One Stage 3 tree-localized section candidate."""

    doc_id: str
    section_id: str
    node_id: str
    parent_id: str | None
    title: str
    depth: int
    summary: str
    title_path: str
    localization_score: float
    stage2_section_score: float
    anchor_section_id: str | None
    relation_to_anchor: Literal["anchor", "descendant", "ancestor", "sibling", "doc_fallback"]
    reason_codes: list[str]
    supporting_chunks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class LocalizedDoc:
    """One document-level Stage 3 localization result."""

    doc_id: str
    doc_score: float
    mode_used: Literal["heuristic", "hybrid"]
    anchor_sections: list[SectionCandidate] = field(default_factory=list)
    localized_sections: list[LocalizedSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "doc_id": self.doc_id,
            "doc_score": self.doc_score,
            "mode_used": self.mode_used,
            "anchor_sections": [section.to_dict() for section in self.anchor_sections],
            "localized_sections": [section.to_dict() for section in self.localized_sections],
        }


@dataclass(frozen=True)
class RetrievalStage3Result:
    """Rich Stage 1 + Stage 2 + Stage 3 retrieval handoff object."""

    query_understanding: QueryUnderstanding
    chunk_hits: list[RetrievalChunkHit]
    doc_candidates: list[DocCandidate]
    section_candidates: list[SectionCandidate]
    localized_docs: list[LocalizedDoc]
    localized_sections: list[LocalizedSection]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "query_understanding": self.query_understanding.to_dict(),
            "chunk_hits": [item.to_dict() for item in self.chunk_hits],
            "doc_candidates": [item.to_dict() for item in self.doc_candidates],
            "section_candidates": [item.to_dict() for item in self.section_candidates],
            "localized_docs": [item.to_dict() for item in self.localized_docs],
            "localized_sections": [item.to_dict() for item in self.localized_sections],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class _Stage3TreeSection:
    """One document section loaded for Stage 3 tree localization."""

    section_id: str
    parent_id: str | None
    doc_id: str
    node_id: str
    title: str
    depth: int
    summary: str
    title_path: str


def parse_query(
    query: str,
    *,
    use_llm: bool = True,
    parse_model: str = DEFAULT_PARSE_MODEL,
    parse_fallback_model: str = DEFAULT_PARSE_FALLBACK_MODEL,
    retrieval_profile_path: str | None = None,
) -> QueryUnderstanding:
    """Parse one retrieval query into a structured understanding object."""
    return _parse_query_internal(
        query,
        use_llm=use_llm,
        parse_model=parse_model,
        parse_fallback_model=parse_fallback_model,
        retrieval_profile_path=retrieval_profile_path,
        debug_recorder=None,
    )


def localize_sections(
    stage12_result: RetrievalStage12Result,
    *,
    mode: Literal["heuristic", "hybrid"] = "hybrid",
    top_k_docs: int | None = None,
    top_k_anchor_sections_per_doc: int = 3,
    top_k_tree_sections_per_doc: int = 5,
    whole_doc_fallback: bool = True,
    rerank_model: str = DEFAULT_PARSE_MODEL,
    rerank_fallback_model: str = DEFAULT_PARSE_FALLBACK_MODEL,
    stage3_shortlist_size: int = DEFAULT_STAGE3_SHORTLIST_SIZE,
    stage3_relation_priors: dict[str, float] | None = None,
    debug_log: bool = False,
    debug_log_dir: str | None = None,
) -> RetrievalStage3Result:
    """Run Stage 3 tree localization over an existing Stage 1 + 2 result."""
    debug_recorder = _create_debug_recorder(debug_log=debug_log, debug_log_dir=debug_log_dir)
    started_at = time.perf_counter()
    if debug_recorder is not None:
        debug_recorder.set_run_metadata(
            stage="stage3_only",
            mode=mode,
            top_k_docs=top_k_docs,
            top_k_anchor_sections_per_doc=top_k_anchor_sections_per_doc,
            top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
            whole_doc_fallback=whole_doc_fallback,
            rerank_model=rerank_model,
            rerank_fallback_model=rerank_fallback_model,
            stage3_shortlist_size=stage3_shortlist_size,
            stage3_relation_priors=stage3_relation_priors or STAGE3_RELATION_PRIOR,
        )
    try:
        return _localize_sections_internal(
            stage12_result,
            mode=mode,
            top_k_docs=top_k_docs,
            top_k_anchor_sections_per_doc=top_k_anchor_sections_per_doc,
            top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
            whole_doc_fallback=whole_doc_fallback,
            rerank_model=rerank_model,
            rerank_fallback_model=rerank_fallback_model,
            stage3_shortlist_size=stage3_shortlist_size,
            stage3_relation_priors=_normalize_stage3_relation_priors(stage3_relation_priors),
            debug_recorder=debug_recorder,
        )
    finally:
        if debug_recorder is not None:
            debug_recorder.write_summary(total_duration_ms=int((time.perf_counter() - started_at) * 1000))


def retrieve_candidates(
    query: str,
    *,
    top_k_dense: int = DEFAULT_TOP_K_DENSE,
    top_k_lexical: int = DEFAULT_TOP_K_LEXICAL,
    top_k_fused_chunks: int = DEFAULT_TOP_K_FUSED_CHUNKS,
    top_k_docs: int = DEFAULT_TOP_K_DOCS,
    top_k_sections_per_doc: int = DEFAULT_TOP_K_SECTIONS_PER_DOC,
    top_k_chunks_per_section: int = DEFAULT_TOP_K_CHUNKS_PER_SECTION,
    use_llm_parse: bool = True,
    parse_model: str = DEFAULT_PARSE_MODEL,
    parse_fallback_model: str = DEFAULT_PARSE_FALLBACK_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    rrf_k: int = DEFAULT_RRF_K,
    lexical_score_threshold: float = LEXICAL_SCORE_THRESHOLD,
    doc_score_chunk_limit: int = DEFAULT_DOC_SCORE_CHUNK_LIMIT,
    section_score_chunk_limit: int = DEFAULT_SECTION_SCORE_CHUNK_LIMIT,
    retrieval_profile_path: str | None = None,
    debug_log: bool = False,
    debug_log_dir: str | None = None,
) -> RetrievalStage12Result:
    """Run Stage 1 and Stage 2 retrieval and return a rich handoff object."""
    debug_recorder = _create_debug_recorder(debug_log=debug_log, debug_log_dir=debug_log_dir)
    started_at = time.perf_counter()
    if debug_recorder is not None:
        debug_recorder.set_run_metadata(
            query=query,
            top_k_dense=top_k_dense,
            top_k_lexical=top_k_lexical,
            top_k_fused_chunks=top_k_fused_chunks,
            top_k_docs=top_k_docs,
            top_k_sections_per_doc=top_k_sections_per_doc,
            top_k_chunks_per_section=top_k_chunks_per_section,
            use_llm_parse=use_llm_parse,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            embedding_model=embedding_model,
            rrf_k=rrf_k,
            lexical_score_threshold=lexical_score_threshold,
            doc_score_chunk_limit=doc_score_chunk_limit,
            section_score_chunk_limit=section_score_chunk_limit,
            retrieval_profile_path=retrieval_profile_path,
        )
    try:
        return _retrieve_candidates_internal(
            query=query,
            top_k_dense=top_k_dense,
            top_k_lexical=top_k_lexical,
            top_k_fused_chunks=top_k_fused_chunks,
            top_k_docs=top_k_docs,
            top_k_sections_per_doc=top_k_sections_per_doc,
            top_k_chunks_per_section=top_k_chunks_per_section,
            use_llm_parse=use_llm_parse,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            embedding_model=embedding_model,
            rrf_k=rrf_k,
            lexical_score_threshold=lexical_score_threshold,
            doc_score_chunk_limit=doc_score_chunk_limit,
            section_score_chunk_limit=section_score_chunk_limit,
            retrieval_profile_path=retrieval_profile_path,
            debug_recorder=debug_recorder,
        )
    finally:
        if debug_recorder is not None:
            debug_recorder.write_summary(total_duration_ms=int((time.perf_counter() - started_at) * 1000))


def retrieve_tree_candidates(
    query: str,
    *,
    top_k_dense: int = DEFAULT_TOP_K_DENSE,
    top_k_lexical: int = DEFAULT_TOP_K_LEXICAL,
    top_k_fused_chunks: int = DEFAULT_TOP_K_FUSED_CHUNKS,
    top_k_docs: int = DEFAULT_TOP_K_DOCS,
    top_k_sections_per_doc: int = DEFAULT_TOP_K_SECTIONS_PER_DOC,
    top_k_chunks_per_section: int = DEFAULT_TOP_K_CHUNKS_PER_SECTION,
    use_llm_parse: bool = True,
    parse_model: str = DEFAULT_PARSE_MODEL,
    parse_fallback_model: str = DEFAULT_PARSE_FALLBACK_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    rrf_k: int = DEFAULT_RRF_K,
    lexical_score_threshold: float = LEXICAL_SCORE_THRESHOLD,
    doc_score_chunk_limit: int = DEFAULT_DOC_SCORE_CHUNK_LIMIT,
    section_score_chunk_limit: int = DEFAULT_SECTION_SCORE_CHUNK_LIMIT,
    retrieval_profile_path: str | None = None,
    stage3_mode: Literal["heuristic", "hybrid"] = "hybrid",
    top_k_tree_sections_per_doc: int = 5,
    top_k_anchor_sections_per_doc: int = 3,
    whole_doc_fallback: bool = True,
    rerank_model: str = DEFAULT_PARSE_MODEL,
    rerank_fallback_model: str = DEFAULT_PARSE_FALLBACK_MODEL,
    stage3_shortlist_size: int = DEFAULT_STAGE3_SHORTLIST_SIZE,
    stage3_relation_priors: dict[str, float] | None = None,
    debug_log: bool = False,
    debug_log_dir: str | None = None,
) -> RetrievalStage3Result:
    """Run Stage 1 + Stage 2 + Stage 3 retrieval and return tree-localized sections."""
    if not str(query or "").strip():
        raise ValueError("Query must not be empty.")

    debug_recorder = _create_debug_recorder(debug_log=debug_log, debug_log_dir=debug_log_dir)
    started_at = time.perf_counter()
    if debug_recorder is not None:
        debug_recorder.set_run_metadata(
            query=query,
            top_k_dense=top_k_dense,
            top_k_lexical=top_k_lexical,
            top_k_fused_chunks=top_k_fused_chunks,
            top_k_docs=top_k_docs,
            top_k_sections_per_doc=top_k_sections_per_doc,
            top_k_chunks_per_section=top_k_chunks_per_section,
            use_llm_parse=use_llm_parse,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            embedding_model=embedding_model,
            rrf_k=rrf_k,
            lexical_score_threshold=lexical_score_threshold,
            doc_score_chunk_limit=doc_score_chunk_limit,
            section_score_chunk_limit=section_score_chunk_limit,
            retrieval_profile_path=retrieval_profile_path,
            stage3_mode=stage3_mode,
            top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
            top_k_anchor_sections_per_doc=top_k_anchor_sections_per_doc,
            whole_doc_fallback=whole_doc_fallback,
            rerank_model=rerank_model,
            rerank_fallback_model=rerank_fallback_model,
            stage3_shortlist_size=stage3_shortlist_size,
            stage3_relation_priors=stage3_relation_priors or STAGE3_RELATION_PRIOR,
        )
    try:
        stage12_result = _retrieve_candidates_internal(
            query=query,
            top_k_dense=top_k_dense,
            top_k_lexical=top_k_lexical,
            top_k_fused_chunks=top_k_fused_chunks,
            top_k_docs=top_k_docs,
            top_k_sections_per_doc=top_k_sections_per_doc,
            top_k_chunks_per_section=top_k_chunks_per_section,
            use_llm_parse=use_llm_parse,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            embedding_model=embedding_model,
            rrf_k=rrf_k,
            lexical_score_threshold=lexical_score_threshold,
            doc_score_chunk_limit=doc_score_chunk_limit,
            section_score_chunk_limit=section_score_chunk_limit,
            retrieval_profile_path=retrieval_profile_path,
            debug_recorder=debug_recorder,
        )
        return _localize_sections_internal(
            stage12_result,
            mode=stage3_mode,
            top_k_docs=top_k_docs,
            top_k_anchor_sections_per_doc=top_k_anchor_sections_per_doc,
            top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
            whole_doc_fallback=whole_doc_fallback,
            rerank_model=rerank_model,
            rerank_fallback_model=rerank_fallback_model,
            stage3_shortlist_size=stage3_shortlist_size,
            stage3_relation_priors=_normalize_stage3_relation_priors(stage3_relation_priors),
            debug_recorder=debug_recorder,
        )
    finally:
        if debug_recorder is not None:
            debug_recorder.write_summary(total_duration_ms=int((time.perf_counter() - started_at) * 1000))


def _retrieve_candidates_internal(
    *,
    query: str,
    top_k_dense: int,
    top_k_lexical: int,
    top_k_fused_chunks: int,
    top_k_docs: int,
    top_k_sections_per_doc: int,
    top_k_chunks_per_section: int,
    use_llm_parse: bool,
    parse_model: str,
    parse_fallback_model: str,
    embedding_model: str,
    rrf_k: int,
    lexical_score_threshold: float,
    doc_score_chunk_limit: int,
    section_score_chunk_limit: int,
    retrieval_profile_path: str | None,
    debug_recorder: DebugRecorder | None,
) -> RetrievalStage12Result:
    """Internal Stage 1 + Stage 2 retrieval implementation with optional shared debug state."""
    if not str(query or "").strip():
        raise ValueError("Query must not be empty.")

    resolved_database_url = resolve_database_url()
    started_at = time.perf_counter()
    with _debug_stage(debug_recorder, "parse_query"):
        query_understanding = _parse_query_internal(
            query,
            use_llm=use_llm_parse,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            retrieval_profile_path=retrieval_profile_path,
            debug_recorder=debug_recorder,
        )
    if debug_recorder is not None:
        debug_recorder.log_event("query_understanding", payload=query_understanding.to_dict())

    lexical_candidate_count = 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        lexical_future = executor.submit(
            _run_lexical_recall,
            query_understanding,
            top_k_lexical,
            resolved_database_url,
            lexical_score_threshold,
            debug_recorder,
        )
        with _debug_stage(debug_recorder, "dense_recall"):
            load_dashscope_api_key()
            embedding_client = DashScopeEmbeddingClient(
                model_name=embedding_model,
                debug_recorder=debug_recorder,
            )
            dense_hits = _run_dense_recall(
                query_understanding.normalized_query,
                top_k_dense,
                resolved_database_url,
                embedding_client,
            )
        lexical_hits, lexical_candidate_count = lexical_future.result()

    with _debug_stage(debug_recorder, "fuse_chunk_hits"):
        fused_hits = _fuse_chunk_hits(
            dense_hits=dense_hits,
            lexical_hits=lexical_hits,
            top_k_fused_chunks=top_k_fused_chunks,
            rrf_k=rrf_k,
        )
    with _debug_stage(debug_recorder, "aggregate_candidates"):
        doc_candidates, section_candidates = _aggregate_candidates(
            fused_hits=fused_hits,
            database_url=resolved_database_url,
            top_k_docs=top_k_docs,
            top_k_sections_per_doc=top_k_sections_per_doc,
            top_k_chunks_per_section=top_k_chunks_per_section,
            doc_score_chunk_limit=doc_score_chunk_limit,
            section_score_chunk_limit=section_score_chunk_limit,
        )

    metadata = {
        "settings": {
            "top_k_dense": top_k_dense,
            "top_k_lexical": top_k_lexical,
            "top_k_fused_chunks": top_k_fused_chunks,
            "top_k_docs": top_k_docs,
            "top_k_sections_per_doc": top_k_sections_per_doc,
            "top_k_chunks_per_section": top_k_chunks_per_section,
            "use_llm_parse": use_llm_parse,
            "parse_model": parse_model,
            "parse_fallback_model": parse_fallback_model,
            "embedding_model": embedding_model,
            "rrf_k": rrf_k,
            "lexical_score_threshold": lexical_score_threshold,
            "doc_score_chunk_limit": doc_score_chunk_limit,
            "section_score_chunk_limit": section_score_chunk_limit,
            "retrieval_profile_path": retrieval_profile_path,
        },
        "counts": {
            "dense_hits": len(dense_hits),
            "lexical_hits": len(lexical_hits),
            "fused_chunk_hits": len(fused_hits),
            "doc_candidates": len(doc_candidates),
            "section_candidates": len(section_candidates),
            "lexical_candidates": lexical_candidate_count,
        },
        "total_duration_ms": int((time.perf_counter() - started_at) * 1000),
        "debug_log_dir": str(debug_recorder.base_dir) if debug_recorder is not None else None,
    }
    result = RetrievalStage12Result(
        query_understanding=query_understanding,
        chunk_hits=fused_hits,
        doc_candidates=doc_candidates,
        section_candidates=section_candidates,
        metadata=metadata,
    )
    if debug_recorder is not None:
        debug_recorder.log_event("retrieval_summary", payload=result.to_dict())
    return result


def _localize_sections_internal(
    stage12_result: RetrievalStage12Result,
    *,
    mode: Literal["heuristic", "hybrid"],
    top_k_docs: int | None,
    top_k_anchor_sections_per_doc: int,
    top_k_tree_sections_per_doc: int,
    whole_doc_fallback: bool,
    rerank_model: str,
    rerank_fallback_model: str,
    stage3_shortlist_size: int,
    stage3_relation_priors: dict[str, float],
    debug_recorder: DebugRecorder | None,
) -> RetrievalStage3Result:
    """Internal Stage 3 localization implementation with optional shared debug state."""
    if mode not in {"heuristic", "hybrid"}:
        raise ValueError(f"Unsupported Stage 3 mode: {mode}")

    with _debug_stage(debug_recorder, "stage3_localize_sections", mode=mode):
        selected_docs = (
            stage12_result.doc_candidates[: int(top_k_docs)]
            if top_k_docs is not None
            else list(stage12_result.doc_candidates)
        )
        if debug_recorder is not None:
            debug_recorder.log_event(
                "stage3_mode_selected",
                mode=mode,
                selected_doc_ids=[item.doc_id for item in selected_docs],
            )

        doc_ids = [item.doc_id for item in selected_docs]
        tree_sections_by_doc, children_by_doc = _load_tree_sections_for_docs(
            database_url=resolve_database_url(),
            doc_ids=doc_ids,
        )
        stage2_section_lookup = {
            (item.doc_id, item.section_id): item
            for item in stage12_result.section_candidates
        }
        search_terms = _derive_search_terms(stage12_result.query_understanding)
        localized_docs: list[LocalizedDoc] = []
        localized_sections: list[LocalizedSection] = []
        llm_client = None
        if mode == "hybrid":
            load_dashscope_api_key()
            llm_client = QwenChatClient(
                primary_model=rerank_model,
                fallback_model=rerank_fallback_model,
                debug_recorder=debug_recorder,
            )

        for doc_candidate in selected_docs:
            doc_sections = tree_sections_by_doc.get(doc_candidate.doc_id, {})
            children_map = children_by_doc.get(doc_candidate.doc_id, {})
            if not doc_sections:
                if debug_recorder is not None:
                    debug_recorder.log_event("stage3_doc_skipped", doc_id=doc_candidate.doc_id, reason="missing_tree")
                localized_docs.append(
                    LocalizedDoc(
                        doc_id=doc_candidate.doc_id,
                        doc_score=doc_candidate.doc_score,
                        mode_used="heuristic",
                        anchor_sections=[],
                        localized_sections=[],
                    )
                )
                continue

            anchor_sections = doc_candidate.section_candidates[:top_k_anchor_sections_per_doc]
            if debug_recorder is not None:
                debug_recorder.log_event(
                    "stage3_doc_selected",
                    doc_id=doc_candidate.doc_id,
                    tree_size=len(doc_sections),
                    anchor_section_ids=[item.section_id for item in anchor_sections],
                )
            candidate_pool, used_whole_doc_fallback = _build_stage3_candidate_pool(
                doc_id=doc_candidate.doc_id,
                doc_sections=doc_sections,
                children_map=children_map,
                anchor_sections=anchor_sections,
                stage2_section_lookup=stage2_section_lookup,
                top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
                whole_doc_fallback=whole_doc_fallback,
            )
            heuristic_sections = _score_stage3_candidates(
                query_understanding=stage12_result.query_understanding,
                search_terms=search_terms,
                candidate_pool=candidate_pool,
                top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
                stage3_relation_priors=stage3_relation_priors,
            )
            if debug_recorder is not None:
                debug_recorder.log_event(
                    "stage3_heuristic_shortlist",
                    doc_id=doc_candidate.doc_id,
                    candidate_pool_size=len(candidate_pool),
                    used_whole_doc_fallback=used_whole_doc_fallback,
                    shortlist_section_ids=[item.section_id for item in heuristic_sections[:stage3_shortlist_size]],
                )

            mode_used: Literal["heuristic", "hybrid"] = "heuristic"
            localized_for_doc = heuristic_sections
            if mode == "hybrid" and llm_client is not None and heuristic_sections:
                reranked_sections = _rerank_stage3_sections_with_llm(
                    query_understanding=stage12_result.query_understanding,
                    anchor_sections=anchor_sections,
                    shortlisted_sections=heuristic_sections[:stage3_shortlist_size],
                    top_k_tree_sections_per_doc=top_k_tree_sections_per_doc,
                    llm_client=llm_client,
                    rerank_model=rerank_model,
                    debug_recorder=debug_recorder,
                    doc_id=doc_candidate.doc_id,
                )
                if reranked_sections is not None:
                    localized_for_doc = reranked_sections
                    mode_used = "hybrid"
                elif debug_recorder is not None:
                    debug_recorder.log_event(
                        "stage3_hybrid_fallback",
                        doc_id=doc_candidate.doc_id,
                        reason="llm_rerank_failed_or_empty",
                    )

            localized_docs.append(
                LocalizedDoc(
                    doc_id=doc_candidate.doc_id,
                    doc_score=doc_candidate.doc_score,
                    mode_used=mode_used,
                    anchor_sections=anchor_sections,
                    localized_sections=localized_for_doc,
                )
            )
            localized_sections.extend(localized_for_doc)

        stage3_metadata = {
            "mode": mode,
            "top_k_docs": top_k_docs,
            "top_k_anchor_sections_per_doc": top_k_anchor_sections_per_doc,
            "top_k_tree_sections_per_doc": top_k_tree_sections_per_doc,
            "whole_doc_fallback": whole_doc_fallback,
            "rerank_model": rerank_model,
            "rerank_fallback_model": rerank_fallback_model,
            "stage3_shortlist_size": stage3_shortlist_size,
            "stage3_relation_priors": stage3_relation_priors,
            "localized_doc_count": len(localized_docs),
            "localized_section_count": len(localized_sections),
        }
        metadata = dict(stage12_result.metadata)
        metadata["stage3"] = stage3_metadata
        result = RetrievalStage3Result(
            query_understanding=stage12_result.query_understanding,
            chunk_hits=stage12_result.chunk_hits,
            doc_candidates=stage12_result.doc_candidates,
            section_candidates=stage12_result.section_candidates,
            localized_docs=localized_docs,
            localized_sections=localized_sections,
            metadata=metadata,
        )
        if debug_recorder is not None:
            debug_recorder.log_event("stage3_localization_summary", payload=result.to_dict())
        return result


def _parse_query_internal(
    query: str,
    *,
    use_llm: bool,
    parse_model: str,
    parse_fallback_model: str,
    retrieval_profile_path: str | None,
    debug_recorder: DebugRecorder | None,
) -> QueryUnderstanding:
    """Parse a query with rule-first extraction and optional LLM enrichment."""
    normalized_query = _normalize_query(query)
    profile_aliases = _load_retrieval_profile_aliases(
        debug_recorder=debug_recorder,
        retrieval_profile_path=retrieval_profile_path,
    )
    rule_result = QueryUnderstanding(
        raw_query=query,
        normalized_query=normalized_query,
        language=_detect_language(normalized_query),
        intent=_detect_intent(normalized_query),
        terms=_extract_terms(normalized_query),
        metrics=_match_aliases(normalized_query, profile_aliases.get("metrics", {})),
        regions=_match_aliases(normalized_query, profile_aliases.get("regions", {})),
        platforms=_match_aliases(normalized_query, profile_aliases.get("platforms", {})),
        genres=_match_aliases(normalized_query, profile_aliases.get("genres", {})),
        time_scope=_extract_time_scope(normalized_query),
        llm_enriched=False,
    )
    if not use_llm:
        if debug_recorder is not None:
            debug_recorder.log_event("query_llm_enrichment_skipped", reason="llm_disabled")
        return rule_result

    enrichment_reason = _needs_llm_enrichment(rule_result)
    if enrichment_reason is None:
        if debug_recorder is not None:
            debug_recorder.log_event("query_llm_enrichment_skipped", reason="generic_parse_sufficient")
        return rule_result

    try:
        load_dashscope_api_key()
        enriched = _enrich_query_with_llm(
            rule_result,
            parse_model=parse_model,
            parse_fallback_model=parse_fallback_model,
            retrieval_profile_path=retrieval_profile_path,
            debug_recorder=debug_recorder,
        )
    except Exception as exc:  # noqa: PERF203
        if debug_recorder is not None:
            debug_recorder.log_event(
                "query_llm_enrichment_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
                reason=enrichment_reason,
            )
        return rule_result

    return _merge_query_understanding(rule_result, enriched)


def _enrich_query_with_llm(
    query_understanding: QueryUnderstanding,
    *,
    parse_model: str,
    parse_fallback_model: str,
    retrieval_profile_path: str | None,
    debug_recorder: DebugRecorder | None,
) -> QueryUnderstanding:
    """Use one LLM pass to supplement weak or missing query fields."""
    client = QwenChatClient(
        primary_model=parse_model,
        fallback_model=parse_fallback_model,
        debug_recorder=debug_recorder,
    )
    prompt = f"""
You are given a search query for a research report retrieval system.
Return only JSON with these keys:
- language: string
- intent: one of ["trend", "benchmark", "diagnosis", "strategy", "comparison", "general"]
- terms: array of strings
- metrics: array of strings
- regions: array of strings
- platforms: array of strings
- genres: array of strings
- time_scope: object with optional keys ["years", "quarters", "raw_mentions"]

Use concise canonical values where possible.
If a field is unknown, return an empty list or empty object.

Query: {query_understanding.normalized_query}

Current rule-based parse:
{json.dumps(query_understanding.to_dict(), ensure_ascii=False)}
""".strip()
    response = client.completion(parse_model, prompt)
    payload = _extract_json_payload(response)
    profile_aliases = _load_retrieval_profile_aliases(
        debug_recorder=None,
        retrieval_profile_path=retrieval_profile_path,
    )
    return QueryUnderstanding(
        raw_query=query_understanding.raw_query,
        normalized_query=query_understanding.normalized_query,
        language=str(payload.get("language") or query_understanding.language),
        intent=str(payload.get("intent") or query_understanding.intent),
        terms=_normalize_string_list(payload.get("terms")),
        metrics=_canonicalize_values(_normalize_string_list(payload.get("metrics")), profile_aliases.get("metrics", {})),
        regions=_canonicalize_values(_normalize_string_list(payload.get("regions")), profile_aliases.get("regions", {})),
        platforms=_canonicalize_values(
            _normalize_string_list(payload.get("platforms")),
            profile_aliases.get("platforms", {}),
        ),
        genres=_canonicalize_values(_normalize_string_list(payload.get("genres")), profile_aliases.get("genres", {})),
        time_scope=_normalize_time_scope(payload.get("time_scope")),
        llm_enriched=True,
    )


def _merge_query_understanding(base: QueryUnderstanding, enriched: QueryUnderstanding) -> QueryUnderstanding:
    """Merge rule-based parsing with optional LLM enrichment."""
    merged_time_scope = {
        "years": sorted(set(base.time_scope.get("years", [])) | set(enriched.time_scope.get("years", []))),
        "quarters": _deduplicate_strings([*base.time_scope.get("quarters", []), *enriched.time_scope.get("quarters", [])]),
        "raw_mentions": _deduplicate_strings(
            [*base.time_scope.get("raw_mentions", []), *enriched.time_scope.get("raw_mentions", [])]
        ),
    }
    llm_changed = (
        set(enriched.metrics) - set(base.metrics)
        or set(enriched.regions) - set(base.regions)
        or set(enriched.platforms) - set(base.platforms)
        or set(enriched.genres) - set(base.genres)
        or set(enriched.terms) - set(base.terms)
        or merged_time_scope != base.time_scope
        or (base.intent == "general" and enriched.intent != "general")
        or (base.language == "unknown" and enriched.language != "unknown")
    )
    return QueryUnderstanding(
        raw_query=base.raw_query,
        normalized_query=base.normalized_query,
        language=enriched.language if base.language == "unknown" else base.language,
        intent=enriched.intent if base.intent == "general" and enriched.intent != "general" else base.intent,
        terms=_deduplicate_strings([*base.terms, *enriched.terms]),
        metrics=_deduplicate_strings([*base.metrics, *enriched.metrics]),
        regions=_deduplicate_strings([*base.regions, *enriched.regions]),
        platforms=_deduplicate_strings([*base.platforms, *enriched.platforms]),
        genres=_deduplicate_strings([*base.genres, *enriched.genres]),
        time_scope=merged_time_scope,
        llm_enriched=bool(llm_changed),
    )


def _run_dense_recall(
    normalized_query: str,
    top_k_dense: int,
    database_url: str,
    embedding_client: DashScopeEmbeddingClient,
) -> list[dict[str, Any]]:
    """Run dense ANN recall over section chunks."""
    query_vector = embedding_client.embed_queries([normalized_query])[0]
    vector_literal = _vector_literal(query_vector)
    psycopg, dict_row = _import_psycopg()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    chunk_id::text AS chunk_id,
                    doc_id,
                    section_id::text AS section_id,
                    node_id,
                    title,
                    title_path,
                    page_index,
                    chunk_index,
                    chunk_text,
                    GREATEST(0.0, 1.0 - (embedding <=> %s::vector)) AS dense_score
                FROM section_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vector_literal, vector_literal, int(top_k_dense)),
            )
            rows = list(cursor.fetchall())
    for rank, row in enumerate(rows, start=1):
        row["dense_rank"] = rank
    return rows


def _run_lexical_recall(
    query_understanding: QueryUnderstanding,
    top_k_lexical: int,
    database_url: str,
    lexical_score_threshold: float,
    debug_recorder: DebugRecorder | None,
) -> tuple[list[dict[str, Any]], int]:
    """Run lexical recall over section chunks using generic Chinese-friendly matching."""
    search_terms = _derive_search_terms(query_understanding)
    lowered_terms = [term.casefold() for term in search_terms]
    if debug_recorder is not None:
        debug_recorder.log_event(
            "lexical_search_terms",
            normalized_query=query_understanding.normalized_query,
            search_terms=search_terms,
        )

    with _debug_stage(debug_recorder, "lexical_recall"):
        psycopg, dict_row = _import_psycopg()
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH scored AS (
                        SELECT
                            chunk_id::text AS chunk_id,
                            doc_id,
                            section_id::text AS section_id,
                            node_id,
                            title,
                            title_path,
                            page_index,
                            chunk_index,
                            chunk_text,
                            (
                                SELECT COUNT(*)
                                FROM unnest(%s::text[]) AS term
                                WHERE char_length(term) >= 2
                                  AND lower(title) LIKE '%%' || term || '%%'
                            ) AS title_term_hits,
                            (
                                SELECT COUNT(*)
                                FROM unnest(%s::text[]) AS term
                                WHERE char_length(term) >= 2
                                  AND lower(title_path) LIKE '%%' || term || '%%'
                            ) AS title_path_term_hits,
                            (
                                SELECT COUNT(*)
                                FROM unnest(%s::text[]) AS term
                                WHERE char_length(term) >= 2
                                  AND lower(search_text) LIKE '%%' || term || '%%'
                            ) AS search_text_term_hits,
                            word_similarity(lower(title), %s) AS title_word_similarity,
                            word_similarity(lower(title_path), %s) AS title_path_word_similarity,
                            word_similarity(lower(search_text), %s) AS search_text_word_similarity,
                            similarity(lower(title), %s) AS title_similarity,
                            similarity(lower(title_path), %s) AS title_path_similarity,
                            similarity(lower(search_text), %s) AS search_text_similarity
                        FROM section_chunks
                    ),
                    filtered AS (
                        SELECT
                            *,
                            (
                                title_term_hits * 5.0 +
                                title_path_term_hits * 3.0 +
                                search_text_term_hits * 1.5 +
                                title_word_similarity * 2.0 +
                                title_path_word_similarity * 1.25 +
                                search_text_word_similarity * 1.0 +
                                title_similarity * 1.0 +
                                title_path_similarity * 0.75 +
                                search_text_similarity * 0.5
                            ) AS lexical_score
                        FROM scored
                        WHERE
                            title_term_hits > 0
                            OR title_path_term_hits > 0
                            OR search_text_term_hits > 0
                            OR GREATEST(
                                title_word_similarity,
                                title_path_word_similarity,
                                search_text_word_similarity,
                                title_similarity,
                                title_path_similarity,
                                search_text_similarity
                            ) >= %s
                    )
                    SELECT
                        chunk_id,
                        doc_id,
                        section_id,
                        node_id,
                        title,
                        title_path,
                        page_index,
                        chunk_index,
                        chunk_text,
                        lexical_score,
                        COUNT(*) OVER () AS candidate_count
                    FROM filtered
                    ORDER BY lexical_score DESC, doc_id, chunk_id
                    LIMIT %s
                    """,
                    (
                        lowered_terms,
                        lowered_terms,
                        lowered_terms,
                        query_understanding.normalized_query.casefold(),
                        query_understanding.normalized_query.casefold(),
                        query_understanding.normalized_query.casefold(),
                        query_understanding.normalized_query.casefold(),
                        query_understanding.normalized_query.casefold(),
                        query_understanding.normalized_query.casefold(),
                        lexical_score_threshold,
                        int(top_k_lexical),
                    ),
                )
                rows = list(cursor.fetchall())
        candidate_count = int(rows[0]["candidate_count"]) if rows else 0
        for rank, row in enumerate(rows, start=1):
            row["lexical_rank"] = rank
            row.pop("candidate_count", None)
        if debug_recorder is not None:
            debug_recorder.log_event(
                "lexical_recall_stats",
                candidate_count=candidate_count,
                lexical_hit_count=len(rows),
            )
        return rows, candidate_count


def _fuse_chunk_hits(
    *,
    dense_hits: list[dict[str, Any]],
    lexical_hits: list[dict[str, Any]],
    top_k_fused_chunks: int,
    rrf_k: int,
) -> list[RetrievalChunkHit]:
    """Fuse dense and lexical hits with reciprocal rank fusion."""
    fused: dict[str, dict[str, Any]] = {}
    for row in dense_hits:
        item = fused.setdefault(row["chunk_id"], _base_hit_payload(row))
        item["dense_rank"] = int(row["dense_rank"])
        item["dense_score"] = float(row["dense_score"])
    for row in lexical_hits:
        item = fused.setdefault(row["chunk_id"], _base_hit_payload(row))
        item["lexical_rank"] = int(row["lexical_rank"])
        item["lexical_score"] = float(row["lexical_score"])

    results: list[RetrievalChunkHit] = []
    for item in fused.values():
        rrf_score = 0.0
        if item["dense_rank"] is not None:
            rrf_score += 1.0 / (rrf_k + int(item["dense_rank"]))
        if item["lexical_rank"] is not None:
            rrf_score += 1.0 / (rrf_k + int(item["lexical_rank"]))
        results.append(
            RetrievalChunkHit(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                section_id=item["section_id"],
                node_id=item["node_id"],
                title=item["title"],
                title_path=item["title_path"],
                page_index=item["page_index"],
                chunk_index=item["chunk_index"],
                chunk_text=item["chunk_text"],
                dense_rank=item["dense_rank"],
                dense_score=item["dense_score"],
                lexical_rank=item["lexical_rank"],
                lexical_score=item["lexical_score"],
                rrf_score=round(rrf_score, 8),
            )
        )
    results.sort(
        key=lambda item: (
            item.rrf_score,
            -(item.dense_score or 0.0),
            -(item.lexical_score or 0.0),
        ),
        reverse=True,
    )
    return results[:top_k_fused_chunks]


def _aggregate_candidates(
    *,
    fused_hits: list[RetrievalChunkHit],
    database_url: str,
    top_k_docs: int,
    top_k_sections_per_doc: int,
    top_k_chunks_per_section: int,
    doc_score_chunk_limit: int,
    section_score_chunk_limit: int,
) -> tuple[list[DocCandidate], list[SectionCandidate]]:
    """Aggregate fused chunk hits into doc and section candidates."""
    doc_groups: dict[str, list[RetrievalChunkHit]] = {}
    section_groups: dict[tuple[str, str], list[RetrievalChunkHit]] = {}
    for hit in fused_hits:
        doc_groups.setdefault(hit.doc_id, []).append(hit)
        section_groups.setdefault((hit.doc_id, hit.section_id), []).append(hit)

    all_section_metadata = _load_section_metadata(
        database_url=database_url,
        section_ids=[section_id for _doc_id, section_id in section_groups],
    )

    section_candidates_all: list[SectionCandidate] = []
    section_candidates_by_doc: dict[str, list[SectionCandidate]] = {}
    for (doc_id, section_id), hits in section_groups.items():
        hits.sort(key=lambda item: item.rrf_score, reverse=True)
        metadata = all_section_metadata.get(section_id, {})
        candidate = SectionCandidate(
            doc_id=doc_id,
            section_id=section_id,
            node_id=str(metadata.get("node_id") or hits[0].node_id),
            title=str(metadata.get("title") or hits[0].title),
            depth=int(metadata.get("depth") or 0),
            summary=str(metadata.get("summary") or ""),
            section_score=round(sum(hit.rrf_score for hit in hits[:section_score_chunk_limit]), 8),
            matched_chunk_count=len(hits),
            supporting_chunks=[hit.to_dict() for hit in hits[:top_k_chunks_per_section]],
        )
        section_candidates_all.append(candidate)
        section_candidates_by_doc.setdefault(doc_id, []).append(candidate)

    for doc_id, candidates in section_candidates_by_doc.items():
        candidates.sort(key=lambda item: item.section_score, reverse=True)
        section_candidates_by_doc[doc_id] = candidates[:top_k_sections_per_doc]

    doc_candidates: list[DocCandidate] = []
    for doc_id, hits in doc_groups.items():
        hits.sort(key=lambda item: item.rrf_score, reverse=True)
        top_sections = section_candidates_by_doc.get(doc_id, [])
        doc_candidates.append(
            DocCandidate(
                doc_id=doc_id,
                doc_score=round(sum(hit.rrf_score for hit in hits[:doc_score_chunk_limit]), 8),
                matched_chunk_count=len(hits),
                matched_section_count=len({hit.section_id for hit in hits}),
                top_section_ids=[section.section_id for section in top_sections],
                section_candidates=top_sections,
            )
        )
    doc_candidates.sort(key=lambda item: item.doc_score, reverse=True)
    doc_candidates = doc_candidates[:top_k_docs]

    selected_doc_ids = {item.doc_id for item in doc_candidates}
    section_candidates = [
        section
        for section in section_candidates_all
        if section.doc_id in selected_doc_ids and section.section_id in set(
            section_id for doc in doc_candidates for section_id in doc.top_section_ids
        )
    ]
    section_candidates.sort(key=lambda item: (item.doc_id, item.section_score), reverse=False)
    ordered_section_candidates: list[SectionCandidate] = []
    for doc_candidate in doc_candidates:
        ordered_section_candidates.extend(doc_candidate.section_candidates)
    return doc_candidates, ordered_section_candidates


def _load_section_metadata(*, database_url: str, section_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Load section metadata from document_sections for aggregation output."""
    if not section_ids:
        return {}
    psycopg, dict_row = _import_psycopg()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    section_id::text AS section_id,
                    doc_id,
                    node_id,
                    title,
                    depth,
                    summary
                FROM document_sections
                WHERE section_id = ANY(%s::uuid[])
                """,
                (section_ids,),
            )
            return {row["section_id"]: dict(row) for row in cursor.fetchall()}


def _load_tree_sections_for_docs(
    *,
    database_url: str,
    doc_ids: list[str],
) -> tuple[dict[str, dict[str, _Stage3TreeSection]], dict[str, dict[str | None, list[str]]]]:
    """Load full document trees for selected docs and reconstruct title paths."""
    if not doc_ids:
        return {}, {}
    psycopg, dict_row = _import_psycopg()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    section_id::text AS section_id,
                    parent_id::text AS parent_id,
                    doc_id,
                    node_id,
                    title,
                    depth,
                    summary
                FROM document_sections
                WHERE doc_id = ANY(%s)
                ORDER BY doc_id, depth, title
                """,
                (doc_ids,),
            )
            rows = list(cursor.fetchall())

    raw_sections_by_doc: dict[str, dict[str, dict[str, Any]]] = {}
    children_by_doc: dict[str, dict[str | None, list[str]]] = {}
    for row in rows:
        doc_id = str(row["doc_id"])
        section_id = str(row["section_id"])
        parent_id = str(row["parent_id"]) if row["parent_id"] is not None else None
        raw_sections_by_doc.setdefault(doc_id, {})[section_id] = {
            "section_id": section_id,
            "parent_id": parent_id,
            "doc_id": doc_id,
            "node_id": str(row["node_id"]),
            "title": str(row["title"]),
            "depth": int(row["depth"] or 0),
            "summary": str(row["summary"] or ""),
        }
        children_by_doc.setdefault(doc_id, {}).setdefault(parent_id, []).append(section_id)

    sections_by_doc: dict[str, dict[str, _Stage3TreeSection]] = {}
    for doc_id, raw_sections in raw_sections_by_doc.items():
        title_path_cache: dict[str, str] = {}

        def build_title_path(section_id: str) -> str:
            cached = title_path_cache.get(section_id)
            if cached is not None:
                return cached
            row = raw_sections[section_id]
            parent_id = row["parent_id"]
            if not parent_id or parent_id not in raw_sections:
                title_path = row["title"]
            else:
                title_path = f"{build_title_path(parent_id)} > {row['title']}"
            title_path_cache[section_id] = title_path
            return title_path

        sections_by_doc[doc_id] = {
            section_id: _Stage3TreeSection(
                section_id=section_id,
                parent_id=row["parent_id"],
                doc_id=doc_id,
                node_id=row["node_id"],
                title=row["title"],
                depth=row["depth"],
                summary=row["summary"],
                title_path=build_title_path(section_id),
            )
            for section_id, row in raw_sections.items()
        }
    return sections_by_doc, children_by_doc


def _build_stage3_candidate_pool(
    *,
    doc_id: str,
    doc_sections: dict[str, _Stage3TreeSection],
    children_map: dict[str | None, list[str]],
    anchor_sections: list[SectionCandidate],
    stage2_section_lookup: dict[tuple[str, str], SectionCandidate],
    top_k_tree_sections_per_doc: int,
    whole_doc_fallback: bool,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Build the tree-local candidate pool for one document from selected anchors."""
    candidates: dict[str, dict[str, Any]] = {}

    def update_candidate(
        section_id: str,
        *,
        relation_to_anchor: Literal["anchor", "descendant", "ancestor", "sibling", "doc_fallback"],
        anchor_section: SectionCandidate | None,
    ) -> None:
        section = doc_sections.get(section_id)
        if section is None:
            return
        stage2_section = stage2_section_lookup.get((doc_id, section_id))
        stage2_score = stage2_section.section_score if stage2_section is not None else 0.0
        supporting_chunks = (
            stage2_section.supporting_chunks
            if stage2_section is not None
            else (anchor_section.supporting_chunks if anchor_section is not None else [])
        )
        reason_codes = [f"relation:{relation_to_anchor}"]
        if stage2_section is not None:
            reason_codes.append("stage2_section_hit")
        elif anchor_section is not None:
            reason_codes.append("anchor_support_transfer")
        anchor_section_id = anchor_section.section_id if anchor_section is not None else None
        candidate = {
            "section": section,
            "anchor_section_id": anchor_section_id,
            "relation_to_anchor": relation_to_anchor,
            "reason_codes": reason_codes,
            "stage2_section_score": float(stage2_score),
            "supporting_chunks": supporting_chunks,
        }
        existing = candidates.get(section_id)
        if existing is None:
            candidates[section_id] = candidate
            return

        current_priority = STAGE3_RELATION_ORDER[relation_to_anchor]
        existing_priority = STAGE3_RELATION_ORDER[existing["relation_to_anchor"]]
        if current_priority > existing_priority or (
            current_priority == existing_priority and stage2_score > existing["stage2_section_score"]
        ):
            reason_codes = _deduplicate_strings([*existing["reason_codes"], *reason_codes])
            candidate["reason_codes"] = reason_codes
            candidates[section_id] = candidate
        else:
            existing["reason_codes"] = _deduplicate_strings([*existing["reason_codes"], *reason_codes])

    for anchor_section in anchor_sections:
        anchor_id = anchor_section.section_id
        if anchor_id not in doc_sections:
            continue
        update_candidate(anchor_id, relation_to_anchor="anchor", anchor_section=anchor_section)

        stack = list(children_map.get(anchor_id, []))
        while stack:
            child_id = stack.pop()
            update_candidate(child_id, relation_to_anchor="descendant", anchor_section=anchor_section)
            stack.extend(children_map.get(child_id, []))

        current_parent = doc_sections[anchor_id].parent_id
        ancestor_ids: list[str] = []
        while current_parent:
            if current_parent not in doc_sections:
                break
            ancestor_ids.append(current_parent)
            update_candidate(current_parent, relation_to_anchor="ancestor", anchor_section=anchor_section)
            current_parent = doc_sections[current_parent].parent_id

        sibling_parent_ids = [doc_sections[anchor_id].parent_id]
        sibling_parent_ids.extend(doc_sections[ancestor_id].parent_id for ancestor_id in ancestor_ids)
        for parent_id in sibling_parent_ids:
            if parent_id is None:
                continue
            for sibling_id in children_map.get(parent_id, []):
                if sibling_id == anchor_id or sibling_id in ancestor_ids:
                    continue
                update_candidate(sibling_id, relation_to_anchor="sibling", anchor_section=anchor_section)

    used_whole_doc_fallback = False
    if len(candidates) < top_k_tree_sections_per_doc and whole_doc_fallback:
        used_whole_doc_fallback = True
        for section_id in doc_sections:
            if section_id in candidates:
                continue
            update_candidate(section_id, relation_to_anchor="doc_fallback", anchor_section=None)
    return candidates, used_whole_doc_fallback


def _score_stage3_candidates(
    *,
    query_understanding: QueryUnderstanding,
    search_terms: list[str],
    candidate_pool: dict[str, dict[str, Any]],
    top_k_tree_sections_per_doc: int,
    stage3_relation_priors: dict[str, float],
) -> list[LocalizedSection]:
    """Score tree-local Stage 3 candidates with deterministic heuristics."""
    localized_sections: list[LocalizedSection] = []
    years = [str(year) for year in query_understanding.time_scope.get("years", [])]
    quarters = [str(value) for value in query_understanding.time_scope.get("quarters", [])]

    for candidate in candidate_pool.values():
        section = candidate["section"]
        title_hits = _count_term_hits(section.title, search_terms)
        title_path_hits = _count_term_hits(section.title_path, search_terms)
        summary_hits = _count_term_hits(section.summary, search_terms)
        time_hits = _count_time_hits(
            text=" ".join(filter(None, [section.title, section.title_path, section.summary])),
            years=years,
            quarters=quarters,
        )
        relation_to_anchor = candidate["relation_to_anchor"]
        stage2_score = float(candidate["stage2_section_score"])
        localization_score = (
            stage3_relation_priors[relation_to_anchor]
            + stage2_score * 100.0
            + title_hits * 3.0
            + title_path_hits * 2.0
            + summary_hits * 1.0
            + time_hits * 1.5
        )
        reason_codes = list(candidate["reason_codes"])
        if title_hits:
            reason_codes.append("title_term_match")
        if title_path_hits:
            reason_codes.append("title_path_term_match")
        if summary_hits:
            reason_codes.append("summary_term_match")
        if time_hits:
            reason_codes.append("time_scope_overlap")
        if stage2_score > 0:
            reason_codes.append("stage2_score_boost")
        localized_sections.append(
            LocalizedSection(
                doc_id=section.doc_id,
                section_id=section.section_id,
                node_id=section.node_id,
                parent_id=section.parent_id,
                title=section.title,
                depth=section.depth,
                summary=section.summary,
                title_path=section.title_path,
                localization_score=round(localization_score, 8),
                stage2_section_score=round(stage2_score, 8),
                anchor_section_id=candidate["anchor_section_id"],
                relation_to_anchor=relation_to_anchor,
                reason_codes=_deduplicate_strings(reason_codes),
                supporting_chunks=candidate["supporting_chunks"],
            )
        )

    localized_sections.sort(
        key=lambda item: (
            item.localization_score,
            STAGE3_RELATION_ORDER[item.relation_to_anchor],
            -item.depth,
            item.title_path,
        ),
        reverse=True,
    )
    return localized_sections[:top_k_tree_sections_per_doc]


def _rerank_stage3_sections_with_llm(
    *,
    query_understanding: QueryUnderstanding,
    anchor_sections: list[SectionCandidate],
    shortlisted_sections: list[LocalizedSection],
    top_k_tree_sections_per_doc: int,
    llm_client: QwenChatClient,
    rerank_model: str,
    debug_recorder: DebugRecorder | None,
    doc_id: str,
) -> list[LocalizedSection] | None:
    """Rerank one document's shortlisted sections with one LLM call."""
    prompt = f"""
You are ranking sections inside one research report for retrieval.
Return only JSON with this shape:
{{
  "ranked_sections": [
    {{"section_id": "id", "reason": "short reason"}}
  ]
}}

Use only section_id values that appear in the shortlist.
Prefer sections that are most likely to directly answer the query.

Query understanding:
{json.dumps(query_understanding.to_dict(), ensure_ascii=False)}

Anchor sections:
{json.dumps([section.to_dict() for section in anchor_sections], ensure_ascii=False)}

Shortlisted sections:
{json.dumps([section.to_dict() for section in shortlisted_sections], ensure_ascii=False)}
""".strip()
    try:
        response = llm_client.completion(rerank_model, prompt)
        payload = _extract_json_payload(response)
        ranked_sections = payload.get("ranked_sections")
        if not isinstance(ranked_sections, list):
            raise ValueError("ranked_sections must be a list")
    except Exception as exc:  # noqa: PERF203
        if debug_recorder is not None:
            debug_recorder.log_event(
                "stage3_llm_rerank_error",
                doc_id=doc_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        return None

    shortlist_lookup = {section.section_id: section for section in shortlisted_sections}
    llm_rankings: list[tuple[LocalizedSection, str, int]] = []
    for rank, item in enumerate(ranked_sections, start=1):
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("section_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        shortlisted = shortlist_lookup.get(section_id)
        if shortlisted is None:
            continue
        llm_rankings.append((shortlisted, reason, rank))

    if not llm_rankings:
        if debug_recorder is not None:
            debug_recorder.log_event("stage3_llm_rerank_empty", doc_id=doc_id)
        return None

    max_base_score = max(section.localization_score for section in shortlisted_sections) or 1.0
    bonus_scale = max(1.0, max_base_score * 0.6)
    llm_bonus_by_section = {
        section.section_id: bonus_scale / float(rank)
        for section, _reason, rank in llm_rankings
    }
    llm_reason_by_section = {
        section.section_id: reason
        for section, reason, _rank in llm_rankings
        if reason
    }
    reranked_sections: list[LocalizedSection] = []
    for section in shortlisted_sections:
        reason_codes = list(section.reason_codes)
        if section.section_id in llm_bonus_by_section:
            reason_codes.append("llm_rerank_selected")
        reranked_sections.append(
            LocalizedSection(
                doc_id=section.doc_id,
                section_id=section.section_id,
                node_id=section.node_id,
                parent_id=section.parent_id,
                title=section.title,
                depth=section.depth,
                summary=section.summary,
                title_path=section.title_path,
                localization_score=round(section.localization_score + llm_bonus_by_section.get(section.section_id, 0.0), 8),
                stage2_section_score=section.stage2_section_score,
                anchor_section_id=section.anchor_section_id,
                relation_to_anchor=section.relation_to_anchor,
                reason_codes=_deduplicate_strings(reason_codes),
                supporting_chunks=section.supporting_chunks,
            )
        )

    reranked_sections.sort(
        key=lambda item: (
            item.localization_score,
            STAGE3_RELATION_ORDER[item.relation_to_anchor],
            -item.depth,
            item.title_path,
        ),
        reverse=True,
    )
    reranked_sections = reranked_sections[:top_k_tree_sections_per_doc]
    if debug_recorder is not None:
        debug_recorder.log_event(
            "stage3_llm_rerank",
            doc_id=doc_id,
            ranked_section_ids=[section.section_id for section in reranked_sections],
            llm_reasons=llm_reason_by_section,
        )
    return reranked_sections


def _create_debug_recorder(*, debug_log: bool, debug_log_dir: str | None) -> DebugRecorder | None:
    """Create a debug recorder for retrieval runs when requested."""
    if not debug_log:
        return None
    if debug_log_dir:
        return DebugRecorder(Path(debug_log_dir).expanduser().resolve())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DebugRecorder(REPO_ROOT / "DemoIndex" / "artifacts" / "retrieval" / timestamp / "debug")


def _load_retrieval_profile_aliases(
    *,
    debug_recorder: DebugRecorder | None,
    retrieval_profile_path: str | None,
) -> dict[str, dict[str, list[str]]]:
    """Load optional external alias mappings for query understanding."""
    profile_path = retrieval_profile_path or os.getenv(DEFAULT_PROFILE_ENV_VAR)
    if not profile_path:
        if debug_recorder is not None:
            debug_recorder.log_event("retrieval_profile_loaded", enabled=False, path=None)
        return {field_name: {} for field_name in PROFILE_FIELD_NAMES}

    resolved_path = Path(profile_path).expanduser().resolve()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Retrieval profile must be a JSON object: {resolved_path}")

    alias_maps = {
        field_name: _normalize_profile_alias_mapping(payload.get(field_name))
        for field_name in PROFILE_FIELD_NAMES
    }
    if debug_recorder is not None:
        debug_recorder.log_event(
            "retrieval_profile_loaded",
            enabled=True,
            path=str(resolved_path),
            available_fields=[field_name for field_name, values in alias_maps.items() if values],
        )
    return alias_maps


def _normalize_stage3_relation_priors(overrides: dict[str, float] | None) -> dict[str, float]:
    """Normalize Stage 3 relation-prior overrides against the default key set."""
    priorities = dict(STAGE3_RELATION_PRIOR)
    if not overrides:
        return priorities
    for key, value in overrides.items():
        if key not in priorities:
            raise ValueError(f"Unsupported Stage 3 relation prior key: {key}")
        priorities[key] = float(value)
    return priorities


def _normalize_profile_alias_mapping(value: Any) -> dict[str, list[str]]:
    """Normalize one alias mapping payload from the optional retrieval profile."""
    if not isinstance(value, dict):
        return {}
    results: dict[str, list[str]] = {}
    for canonical, aliases in value.items():
        canonical_text = str(canonical).strip()
        if not canonical_text:
            continue
        normalized_aliases = _normalize_string_list(aliases)
        if canonical_text not in normalized_aliases:
            normalized_aliases.insert(0, canonical_text)
        results[canonical_text] = normalized_aliases
    return results


def _debug_stage(debug_recorder: DebugRecorder | None, stage_name: str, **metadata: Any):
    """Return a no-op or structured debug stage context manager."""
    if debug_recorder is None:
        return _NoOpContextManager()
    return debug_recorder.stage(stage_name, **metadata)


def _normalize_query(query: str) -> str:
    """Normalize whitespace in a retrieval query."""
    return " ".join(str(query or "").split()).strip()


def _detect_language(query: str) -> str:
    """Infer the dominant language class for a query."""
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", query))
    has_ascii = bool(re.search(r"[A-Za-z]", query))
    if has_cjk and has_ascii:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_ascii:
        return "en"
    return "unknown"


def _detect_intent(query: str) -> str:
    """Infer the retrieval intent from common analysis keywords."""
    lowered = query.casefold()
    for intent, patterns in INTENT_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return intent
    return "general"


def _extract_terms(query: str) -> list[str]:
    """Extract meaningful keyword terms from one query."""
    candidates = re.findall(r"[A-Za-z0-9\+\-\.]{2,}|[\u4e00-\u9fff]{2,}", query)
    results: list[str] = []
    for token in candidates:
        for normalized in _expand_term_candidates(token):
            if normalized not in results:
                results.append(normalized)
    return results


def _extract_time_scope(query: str) -> dict[str, Any]:
    """Extract year and quarter hints from one query."""
    years = sorted({int(match) for match in re.findall(r"\b(20\d{2})\b", query)})
    quarter_mentions = re.findall(r"(?:\b(20\d{2})\s*)?(Q[1-4]|[1-4]\s*季度|第[一二三四1-4]季度)", query, flags=re.I)
    quarters: list[str] = []
    raw_mentions: list[str] = []
    for year_text, quarter_text in quarter_mentions:
        quarter = _normalize_quarter(quarter_text)
        if year_text:
            quarters.append(f"{year_text}{quarter}")
            raw_mentions.append(f"{year_text} {quarter_text}".strip())
        else:
            quarters.append(quarter)
            raw_mentions.append(quarter_text)
    raw_mentions.extend(re.findall(r"\b20\d{2}\b", query))
    return {
        "years": years,
        "quarters": _deduplicate_strings(quarters),
        "raw_mentions": _deduplicate_strings(raw_mentions),
    }


def _normalize_quarter(value: str) -> str:
    """Normalize quarter expressions to `Qn`."""
    lowered = value.casefold().replace(" ", "")
    mapping = {
        "1季度": "Q1",
        "2季度": "Q2",
        "3季度": "Q3",
        "4季度": "Q4",
        "第一季度": "Q1",
        "第二季度": "Q2",
        "第三季度": "Q3",
        "第四季度": "Q4",
        "q1": "Q1",
        "q2": "Q2",
        "q3": "Q3",
        "q4": "Q4",
    }
    return mapping.get(lowered, value.upper())


def _match_aliases(query: str, alias_mapping: dict[str, list[str]]) -> list[str]:
    """Return canonical values whose aliases appear in the query."""
    lowered = query.casefold()
    matches: list[str] = []
    for canonical, aliases in alias_mapping.items():
        for alias in aliases:
            alias_lower = alias.casefold()
            if _alias_matches_query(lowered, alias_lower):
                matches.append(canonical)
                break
    return matches


def _alias_matches_query(query: str, alias: str) -> bool:
    """Return whether one canonical alias appears in the query."""
    if re.search(r"[A-Za-z]", alias):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
        return bool(re.search(pattern, query))
    return alias in query


def _needs_llm_enrichment(query_understanding: QueryUnderstanding) -> str | None:
    """Return an enrichment reason when one LLM pass is justified."""
    sparse_time = not query_understanding.time_scope.get("years") and not query_understanding.time_scope.get("quarters")
    if query_understanding.language == "unknown":
        return "unknown_language"
    if len(query_understanding.normalized_query) <= 4:
        return "very_short_query"
    if len(query_understanding.terms) < 2 and sparse_time:
        return "insufficient_terms_and_time_scope"
    if query_understanding.intent == "general" and len(query_understanding.terms) < 3 and sparse_time:
        return "generic_intent_with_sparse_terms"
    return None


def _canonicalize_values(values: list[str], alias_mapping: dict[str, list[str]]) -> list[str]:
    """Map free-form values onto canonical aliases when possible."""
    if not values:
        return []
    results: list[str] = []
    alias_lookup = {
        alias.casefold(): canonical
        for canonical, aliases in alias_mapping.items()
        for alias in [canonical, *aliases]
    }
    for value in values:
        canonical = alias_lookup.get(str(value).casefold(), str(value))
        if canonical not in results:
            results.append(canonical)
    return results


def _expand_term_candidates(token: str) -> list[str]:
    """Expand one raw regex token into generic searchable terms."""
    normalized = str(token).strip()
    if not normalized:
        return []
    if " " in normalized or (
        re.search(r"[A-Za-z0-9]", normalized) and re.search(r"[\u4e00-\u9fff]", normalized)
    ):
        results: list[str] = []
        for part in re.findall(r"[A-Za-z0-9\+\-\.]{2,}|[\u4e00-\u9fff]{2,}", normalized):
            for expanded in _expand_term_candidates(part):
                if expanded not in results:
                    results.append(expanded)
        return results
    if re.fullmatch(r"[A-Za-z0-9\+\-\.]{2,}", normalized):
        lowered = normalized.casefold()
        if lowered in STOP_TERMS:
            return []
        return [normalized]

    cleaned = LEADING_CJK_CONNECTOR_RE.sub("", normalized)
    cleaned = TRAILING_CJK_CONNECTOR_RE.sub("", cleaned)
    if len(cleaned) < 2:
        return []

    results = [cleaned]
    if len(cleaned) >= 3:
        prefix = cleaned[:2]
        suffix = cleaned[-2:]
        if prefix not in results:
            results.append(prefix)
        if suffix not in results:
            results.append(suffix)
    return [item for item in results if item.casefold() not in STOP_TERMS]


def _derive_search_terms(query_understanding: QueryUnderstanding) -> list[str]:
    """Derive generic lexical search terms from one parsed query."""
    results: list[str] = []
    raw_candidates: list[str] = [query_understanding.normalized_query, *query_understanding.terms]
    raw_candidates.extend(str(year) for year in query_understanding.time_scope.get("years", []))
    raw_candidates.extend(query_understanding.time_scope.get("quarters", []))

    for candidate in raw_candidates:
        for token in _expand_term_candidates(candidate):
            normalized = token.casefold() if re.search(r"[A-Za-z]", token) else token
            if len(normalized) < 2:
                continue
            if normalized in results:
                continue
            results.append(normalized)
    return results


def _count_term_hits(text: str, search_terms: list[str]) -> int:
    """Count unique search-term hits inside one text field."""
    lowered = str(text or "").casefold()
    hit_count = 0
    for term in search_terms:
        if len(term) < 2:
            continue
        if term.casefold() in lowered:
            hit_count += 1
    return hit_count


def _count_time_hits(*, text: str, years: list[str], quarters: list[str]) -> int:
    """Count year and quarter overlaps between the query and one text field."""
    lowered = str(text or "").casefold()
    hit_count = 0
    for year in years:
        if year and year.casefold() in lowered:
            hit_count += 1
    for quarter in quarters:
        if quarter and quarter.casefold() in lowered:
            hit_count += 1
    return hit_count


def _normalize_string_list(value: Any) -> list[str]:
    """Normalize one possibly-scalar JSON field into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return _deduplicate_strings([str(item).strip() for item in value if str(item).strip()])
    text = str(value).strip()
    if not text:
        return []
    return [text]


def _normalize_time_scope(value: Any) -> dict[str, Any]:
    """Normalize the JSON time scope payload into the local schema."""
    if not isinstance(value, dict):
        return {"years": [], "quarters": [], "raw_mentions": []}
    years = []
    for year in value.get("years", []) if isinstance(value.get("years"), list) else []:
        try:
            years.append(int(year))
        except (TypeError, ValueError):
            continue
    quarters = _normalize_string_list(value.get("quarters"))
    raw_mentions = _normalize_string_list(value.get("raw_mentions"))
    return {
        "years": sorted(set(years)),
        "quarters": _deduplicate_strings([_normalize_quarter(item) for item in quarters]),
        "raw_mentions": _deduplicate_strings(raw_mentions),
    }


def _deduplicate_strings(values: list[str]) -> list[str]:
    """Return strings with stable-order deduplication."""
    results: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in results:
            results.append(cleaned)
    return results


def _extract_json_payload(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output."""
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError(f"Model did not return JSON: {text}")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object, got: {type(payload).__name__}")
    return payload


def _base_hit_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Create the common payload used before dense/lexical fusion."""
    return {
        "chunk_id": str(row["chunk_id"]),
        "doc_id": str(row["doc_id"]),
        "section_id": str(row["section_id"]),
        "node_id": str(row["node_id"]),
        "title": str(row["title"]),
        "title_path": str(row["title_path"]),
        "page_index": int(row["page_index"]) if row["page_index"] is not None else None,
        "chunk_index": int(row["chunk_index"]),
        "chunk_text": str(row["chunk_text"]),
        "dense_rank": None,
        "dense_score": None,
        "lexical_rank": None,
        "lexical_score": None,
    }


def _vector_literal(values: list[float]) -> str:
    """Convert a Python vector into a PostgreSQL vector literal."""
    return "[" + ",".join(f"{float(value):.10f}" for value in values) + "]"


def _import_psycopg():
    """Import psycopg lazily together with the dict row factory."""
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Retrieval requires the `psycopg` package. Install it in the active environment first."
        ) from exc
    return psycopg, dict_row


class _NoOpContextManager:
    """Provide a no-op context manager when debug logging is disabled."""

    def __enter__(self) -> None:
        """Enter the no-op context."""
        return None

    def __exit__(self, _exc_type, _exc, _tb) -> bool:
        """Exit the no-op context without suppressing exceptions."""
        return False
