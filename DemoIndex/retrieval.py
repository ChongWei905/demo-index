"""Stage 1 and Stage 2 retrieval helpers for DemoIndex."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

METRIC_ALIASES = {
    "CPI": ["cpi", "cost per install", "单次安装成本", "每次安装成本", "获客成本"],
    "ROAS": ["roas"],
    "ARPU": ["arpu", "每用户平均收入"],
    "ARPMAU": ["arpamau", "arpmau"],
    "Retention": ["retention", "留存", "d1", "d7", "d30"],
    "IAA": ["iaa", "广告变现", "in-app advertising"],
    "IAP": ["iap", "内购", "应用内购买", "in-app purchase"],
    "CTR": ["ctr", "点击率"],
    "CPM": ["cpm"],
    "LTV": ["ltv"],
    "DAU": ["dau"],
    "MAU": ["mau"],
}

REGION_ALIASES = {
    "Global": ["global", "全球"],
    "North America": ["north america", "北美"],
    "Europe": ["europe", "欧洲"],
    "LATAM": ["latam", "latin america", "拉美", "拉丁美洲"],
    "MENA": ["mena", "middle east", "north africa", "中东", "中东北非"],
    "APAC": ["apac", "asia pacific", "亚太"],
    "China": ["china", "中国"],
    "United States": ["united states", "usa", "u.s.", "美国"],
    "Japan": ["japan", "日本"],
    "South Korea": ["south korea", "韩国"],
    "United Kingdom": ["united kingdom", "uk", "英国"],
    "Germany": ["germany", "德国"],
    "France": ["france", "法国"],
    "India": ["india", "印度"],
    "Brazil": ["brazil", "巴西"],
}

PLATFORM_ALIASES = {
    "iOS": ["ios", "iphone", "ipad"],
    "Android": ["android"],
    "Mobile": ["mobile", "mobile game", "mobile games", "手游", "移动游戏"],
    "PC": ["pc", "desktop"],
    "Console": ["console", "主机"],
    "Steam": ["steam"],
    "App Store": ["app store"],
    "Google Play": ["google play"],
}

GENRE_ALIASES = {
    "RPG": ["rpg", "角色扮演"],
    "Strategy": ["strategy", "策略"],
    "Action": ["action", "动作"],
    "Casual": ["casual", "休闲"],
    "Hybrid Casual": ["hybrid casual", "混合休闲"],
    "Hyper Casual": ["hyper casual", "超休闲"],
    "Casino": ["casino", "博彩"],
    "Racing": ["racing", "竞速", "赛车"],
    "Sports": ["sports", "体育"],
    "Arcade": ["arcade", "街机"],
    "Puzzle": ["puzzle", "解谜"],
    "Simulation": ["simulation", "模拟"],
    "Shooter": ["shooter", "射击"],
    "Card": ["card", "卡牌"],
}

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
    "关于",
    "以及",
    "趋势",
    "分析",
    "问题",
    "游戏",
    "手游",
    "移动游戏",
}


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


def parse_query(query: str, *, use_llm: bool = True) -> QueryUnderstanding:
    """Parse one retrieval query into a structured understanding object."""
    return _parse_query_internal(query, use_llm=use_llm, debug_recorder=None)


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
    debug_log: bool = False,
    debug_log_dir: str | None = None,
) -> RetrievalStage12Result:
    """Run Stage 1 and Stage 2 retrieval and return a rich handoff object."""
    if not str(query or "").strip():
        raise ValueError("Query must not be empty.")

    debug_recorder = _create_debug_recorder(debug_log=debug_log, debug_log_dir=debug_log_dir)
    resolved_database_url = resolve_database_url()
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
        )

    try:
        with _debug_stage(debug_recorder, "parse_query"):
            query_understanding = _parse_query_internal(query, use_llm=use_llm_parse, debug_recorder=debug_recorder)
        if debug_recorder is not None:
            debug_recorder.log_event("query_understanding", payload=query_understanding.to_dict())

        lexical_future = None
        with ThreadPoolExecutor(max_workers=2) as executor:
            lexical_future = executor.submit(
                _run_lexical_recall,
                query_understanding.normalized_query,
                top_k_lexical,
                resolved_database_url,
                debug_recorder,
            )
            with _debug_stage(debug_recorder, "dense_recall"):
                load_dashscope_api_key()
                embedding_client = DashScopeEmbeddingClient(
                    model_name=DEFAULT_EMBEDDING_MODEL,
                    debug_recorder=debug_recorder,
                )
                dense_hits = _run_dense_recall(
                    query_understanding.normalized_query,
                    top_k_dense,
                    resolved_database_url,
                    embedding_client,
                )
            lexical_hits = lexical_future.result()

        with _debug_stage(debug_recorder, "fuse_chunk_hits"):
            fused_hits = _fuse_chunk_hits(
                dense_hits=dense_hits,
                lexical_hits=lexical_hits,
                top_k_fused_chunks=top_k_fused_chunks,
            )
        with _debug_stage(debug_recorder, "aggregate_candidates"):
            doc_candidates, section_candidates = _aggregate_candidates(
                fused_hits=fused_hits,
                database_url=resolved_database_url,
                top_k_docs=top_k_docs,
                top_k_sections_per_doc=top_k_sections_per_doc,
                top_k_chunks_per_section=top_k_chunks_per_section,
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
                "rrf_k": DEFAULT_RRF_K,
                "embedding_model": DEFAULT_EMBEDDING_MODEL,
            },
            "counts": {
                "dense_hits": len(dense_hits),
                "lexical_hits": len(lexical_hits),
                "fused_chunk_hits": len(fused_hits),
                "doc_candidates": len(doc_candidates),
                "section_candidates": len(section_candidates),
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
    finally:
        if debug_recorder is not None:
            debug_recorder.write_summary(total_duration_ms=int((time.perf_counter() - started_at) * 1000))


def _parse_query_internal(
    query: str,
    *,
    use_llm: bool,
    debug_recorder: DebugRecorder | None,
) -> QueryUnderstanding:
    """Parse a query with rule-first extraction and optional LLM enrichment."""
    normalized_query = _normalize_query(query)
    rule_result = QueryUnderstanding(
        raw_query=query,
        normalized_query=normalized_query,
        language=_detect_language(normalized_query),
        intent=_detect_intent(normalized_query),
        terms=_extract_terms(normalized_query),
        metrics=_match_aliases(normalized_query, METRIC_ALIASES),
        regions=_match_aliases(normalized_query, REGION_ALIASES),
        platforms=_match_aliases(normalized_query, PLATFORM_ALIASES),
        genres=_match_aliases(normalized_query, GENRE_ALIASES),
        time_scope=_extract_time_scope(normalized_query),
        llm_enriched=False,
    )
    if not use_llm or not _needs_llm_enrichment(rule_result):
        return rule_result

    try:
        load_dashscope_api_key()
        enriched = _enrich_query_with_llm(rule_result, debug_recorder=debug_recorder)
    except Exception as exc:  # noqa: PERF203
        if debug_recorder is not None:
            debug_recorder.log_event(
                "query_llm_enrichment_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        return rule_result

    return _merge_query_understanding(rule_result, enriched)


def _enrich_query_with_llm(
    query_understanding: QueryUnderstanding,
    *,
    debug_recorder: DebugRecorder | None,
) -> QueryUnderstanding:
    """Use one LLM pass to supplement weak or missing query fields."""
    client = QwenChatClient(
        primary_model=DEFAULT_PARSE_MODEL,
        fallback_model=DEFAULT_PARSE_FALLBACK_MODEL,
        debug_recorder=debug_recorder,
    )
    prompt = f"""
