# DemoIndex Retrieval Config

This document is the consolidated retrieval configuration reference for DemoIndex.
It covers Stage 1 through Stage 5, plus the shared API/env settings they depend on.

## Config Layers

DemoIndex retrieval now resolves settings in this order:

1. Explicit Python arguments
2. Explicit CLI arguments
3. `DemoIndex/.env`
4. Code defaults

Notes:

- The main runtime config file is `DemoIndex/.env`
- The only template source is `DemoIndex/.env.example`
- `PageIndex/.env` is no longer the main config source for DemoIndex
- Old names such as `DASHSCOPE_API_KEY`, `OPENAI_API_KEY`, `PAGEINDEX_*`, and `DATABASE_URL` are not DemoIndex fallbacks anymore

## Shared API Settings

Chat and embedding are configured independently and can point at different providers, keys, and base URLs.

Chat env vars:

- `DEMOINDEX_LLM_API_PROVIDER`
- `DEMOINDEX_LLM_API_KEY`
- `DEMOINDEX_LLM_BASE_URL`
- `DEMOINDEX_LLM_TIMEOUT_SECONDS`
- `DEMOINDEX_LLM_MAX_RETRIES`
- `DEMOINDEX_LLM_RETRY_BASE_SECONDS`
- `DEMOINDEX_LLM_MAX_CONCURRENCY`

Embedding env vars:

- `DEMOINDEX_EMBEDDING_API_PROVIDER`
- `DEMOINDEX_EMBEDDING_API_KEY`
- `DEMOINDEX_EMBEDDING_BASE_URL`
- `DEMOINDEX_EMBEDDING_TIMEOUT_SECONDS`
- `DEMOINDEX_EMBEDDING_MAX_RETRIES`
- `DEMOINDEX_EMBEDDING_MAX_BATCH_SIZE`
- `DEMOINDEX_EMBEDDING_DIMENSIONS`

Provider behavior:

- `dashscope`
  - Default base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
  - Embedding requests keep `text_type=document|query`
  - If `DEMOINDEX_EMBEDDING_DIMENSIONS` is unset, DemoIndex defaults to `1024`
- `openai`
  - Default base URL: `https://api.openai.com/v1`
  - Embedding requests do not send DashScope-only `text_type`
  - `dimensions` is sent only when `DEMOINDEX_EMBEDDING_DIMENSIONS` is explicitly set

## Shared Retrieval Env

- `DEMOINDEX_DATABASE_URL`
- `DEMOINDEX_DEBUG_LOG`
- `DEMOINDEX_DEBUG_LOG_DIR`
- `DEMOINDEX_RETRIEVAL_PROFILE_PATH`

`DEMOINDEX_RETRIEVAL_PROFILE_PATH` is used only when the caller does not pass `retrieval_profile_path`.

## Entry Points

Python:

- `parse_query(...)`
- `retrieve_candidates(...)`
- `localize_sections(...)`
- `retrieve_tree_candidates(...)`
- `expand_localized_sections(...)`
- `package_evidence(...)`
- `retrieve_evidence(...)`

CLI:

- `python -m DemoIndex.run retrieve ...`
- `python -m DemoIndex.run retrieve-tree ...`
- `python -m DemoIndex.run retrieve-evidence ...`

## Stage 1: Query Understanding

API options:

- `use_llm` on `parse_query(...)`
- `use_llm_parse` on retrieval entrypoints
- `parse_model`
- `parse_fallback_model`
- `retrieval_profile_path`

CLI options:

- `--disable-llm-parse`
- `--parse-model`
- `--parse-fallback-model`
- `--retrieval-profile-path`

Env vars:

- `DEMOINDEX_RETRIEVAL_USE_LLM_PARSE`
- `DEMOINDEX_RETRIEVAL_PARSE_MODEL`
- `DEMOINDEX_RETRIEVAL_PARSE_FALLBACK_MODEL`
- `DEMOINDEX_RETRIEVAL_PROFILE_PATH`

Code defaults:

- `use_llm_parse=True`
- `parse_model="dashscope/qwen3.6-plus"`
- `parse_fallback_model="dashscope/qwen3.5-plus"`

Notes:

- Stage 1 remains rule-first and uses LLM enrichment only when needed
- `retrieval_profile_path` / `DEMOINDEX_RETRIEVAL_PROFILE_PATH` stays optional

