# DemoIndex Retrieval Config

This document lists the current configurable options for Stage 1, Stage 2, and Stage 3 retrieval.

## Stage 1: Query Understanding

API options:

- `use_llm_parse`
- `parse_model`
- `parse_fallback_model`
- `retrieval_profile_path`

CLI options:

- `--disable-llm-parse`
- `--parse-model`
- `--parse-fallback-model`
- `--retrieval-profile-path`

Notes:

- `retrieval_profile_path` overrides `DEMOINDEX_RETRIEVAL_PROFILE_PATH`.
- Stage 1 still uses generic parsing first, then optional LLM enrichment.
- Current defaults:
  - `use_llm_parse=True`
  - `parse_model="dashscope/qwen3.6-plus"`
  - `parse_fallback_model="dashscope/qwen3.5-plus"`

## Stage 2: Global Candidate Recall

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

Current defaults:

- `top_k_dense=60`
- `top_k_lexical=60`
- `top_k_fused_chunks=80`
- `top_k_docs=10`
- `top_k_sections_per_doc=3`
- `top_k_chunks_per_section=2`
- `embedding_model="text-embedding-v4"`
- `rrf_k=60`
- `lexical_score_threshold=0.18`
- `doc_score_chunk_limit=5`
- `section_score_chunk_limit=3`

## Stage 3: Tree Localization

API options:

- `mode` on `localize_sections(...)`
- `stage3_mode` on `retrieve_tree_candidates(...)`
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

Current defaults:

- `mode="hybrid"`
- `top_k_tree_sections_per_doc=5`
- `top_k_anchor_sections_per_doc=3`
- `whole_doc_fallback=True`
- `rerank_model="dashscope/qwen3.6-plus"`
- `rerank_fallback_model="dashscope/qwen3.5-plus"`
- `stage3_shortlist_size=8`

`stage3_relation_priors` and `--stage3-relation-priors-json` use the same key set:

- `anchor`
- `descendant`
- `ancestor`
- `sibling`
- `doc_fallback`

Example:

```json
{
  "anchor": 4.0,
  "descendant": 2.75,
  "ancestor": 2.1,
  "sibling": 1.45,
  "doc_fallback": 0.55
}
```

## Debug and Logging

Both retrieval entrypoints support:

- `debug_log`
- `debug_log_dir`

CLI options:

- `--debug-log`
- `--debug-log-dir`

## Entry Points

Python:

- `retrieve_candidates(...)`
- `retrieve_tree_candidates(...)`
- `localize_sections(...)`
- `parse_query(...)`

CLI:

- `python -m DemoIndex.run retrieve ...`
- `python -m DemoIndex.run retrieve-tree ...`