You are given a search query for a game-industry research retrieval system.
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
    response = client.completion(DEFAULT_PARSE_MODEL, prompt)
    payload = _extract_json_payload(response)
    return QueryUnderstanding(
        raw_query=query_understanding.raw_query,
        normalized_query=query_understanding.normalized_query,
        language=str(payload.get("language") or query_understanding.language),
        intent=str(payload.get("intent") or query_understanding.intent),
        terms=_normalize_string_list(payload.get("terms")),
        metrics=_canonicalize_values(_normalize_string_list(payload.get("metrics")), METRIC_ALIASES),
        regions=_canonicalize_values(_normalize_string_list(payload.get("regions")), REGION_ALIASES),
        platforms=_canonicalize_values(_normalize_string_list(payload.get("platforms")), PLATFORM_ALIASES),
        genres=_canonicalize_values(_normalize_string_list(payload.get("genres")), GENRE_ALIASES),
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
    normalized_query: str,
    top_k_lexical: int,
    database_url: str,
    debug_recorder: DebugRecorder | None,
) -> list[dict[str, Any]]:
    """Run lexical recall over section chunks using pg_trgm."""
    with _debug_stage(debug_recorder, "lexical_recall"):
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
                        similarity(lower(search_text), lower(%s)) AS lexical_score
                    FROM section_chunks
                    WHERE lower(search_text) %% lower(%s)
                    ORDER BY lexical_score DESC, doc_id, chunk_id
                    LIMIT %s
                    """,
                    (normalized_query, normalized_query, int(top_k_lexical)),
                )
                rows = list(cursor.fetchall())
        for rank, row in enumerate(rows, start=1):
            row["lexical_rank"] = rank
        return rows


def _fuse_chunk_hits(
    *,
    dense_hits: list[dict[str, Any]],
    lexical_hits: list[dict[str, Any]],
    top_k_fused_chunks: int,
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
            rrf_score += 1.0 / (DEFAULT_RRF_K + int(item["dense_rank"]))
        if item["lexical_rank"] is not None:
            rrf_score += 1.0 / (DEFAULT_RRF_K + int(item["lexical_rank"]))
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
            section_score=round(sum(hit.rrf_score for hit in hits[:DEFAULT_SECTION_SCORE_CHUNK_LIMIT]), 8),
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
                doc_score=round(sum(hit.rrf_score for hit in hits[:DEFAULT_DOC_SCORE_CHUNK_LIMIT]), 8),
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


def _create_debug_recorder(*, debug_log: bool, debug_log_dir: str | None) -> DebugRecorder | None:
    """Create a debug recorder for retrieval runs when requested."""
    if not debug_log:
        return None
    if debug_log_dir:
        return DebugRecorder(Path(debug_log_dir).expanduser().resolve())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DebugRecorder(REPO_ROOT / "DemoIndex" / "artifacts" / "retrieval" / timestamp / "debug")


def _debug_stage(debug_recorder: DebugRecorder | None, stage_name: str):
    """Return a no-op or structured debug stage context manager."""
    if debug_recorder is None:
        return _NoOpContextManager()
    return debug_recorder.stage(stage_name)


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
        normalized = token.strip()
        if not normalized:
            continue
        if normalized.casefold() in STOP_TERMS:
            continue
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


def _needs_llm_enrichment(query_understanding: QueryUnderstanding) -> bool:
    """Return whether the rule-based parse is sparse enough to justify one LLM pass."""
    sparse_time = not query_understanding.time_scope.get("years") and not query_understanding.time_scope.get("quarters")
    return any(
        [
            len(query_understanding.terms) < 2,
            not query_understanding.metrics,
            not query_understanding.regions,
            not query_understanding.platforms,
            not query_understanding.genres,
            sparse_time,
            query_understanding.intent == "general",
        ]
    )


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