## Stage 2: Candidate Recall

API options:

- `top_k_dense`
- `top_k_lexical`
- `top_k_fused_chunks`
- `top_k_docs`
- `top_k_sections_per_doc`
- `top_k_chunks_per_section`
- `embedding_model`
- `rrf_k`
- `lexical_score_threshold`
- `doc_score_chunk_limit`
- `section_score_chunk_limit`

CLI options:

- `--top-k-dense`
- `--top-k-lexical`
- `--top-k-fused-chunks`
- `--top-k-docs`
- `--top-k-sections-per-doc`
- `--top-k-chunks-per-section`
- `--embedding-model`
- `--rrf-k`
- `--lexical-score-threshold`
- `--doc-score-chunk-limit`
- `--section-score-chunk-limit`

Env vars:

- `DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL`
- `DEMOINDEX_RETRIEVAL_TOP_K_DENSE`
- `DEMOINDEX_RETRIEVAL_TOP_K_LEXICAL`
- `DEMOINDEX_RETRIEVAL_TOP_K_FUSED_CHUNKS`
- `DEMOINDEX_RETRIEVAL_TOP_K_DOCS`
- `DEMOINDEX_RETRIEVAL_TOP_K_SECTIONS_PER_DOC`
- `DEMOINDEX_RETRIEVAL_TOP_K_CHUNKS_PER_SECTION`
- `DEMOINDEX_RETRIEVAL_RRF_K`
- `DEMOINDEX_RETRIEVAL_LEXICAL_SCORE_THRESHOLD`
- `DEMOINDEX_RETRIEVAL_DOC_SCORE_CHUNK_LIMIT`
- `DEMOINDEX_RETRIEVAL_SECTION_SCORE_CHUNK_LIMIT`

Code defaults:

- `embedding_model="text-embedding-v4"`
- `top_k_dense=60`
- `top_k_lexical=60`
- `top_k_fused_chunks=80`
- `top_k_docs=10`
- `top_k_sections_per_doc=3`
- `top_k_chunks_per_section=2`
- `rrf_k=60`
- `lexical_score_threshold=0.18`
- `doc_score_chunk_limit=5`
- `section_score_chunk_limit=3`

## Stage 3: Tree Localization

API options:

- `mode` on `localize_sections(...)`
- `stage3_mode` on retrieval entrypoints
- `top_k_tree_sections_per_doc`
- `top_k_anchor_sections_per_doc`
- `whole_doc_fallback`
- `rerank_model`
- `rerank_fallback_model`
- `stage3_shortlist_size`
- `stage3_relation_priors`

CLI options:

- `--stage3-mode`
- `--top-k-tree-sections-per-doc`
- `--top-k-anchor-sections-per-doc`
- `--disable-whole-doc-fallback`
- `--stage3-rerank-model`
- `--stage3-rerank-fallback-model`
- `--stage3-shortlist-size`
- `--stage3-relation-priors-json`

Env vars:

- `DEMOINDEX_STAGE3_MODE`
- `DEMOINDEX_STAGE3_TOP_K_TREE_SECTIONS_PER_DOC`
- `DEMOINDEX_STAGE3_TOP_K_ANCHOR_SECTIONS_PER_DOC`
- `DEMOINDEX_STAGE3_WHOLE_DOC_FALLBACK`
- `DEMOINDEX_STAGE3_RERANK_MODEL`
- `DEMOINDEX_STAGE3_RERANK_FALLBACK_MODEL`
- `DEMOINDEX_STAGE3_SHORTLIST_SIZE`
- `DEMOINDEX_STAGE3_RELATION_PRIORS_JSON`

Code defaults:

- `stage3_mode="hybrid"`
- `top_k_tree_sections_per_doc=5`
- `top_k_anchor_sections_per_doc=3`
- `whole_doc_fallback=True`
- `rerank_model="dashscope/qwen3.6-plus"`
- `rerank_fallback_model="dashscope/qwen3.5-plus"`
- `stage3_shortlist_size=8`

`DEMOINDEX_STAGE3_RELATION_PRIORS_JSON` uses this key set:

- `anchor`
- `descendant`
- `ancestor`
- `sibling`
- `doc_fallback`

Default JSON payload:

```json
{
  "anchor": 4.0,
  "descendant": 2.75,
  "ancestor": 2.1,
  "sibling": 1.45,
  "doc_fallback": 0.55
}
```

## Stage 4: Context Expansion

API options:

- `top_k_focus_sections_per_doc`
- `max_ancestor_hops`
- `max_descendant_depth`
- `max_siblings_per_focus`
- `chunk_neighbor_window`
- `max_evidence_chunks_per_focus`
- `context_char_budget`

CLI options on `retrieve-evidence`:

- `--top-k-focus-sections-per-doc`
- `--max-ancestor-hops`
- `--max-descendant-depth`
- `--max-siblings-per-focus`
- `--chunk-neighbor-window`
- `--max-evidence-chunks-per-focus`
- `--context-char-budget`

Env vars:

- `DEMOINDEX_STAGE4_TOP_K_FOCUS_SECTIONS_PER_DOC`
- `DEMOINDEX_STAGE4_MAX_ANCESTOR_HOPS`
- `DEMOINDEX_STAGE4_MAX_DESCENDANT_DEPTH`
- `DEMOINDEX_STAGE4_MAX_SIBLINGS_PER_FOCUS`
- `DEMOINDEX_STAGE4_CHUNK_NEIGHBOR_WINDOW`
- `DEMOINDEX_STAGE4_MAX_EVIDENCE_CHUNKS_PER_FOCUS`
- `DEMOINDEX_STAGE4_CONTEXT_CHAR_BUDGET`

Code defaults:

- `top_k_focus_sections_per_doc=3`
- `max_ancestor_hops=2`
- `max_descendant_depth=1`
- `max_siblings_per_focus=2`
- `chunk_neighbor_window=1`
- `max_evidence_chunks_per_focus=6`
- `context_char_budget=6000`

Notes:

- Stage 4 is deterministic
- It does not call a new LLM

## Stage 5: Evidence Packaging

API options:

- `relation_mode` on `package_evidence(...)`
- `stage5_relation_mode` on `retrieve_evidence(...)`
- `top_k_evidence_per_doc`
- `top_k_total_evidence`
- `relation_model`
- `relation_fallback_model`
- `relation_shortlist_size`

CLI options on `retrieve-evidence`:

- `--stage5-relation-mode`
- `--top-k-evidence-per-doc`
- `--top-k-total-evidence`
- `--stage5-relation-model`
- `--stage5-relation-fallback-model`
- `--stage5-relation-shortlist-size`

Env vars:

- `DEMOINDEX_STAGE5_RELATION_MODE`
- `DEMOINDEX_STAGE5_TOP_K_EVIDENCE_PER_DOC`
- `DEMOINDEX_STAGE5_TOP_K_TOTAL_EVIDENCE`
- `DEMOINDEX_STAGE5_RELATION_MODEL`
- `DEMOINDEX_STAGE5_RELATION_FALLBACK_MODEL`
- `DEMOINDEX_STAGE5_RELATION_SHORTLIST_SIZE`

Code defaults:

- `stage5_relation_mode="heuristic"`
- `top_k_evidence_per_doc=3`
- `top_k_total_evidence=8`
- `relation_model="dashscope/qwen3.6-plus"`
- `relation_fallback_model="dashscope/qwen3.5-plus"`
- `relation_shortlist_size=8`

Notes:

- `heuristic` packages evidence only
- `hybrid` adds one global relation-labeling LLM pass on top of the heuristic shortlist

## Build Defaults Used by Retrieval-Adjacent Flows

These settings do not directly control retrieval, but they affect how retrieval-ready data is built:

- `DEMOINDEX_BUILD_MODEL`
- `DEMOINDEX_BUILD_FALLBACK_MODEL`
- `DEMOINDEX_BUILD_INCLUDE_SUMMARY`
- `DEMOINDEX_BUILD_WRITE_POSTGRES`
- `DEMOINDEX_BUILD_WRITE_GLOBAL_INDEX`
- `DEMOINDEX_BUILD_GLOBAL_INDEX_MODEL`
- `DEMOINDEX_BUILD_MARKDOWN_LAYOUT`
- `DEMOINDEX_BUILD_ARTIFACTS_DIR`

If the caller omits these build arguments, DemoIndex resolves them from `DemoIndex/.env`.
