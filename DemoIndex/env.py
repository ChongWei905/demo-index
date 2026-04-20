"""Environment and config helpers for DemoIndex."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - bootstrap fallback
    load_dotenv = None


ApiProvider = Literal["dashscope", "openai"]
StageMode = Literal["heuristic", "hybrid"]
BuildPdfStrategy = Literal["auto", "toc_seeded", "pageindex_native", "layout_fallback"]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMOINDEX_ROOT = REPO_ROOT / "DemoIndex"
DEMOINDEX_ENV_PATH = DEMOINDEX_ROOT / ".env"
PAGEINDEX_ROOT = REPO_ROOT / "PageIndex"

DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

DEFAULT_LLM_API_PROVIDER: ApiProvider = "dashscope"
DEFAULT_LLM_TIMEOUT_SECONDS = 180.0
DEFAULT_LLM_MAX_RETRIES = 4
DEFAULT_LLM_RETRY_BASE_SECONDS = 1.5
DEFAULT_LLM_MAX_CONCURRENCY = 4

DEFAULT_EMBEDDING_API_PROVIDER: ApiProvider = "dashscope"
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 180.0
DEFAULT_EMBEDDING_MAX_RETRIES = 4
DEFAULT_EMBEDDING_MAX_BATCH_SIZE = 10
DEFAULT_DASHSCOPE_EMBEDDING_DIMENSIONS = 1024

DEFAULT_BUILD_MODEL = "dashscope/qwen3.6-plus"
DEFAULT_BUILD_FALLBACK_MODEL = "dashscope/qwen3.5-plus"
DEFAULT_BUILD_INCLUDE_SUMMARY = False
DEFAULT_BUILD_WRITE_POSTGRES = False
DEFAULT_BUILD_WRITE_GLOBAL_INDEX = False
DEFAULT_BUILD_GLOBAL_INDEX_MODEL = "text-embedding-v4"
DEFAULT_BUILD_MARKDOWN_LAYOUT = "auto"
DEFAULT_BUILD_PDF_STRATEGY: BuildPdfStrategy = "auto"

DEFAULT_RETRIEVAL_USE_LLM_PARSE = True
DEFAULT_RETRIEVAL_PARSE_MODEL = "dashscope/qwen3.6-plus"
DEFAULT_RETRIEVAL_PARSE_FALLBACK_MODEL = "dashscope/qwen3.5-plus"
DEFAULT_RETRIEVAL_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_RETRIEVAL_TOP_K_DENSE = 60
DEFAULT_RETRIEVAL_TOP_K_LEXICAL = 60
DEFAULT_RETRIEVAL_TOP_K_FUSED_CHUNKS = 80
DEFAULT_RETRIEVAL_TOP_K_DOCS = 10
DEFAULT_RETRIEVAL_TOP_K_SECTIONS_PER_DOC = 3
DEFAULT_RETRIEVAL_TOP_K_CHUNKS_PER_SECTION = 2
DEFAULT_RETRIEVAL_RRF_K = 60
DEFAULT_RETRIEVAL_LEXICAL_SCORE_THRESHOLD = 0.18
DEFAULT_RETRIEVAL_DOC_SCORE_CHUNK_LIMIT = 5
DEFAULT_RETRIEVAL_SECTION_SCORE_CHUNK_LIMIT = 3

DEFAULT_STAGE3_MODE: StageMode = "hybrid"
DEFAULT_STAGE3_TOP_K_TREE_SECTIONS_PER_DOC = 5
DEFAULT_STAGE3_TOP_K_ANCHOR_SECTIONS_PER_DOC = 3
DEFAULT_STAGE3_WHOLE_DOC_FALLBACK = True
DEFAULT_STAGE3_RERANK_MODEL = "dashscope/qwen3.6-plus"
DEFAULT_STAGE3_RERANK_FALLBACK_MODEL = "dashscope/qwen3.5-plus"
DEFAULT_STAGE3_SHORTLIST_SIZE = 8
DEFAULT_STAGE3_RELATION_PRIORS = {
    "anchor": 4.0,
    "descendant": 2.75,
    "ancestor": 2.1,
    "sibling": 1.45,
    "doc_fallback": 0.55,
}

DEFAULT_STAGE4_TOP_K_FOCUS_SECTIONS_PER_DOC = 3
DEFAULT_STAGE4_MAX_ANCESTOR_HOPS = 2
DEFAULT_STAGE4_MAX_DESCENDANT_DEPTH = 1
DEFAULT_STAGE4_MAX_SIBLINGS_PER_FOCUS = 2
DEFAULT_STAGE4_CHUNK_NEIGHBOR_WINDOW = 1
DEFAULT_STAGE4_MAX_EVIDENCE_CHUNKS_PER_FOCUS = 6
DEFAULT_STAGE4_CONTEXT_CHAR_BUDGET = 6000

DEFAULT_STAGE5_RELATION_MODE: StageMode = "heuristic"
DEFAULT_STAGE5_TOP_K_EVIDENCE_PER_DOC = 3
DEFAULT_STAGE5_TOP_K_TOTAL_EVIDENCE = 8
DEFAULT_STAGE5_RELATION_MODEL = "dashscope/qwen3.6-plus"
DEFAULT_STAGE5_RELATION_FALLBACK_MODEL = "dashscope/qwen3.5-plus"
DEFAULT_STAGE5_RELATION_SHORTLIST_SIZE = 8


@dataclass(frozen=True)
class LLMApiConfig:
    """Resolved chat LLM API configuration."""

    provider: ApiProvider
    api_key: str | None
    base_url: str
    timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    max_concurrency: int


@dataclass(frozen=True)
class EmbeddingApiConfig:
    """Resolved embedding API configuration."""

    provider: ApiProvider
    api_key: str | None
    base_url: str
    timeout_seconds: float
    max_retries: int
    max_batch_size: int
    dimensions: int | None


@dataclass(frozen=True)
class BuildDefaults:
    """Resolved build defaults for DemoIndex."""

    model: str
    fallback_model: str
    include_summary: bool
    write_postgres: bool
    write_global_index: bool
    global_index_model: str
    markdown_layout: str
    pdf_strategy: BuildPdfStrategy
    artifacts_dir: str | None


@dataclass(frozen=True)
class RetrievalDefaults:
    """Resolved retrieval defaults for Stage 1 through Stage 5."""

    use_llm_parse: bool
    parse_model: str
    parse_fallback_model: str
    embedding_model: str
    top_k_dense: int
    top_k_lexical: int
    top_k_fused_chunks: int
    top_k_docs: int
    top_k_sections_per_doc: int
    top_k_chunks_per_section: int
    rrf_k: int
    lexical_score_threshold: float
    doc_score_chunk_limit: int
    section_score_chunk_limit: int
    stage3_mode: StageMode
    stage3_top_k_tree_sections_per_doc: int
    stage3_top_k_anchor_sections_per_doc: int
    stage3_whole_doc_fallback: bool
    stage3_rerank_model: str
    stage3_rerank_fallback_model: str
    stage3_shortlist_size: int
    stage3_relation_priors: dict[str, float]
    stage4_top_k_focus_sections_per_doc: int
    stage4_max_ancestor_hops: int
    stage4_max_descendant_depth: int
    stage4_max_siblings_per_focus: int
    stage4_chunk_neighbor_window: int
    stage4_max_evidence_chunks_per_focus: int
    stage4_context_char_budget: int
    stage5_relation_mode: StageMode
    stage5_top_k_evidence_per_doc: int
    stage5_top_k_total_evidence: int
    stage5_relation_model: str
    stage5_relation_fallback_model: str
    stage5_relation_shortlist_size: int


@dataclass(frozen=True)
class DemoIndexConfig:
    """Resolved DemoIndex configuration snapshot."""

    database_url: str | None
    debug_log: bool
    debug_log_dir: str | None
    retrieval_profile_path: str | None
    llm: LLMApiConfig
    embedding: EmbeddingApiConfig
    build: BuildDefaults
    retrieval: RetrievalDefaults


def ensure_pageindex_import_path() -> None:
    """Add the local PageIndex package root to `sys.path` if needed."""
    pageindex_path = str(PAGEINDEX_ROOT)
    if pageindex_path not in sys.path:
        sys.path.insert(0, pageindex_path)


def load_demoindex_env() -> Path:
    """Load `DemoIndex/.env` into the current process when available."""
    if DEMOINDEX_ENV_PATH.exists() and load_dotenv is not None:
        load_dotenv(DEMOINDEX_ENV_PATH, override=False)
    return DEMOINDEX_ENV_PATH


def get_demoindex_config() -> DemoIndexConfig:
    """Load and return the resolved DemoIndex configuration."""
    load_demoindex_env()

    llm_provider = _get_env_provider("DEMOINDEX_LLM_API_PROVIDER", DEFAULT_LLM_API_PROVIDER)
    embedding_provider = _get_env_provider(
        "DEMOINDEX_EMBEDDING_API_PROVIDER",
        DEFAULT_EMBEDDING_API_PROVIDER,
    )

    llm_config = LLMApiConfig(
        provider=llm_provider,
        api_key=_get_env_optional_str("DEMOINDEX_LLM_API_KEY"),
        base_url=_get_env_optional_str("DEMOINDEX_LLM_BASE_URL") or _default_base_url(llm_provider),
        timeout_seconds=_get_env_float(
            "DEMOINDEX_LLM_TIMEOUT_SECONDS",
            DEFAULT_LLM_TIMEOUT_SECONDS,
        ),
        max_retries=_get_env_int("DEMOINDEX_LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES),
        retry_base_seconds=_get_env_float(
            "DEMOINDEX_LLM_RETRY_BASE_SECONDS",
            DEFAULT_LLM_RETRY_BASE_SECONDS,
        ),
        max_concurrency=_get_env_int(
            "DEMOINDEX_LLM_MAX_CONCURRENCY",
            DEFAULT_LLM_MAX_CONCURRENCY,
        ),
    )

    configured_dimensions = _get_env_optional_int("DEMOINDEX_EMBEDDING_DIMENSIONS")
    embedding_config = EmbeddingApiConfig(
        provider=embedding_provider,
        api_key=_get_env_optional_str("DEMOINDEX_EMBEDDING_API_KEY"),
        base_url=_get_env_optional_str("DEMOINDEX_EMBEDDING_BASE_URL")
        or _default_base_url(embedding_provider),
        timeout_seconds=_get_env_float(
            "DEMOINDEX_EMBEDDING_TIMEOUT_SECONDS",
            DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        ),
        max_retries=_get_env_int(
            "DEMOINDEX_EMBEDDING_MAX_RETRIES",
            DEFAULT_EMBEDDING_MAX_RETRIES,
        ),
        max_batch_size=_get_env_int(
            "DEMOINDEX_EMBEDDING_MAX_BATCH_SIZE",
            DEFAULT_EMBEDDING_MAX_BATCH_SIZE,
        ),
        dimensions=(
            configured_dimensions
            if configured_dimensions is not None
            else (
                DEFAULT_DASHSCOPE_EMBEDDING_DIMENSIONS
                if embedding_provider == "dashscope"
                else None
            )
        ),
    )

    retrieval_defaults = RetrievalDefaults(
        use_llm_parse=_get_env_bool(
            "DEMOINDEX_RETRIEVAL_USE_LLM_PARSE",
            DEFAULT_RETRIEVAL_USE_LLM_PARSE,
        ),
        parse_model=_get_env_optional_str("DEMOINDEX_RETRIEVAL_PARSE_MODEL")
        or DEFAULT_RETRIEVAL_PARSE_MODEL,
        parse_fallback_model=_get_env_optional_str("DEMOINDEX_RETRIEVAL_PARSE_FALLBACK_MODEL")
        or DEFAULT_RETRIEVAL_PARSE_FALLBACK_MODEL,
        embedding_model=_get_env_optional_str("DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL")
        or DEFAULT_RETRIEVAL_EMBEDDING_MODEL,
        top_k_dense=_get_env_int("DEMOINDEX_RETRIEVAL_TOP_K_DENSE", DEFAULT_RETRIEVAL_TOP_K_DENSE),
        top_k_lexical=_get_env_int(
            "DEMOINDEX_RETRIEVAL_TOP_K_LEXICAL",
            DEFAULT_RETRIEVAL_TOP_K_LEXICAL,
        ),
        top_k_fused_chunks=_get_env_int(
            "DEMOINDEX_RETRIEVAL_TOP_K_FUSED_CHUNKS",
            DEFAULT_RETRIEVAL_TOP_K_FUSED_CHUNKS,
        ),
        top_k_docs=_get_env_int("DEMOINDEX_RETRIEVAL_TOP_K_DOCS", DEFAULT_RETRIEVAL_TOP_K_DOCS),
        top_k_sections_per_doc=_get_env_int(
            "DEMOINDEX_RETRIEVAL_TOP_K_SECTIONS_PER_DOC",
            DEFAULT_RETRIEVAL_TOP_K_SECTIONS_PER_DOC,
        ),
        top_k_chunks_per_section=_get_env_int(
            "DEMOINDEX_RETRIEVAL_TOP_K_CHUNKS_PER_SECTION",
            DEFAULT_RETRIEVAL_TOP_K_CHUNKS_PER_SECTION,
        ),
        rrf_k=_get_env_int("DEMOINDEX_RETRIEVAL_RRF_K", DEFAULT_RETRIEVAL_RRF_K),
        lexical_score_threshold=_get_env_float(
            "DEMOINDEX_RETRIEVAL_LEXICAL_SCORE_THRESHOLD",
            DEFAULT_RETRIEVAL_LEXICAL_SCORE_THRESHOLD,
        ),
        doc_score_chunk_limit=_get_env_int(
            "DEMOINDEX_RETRIEVAL_DOC_SCORE_CHUNK_LIMIT",
            DEFAULT_RETRIEVAL_DOC_SCORE_CHUNK_LIMIT,
        ),
        section_score_chunk_limit=_get_env_int(
            "DEMOINDEX_RETRIEVAL_SECTION_SCORE_CHUNK_LIMIT",
            DEFAULT_RETRIEVAL_SECTION_SCORE_CHUNK_LIMIT,
        ),
        stage3_mode=_get_env_stage_mode("DEMOINDEX_STAGE3_MODE", DEFAULT_STAGE3_MODE),
        stage3_top_k_tree_sections_per_doc=_get_env_int(
            "DEMOINDEX_STAGE3_TOP_K_TREE_SECTIONS_PER_DOC",
            DEFAULT_STAGE3_TOP_K_TREE_SECTIONS_PER_DOC,
        ),
        stage3_top_k_anchor_sections_per_doc=_get_env_int(
            "DEMOINDEX_STAGE3_TOP_K_ANCHOR_SECTIONS_PER_DOC",
            DEFAULT_STAGE3_TOP_K_ANCHOR_SECTIONS_PER_DOC,
        ),
        stage3_whole_doc_fallback=_get_env_bool(
            "DEMOINDEX_STAGE3_WHOLE_DOC_FALLBACK",
            DEFAULT_STAGE3_WHOLE_DOC_FALLBACK,
        ),
        stage3_rerank_model=_get_env_optional_str("DEMOINDEX_STAGE3_RERANK_MODEL")
        or DEFAULT_STAGE3_RERANK_MODEL,
        stage3_rerank_fallback_model=_get_env_optional_str(
            "DEMOINDEX_STAGE3_RERANK_FALLBACK_MODEL"
        )
        or DEFAULT_STAGE3_RERANK_FALLBACK_MODEL,
        stage3_shortlist_size=_get_env_int(
            "DEMOINDEX_STAGE3_SHORTLIST_SIZE",
            DEFAULT_STAGE3_SHORTLIST_SIZE,
        ),
        stage3_relation_priors=_get_env_float_mapping(
            "DEMOINDEX_STAGE3_RELATION_PRIORS_JSON",
            DEFAULT_STAGE3_RELATION_PRIORS,
        ),
        stage4_top_k_focus_sections_per_doc=_get_env_int(
            "DEMOINDEX_STAGE4_TOP_K_FOCUS_SECTIONS_PER_DOC",
            DEFAULT_STAGE4_TOP_K_FOCUS_SECTIONS_PER_DOC,
        ),
        stage4_max_ancestor_hops=_get_env_int(
            "DEMOINDEX_STAGE4_MAX_ANCESTOR_HOPS",
            DEFAULT_STAGE4_MAX_ANCESTOR_HOPS,
        ),
        stage4_max_descendant_depth=_get_env_int(
            "DEMOINDEX_STAGE4_MAX_DESCENDANT_DEPTH",
            DEFAULT_STAGE4_MAX_DESCENDANT_DEPTH,
        ),
        stage4_max_siblings_per_focus=_get_env_int(
            "DEMOINDEX_STAGE4_MAX_SIBLINGS_PER_FOCUS",
            DEFAULT_STAGE4_MAX_SIBLINGS_PER_FOCUS,
        ),
        stage4_chunk_neighbor_window=_get_env_int(
            "DEMOINDEX_STAGE4_CHUNK_NEIGHBOR_WINDOW",
            DEFAULT_STAGE4_CHUNK_NEIGHBOR_WINDOW,
        ),
        stage4_max_evidence_chunks_per_focus=_get_env_int(
            "DEMOINDEX_STAGE4_MAX_EVIDENCE_CHUNKS_PER_FOCUS",
            DEFAULT_STAGE4_MAX_EVIDENCE_CHUNKS_PER_FOCUS,
        ),
        stage4_context_char_budget=_get_env_int(
            "DEMOINDEX_STAGE4_CONTEXT_CHAR_BUDGET",
            DEFAULT_STAGE4_CONTEXT_CHAR_BUDGET,
        ),
        stage5_relation_mode=_get_env_stage_mode(
            "DEMOINDEX_STAGE5_RELATION_MODE",
            DEFAULT_STAGE5_RELATION_MODE,
        ),
        stage5_top_k_evidence_per_doc=_get_env_int(
            "DEMOINDEX_STAGE5_TOP_K_EVIDENCE_PER_DOC",
            DEFAULT_STAGE5_TOP_K_EVIDENCE_PER_DOC,
        ),
        stage5_top_k_total_evidence=_get_env_int(
            "DEMOINDEX_STAGE5_TOP_K_TOTAL_EVIDENCE",
            DEFAULT_STAGE5_TOP_K_TOTAL_EVIDENCE,
        ),
        stage5_relation_model=_get_env_optional_str("DEMOINDEX_STAGE5_RELATION_MODEL")
        or DEFAULT_STAGE5_RELATION_MODEL,
        stage5_relation_fallback_model=_get_env_optional_str(
            "DEMOINDEX_STAGE5_RELATION_FALLBACK_MODEL"
        )
        or DEFAULT_STAGE5_RELATION_FALLBACK_MODEL,
        stage5_relation_shortlist_size=_get_env_int(
            "DEMOINDEX_STAGE5_RELATION_SHORTLIST_SIZE",
            DEFAULT_STAGE5_RELATION_SHORTLIST_SIZE,
        ),
    )

    return DemoIndexConfig(
        database_url=_get_env_optional_str("DEMOINDEX_DATABASE_URL"),
        debug_log=_get_env_bool("DEMOINDEX_DEBUG_LOG", False),
        debug_log_dir=_get_env_optional_str("DEMOINDEX_DEBUG_LOG_DIR"),
        retrieval_profile_path=_get_env_optional_str("DEMOINDEX_RETRIEVAL_PROFILE_PATH"),
        llm=llm_config,
        embedding=embedding_config,
        build=BuildDefaults(
            model=_get_env_optional_str("DEMOINDEX_BUILD_MODEL") or DEFAULT_BUILD_MODEL,
            fallback_model=_get_env_optional_str("DEMOINDEX_BUILD_FALLBACK_MODEL")
            or DEFAULT_BUILD_FALLBACK_MODEL,
            include_summary=_get_env_bool(
                "DEMOINDEX_BUILD_INCLUDE_SUMMARY",
                DEFAULT_BUILD_INCLUDE_SUMMARY,
            ),
            write_postgres=_get_env_bool(
                "DEMOINDEX_BUILD_WRITE_POSTGRES",
                DEFAULT_BUILD_WRITE_POSTGRES,
            ),
            write_global_index=_get_env_bool(
                "DEMOINDEX_BUILD_WRITE_GLOBAL_INDEX",
                DEFAULT_BUILD_WRITE_GLOBAL_INDEX,
            ),
            global_index_model=_get_env_optional_str("DEMOINDEX_BUILD_GLOBAL_INDEX_MODEL")
            or DEFAULT_BUILD_GLOBAL_INDEX_MODEL,
            markdown_layout=_get_env_optional_str("DEMOINDEX_BUILD_MARKDOWN_LAYOUT")
            or DEFAULT_BUILD_MARKDOWN_LAYOUT,
            pdf_strategy=_get_env_build_pdf_strategy(
                "DEMOINDEX_BUILD_PDF_STRATEGY",
                DEFAULT_BUILD_PDF_STRATEGY,
            ),
            artifacts_dir=_get_env_optional_str("DEMOINDEX_BUILD_ARTIFACTS_DIR"),
        ),
        retrieval=retrieval_defaults,
    )


def load_llm_api_key() -> str:
    """Load and return the configured DemoIndex chat LLM API key."""
    api_key = get_demoindex_config().llm.api_key
    if not api_key:
        raise RuntimeError(
            "Missing DemoIndex chat API key. Set DEMOINDEX_LLM_API_KEY in DemoIndex/.env "
            "or the current environment."
        )
    return api_key


def load_embedding_api_key() -> str:
    """Load and return the configured DemoIndex embedding API key."""
    api_key = get_demoindex_config().embedding.api_key
    if not api_key:
        raise RuntimeError(
            "Missing DemoIndex embedding API key. Set DEMOINDEX_EMBEDDING_API_KEY in DemoIndex/.env "
            "or the current environment."
        )
    return api_key


def load_dashscope_api_key() -> str:
    """Return the configured chat API key for backwards compatibility."""
    return load_llm_api_key()


def _default_base_url(provider: ApiProvider) -> str:
    """Return the default base URL for one supported provider."""
    if provider == "dashscope":
        return DEFAULT_DASHSCOPE_BASE_URL
    return DEFAULT_OPENAI_BASE_URL


def _get_env_optional_str(name: str) -> str | None:
    """Return one optional environment string after trimming whitespace."""
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _get_env_provider(name: str, default: ApiProvider) -> ApiProvider:
    """Parse one provider environment variable."""
    value = (_get_env_optional_str(name) or default).strip().lower()
    if value not in {"dashscope", "openai"}:
        raise ValueError(f"{name} must be one of: dashscope, openai.")
    return value  # type: ignore[return-value]


def _get_env_stage_mode(name: str, default: StageMode) -> StageMode:
    """Parse one stage-mode environment variable."""
    value = (_get_env_optional_str(name) or default).strip().lower()
    if value not in {"heuristic", "hybrid"}:
        raise ValueError(f"{name} must be one of: heuristic, hybrid.")
    return value  # type: ignore[return-value]


def _get_env_build_pdf_strategy(name: str, default: BuildPdfStrategy) -> BuildPdfStrategy:
    """Parse one PDF build strategy environment variable."""
    value = (_get_env_optional_str(name) or default).strip().lower()
    if value not in {"auto", "toc_seeded", "pageindex_native", "layout_fallback"}:
        raise ValueError(
            f"{name} must be one of: auto, toc_seeded, pageindex_native, layout_fallback."
        )
    return value  # type: ignore[return-value]


def _get_env_bool(name: str, default: bool) -> bool:
    """Parse one boolean environment variable."""
    value = _get_env_optional_str(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean string.")


def _get_env_int(name: str, default: int) -> int:
    """Parse one integer environment variable."""
    value = _get_env_optional_str(name)
    if value is None:
        return int(default)
    return int(value)


def _get_env_optional_int(name: str) -> int | None:
    """Parse one optional integer environment variable."""
    value = _get_env_optional_str(name)
    if value is None:
        return None
    return int(value)


def _get_env_float(name: str, default: float) -> float:
    """Parse one float environment variable."""
    value = _get_env_optional_str(name)
    if value is None:
        return float(default)
    return float(value)


def _get_env_float_mapping(name: str, default: dict[str, float]) -> dict[str, float]:
    """Parse one JSON object environment variable into a float mapping."""
    value = _get_env_optional_str(name)
    if value is None:
        return dict(default)
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return {str(key): float(item) for key, item in payload.items()}
