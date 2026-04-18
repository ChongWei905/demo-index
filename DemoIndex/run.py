"""CLI for DemoIndex."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DEFAULTS_NOTE = "Explicit CLI args override DemoIndex/.env, which overrides code defaults."


def _maybe_reexec_into_pageindex_venv() -> None:
    """Re-exec into PageIndex's virtualenv when required dependencies are missing."""
    if os.environ.get("DEMOINDEX_BOOTSTRAPPED") == "1":
        return
    try:
        import openai  # noqa: F401
        import pymupdf  # noqa: F401
        if (
            "--write-postgres" in sys.argv
            or "--write-global-index" in sys.argv
            or (len(sys.argv) > 1 and sys.argv[1] in {"retrieve", "retrieve-tree", "retrieve-evidence"})
        ):
            import psycopg  # noqa: F401
    except Exception:
        venv_python = REPO_ROOT / "PageIndex" / ".venv" / "bin" / "python"
        if not venv_python.exists():
            return
        env = os.environ.copy()
        env["DEMOINDEX_BOOTSTRAPPED"] = "1"
        cmd = [str(venv_python), "-m", "DemoIndex.run", *sys.argv[1:]]
        raise SystemExit(subprocess.call(cmd, cwd=str(REPO_ROOT), env=env))


_maybe_reexec_into_pageindex_venv()

from .pipeline import build_pageindex_tree, compare_tree
from .retrieval import retrieve_candidates, retrieve_evidence, retrieve_tree_candidates
from .env import get_demoindex_config


def _parse_json_object_arg(value: str | None, *, arg_name: str) -> dict[str, float] | None:
    """Parse one optional CLI JSON object argument into a float mapping."""
    if not value:
        return None
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError(f"{arg_name} must be a JSON object.")
    return {str(key): float(item) for key, item in payload.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Build and compare DemoIndex trees. {CLI_DEFAULTS_NOTE}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Build a PageIndex-style tree from a PDF or Markdown file.",
        description=f"Build one DemoIndex tree. {CLI_DEFAULTS_NOTE}",
    )
    run_parser.add_argument("--input-path", default=None, help="Path to the input PDF or Markdown file.")
    run_parser.add_argument("--pdf-path", default=None, help="Deprecated alias for PDF input path.")
    run_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    run_parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Optional artifact directory. Falls back to DEMOINDEX_BUILD_ARTIFACTS_DIR.",
    )
    run_parser.add_argument(
        "--model",
        default=None,
        help="Primary chat model for page transcription. Falls back to DEMOINDEX_BUILD_MODEL.",
    )
    run_parser.add_argument(
        "--fallback-model",
        default=None,
        help="Fallback chat model for page transcription. Falls back to DEMOINDEX_BUILD_FALLBACK_MODEL.",
    )
    run_parser.add_argument(
        "--include-summary",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Generate PageIndex-style node summaries. Falls back to DEMOINDEX_BUILD_INCLUDE_SUMMARY.",
    )
    run_parser.add_argument(
        "--write-postgres",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Persist the final document tree using DEMOINDEX_DATABASE_URL.",
    )
    run_parser.add_argument(
        "--write-global-index",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Build and persist global chunk vectors using DEMOINDEX_DATABASE_URL.",
    )
    run_parser.add_argument(
        "--global-index-model",
        default=None,
        help="Embedding model used for global chunk indexing. Falls back to DEMOINDEX_BUILD_GLOBAL_INDEX_MODEL.",
    )
    run_parser.add_argument(
        "--markdown-layout",
        choices=("auto", "h1_forest", "page_per_page"),
        default=None,
        help="Markdown layout mode. Falls back to DEMOINDEX_BUILD_MARKDOWN_LAYOUT.",
    )
    run_parser.add_argument(
        "--debug-log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write structured debug logs. Falls back to DEMOINDEX_DEBUG_LOG.",
    )
    run_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for structured debug logs. Falls back to DEMOINDEX_DEBUG_LOG_DIR.",
    )

    compare_parser = subparsers.add_parser("compare", help="Compare two tree JSON files.")
    compare_parser.add_argument("--actual-json", required=True, help="Generated tree JSON path.")
    compare_parser.add_argument("--expected-json", required=True, help="Expected tree JSON path.")
    compare_parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path for saving the comparison report as JSON.",
    )

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        help="Run Stage 1 and Stage 2 retrieval.",
        description=f"Run Stage 1 and Stage 2 retrieval. {CLI_DEFAULTS_NOTE}",
    )
    retrieve_parser.add_argument("--query", required=True, help="Search query text.")
    retrieve_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    retrieve_parser.add_argument("--top-k-dense", type=int, default=None, help="Dense ANN recall limit.")
    retrieve_parser.add_argument("--top-k-lexical", type=int, default=None, help="Lexical recall limit.")
    retrieve_parser.add_argument(
        "--top-k-fused-chunks",
        type=int,
        default=None,
        help="Final fused chunk candidate limit.",
    )
    retrieve_parser.add_argument("--top-k-docs", type=int, default=None, help="Document candidate limit.")
    retrieve_parser.add_argument(
        "--top-k-sections-per-doc",
        type=int,
        default=None,
        help="Section anchor limit per selected document.",
    )
    retrieve_parser.add_argument(
        "--top-k-chunks-per-section",
        type=int,
        default=None,
        help="Supporting chunk limit per selected section.",
    )
    retrieve_parser.add_argument(
        "--disable-llm-parse",
        action="store_true",
        help="Disable optional query-time LLM enrichment.",
    )
    retrieve_parser.add_argument(
        "--parse-model",
        default=None,
        help="Chat model used for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_MODEL.",
    )
    retrieve_parser.add_argument(
        "--parse-fallback-model",
        default=None,
        help="Fallback chat model for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_FALLBACK_MODEL.",
    )
    retrieve_parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model used for dense retrieval. Falls back to DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL.",
    )
    retrieve_parser.add_argument(
        "--rrf-k",
        type=int,
        default=None,
        help="Reciprocal rank fusion constant.",
    )
    retrieve_parser.add_argument(
        "--lexical-score-threshold",
        type=float,
        default=None,
        help="Minimum lexical similarity threshold for candidate generation.",
    )
    retrieve_parser.add_argument(
        "--doc-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each doc score.",
    )
    retrieve_parser.add_argument(
        "--section-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each section score.",
    )
    retrieve_parser.add_argument(
        "--retrieval-profile-path",
        default=None,
        help="Optional retrieval profile JSON path overriding DEMOINDEX_RETRIEVAL_PROFILE_PATH.",
    )
    retrieve_parser.add_argument(
        "--debug-log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write structured retrieval debug logs and timings. Falls back to DEMOINDEX_DEBUG_LOG.",
    )
    retrieve_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for retrieval debug logs.",
    )

    retrieve_tree_parser = subparsers.add_parser(
        "retrieve-tree",
        help="Run Stage 1 + Stage 2 + Stage 3 tree localization.",
        description=f"Run Stage 1 through Stage 3 retrieval. {CLI_DEFAULTS_NOTE}",
    )
    retrieve_tree_parser.add_argument("--query", required=True, help="Search query text.")
    retrieve_tree_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    retrieve_tree_parser.add_argument("--top-k-dense", type=int, default=None, help="Dense ANN recall limit.")
    retrieve_tree_parser.add_argument("--top-k-lexical", type=int, default=None, help="Lexical recall limit.")
    retrieve_tree_parser.add_argument(
        "--top-k-fused-chunks",
        type=int,
        default=None,
        help="Final fused chunk candidate limit.",
    )
    retrieve_tree_parser.add_argument("--top-k-docs", type=int, default=None, help="Document candidate limit.")
    retrieve_tree_parser.add_argument(
        "--top-k-sections-per-doc",
        type=int,
        default=None,
        help="Section anchor limit per selected document.",
    )
    retrieve_tree_parser.add_argument(
        "--top-k-chunks-per-section",
        type=int,
        default=None,
        help="Supporting chunk limit per selected section.",
    )
    retrieve_tree_parser.add_argument(
        "--stage3-mode",
        choices=("heuristic", "hybrid"),
        default=None,
        help="Stage 3 localization mode.",
    )
    retrieve_tree_parser.add_argument(
        "--top-k-tree-sections-per-doc",
        type=int,
        default=None,
        help="Final localized section limit per selected document.",
    )
    retrieve_tree_parser.add_argument(
        "--top-k-anchor-sections-per-doc",
        type=int,
        default=None,
        help="Anchor section limit per selected document.",
    )
    retrieve_tree_parser.add_argument(
        "--disable-whole-doc-fallback",
        action="store_true",
        help="Disable whole-document fallback when the anchor-local pool is too small.",
    )
    retrieve_tree_parser.add_argument(
        "--disable-llm-parse",
        action="store_true",
        help="Disable optional query-time LLM enrichment.",
    )
    retrieve_tree_parser.add_argument(
        "--parse-model",
        default=None,
        help="Chat model used for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_MODEL.",
    )
    retrieve_tree_parser.add_argument(
        "--parse-fallback-model",
        default=None,
        help="Fallback chat model for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_FALLBACK_MODEL.",
    )
    retrieve_tree_parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model used for dense retrieval. Falls back to DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL.",
    )
    retrieve_tree_parser.add_argument(
        "--rrf-k",
        type=int,
        default=None,
        help="Reciprocal rank fusion constant.",
    )
    retrieve_tree_parser.add_argument(
        "--lexical-score-threshold",
        type=float,
        default=None,
        help="Minimum lexical similarity threshold for candidate generation.",
    )
    retrieve_tree_parser.add_argument(
        "--doc-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each doc score.",
    )
    retrieve_tree_parser.add_argument(
        "--section-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each section score.",
    )
    retrieve_tree_parser.add_argument(
        "--retrieval-profile-path",
        default=None,
        help="Optional retrieval profile JSON path overriding DEMOINDEX_RETRIEVAL_PROFILE_PATH.",
    )
    retrieve_tree_parser.add_argument(
        "--debug-log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write structured retrieval debug logs and timings. Falls back to DEMOINDEX_DEBUG_LOG.",
    )
    retrieve_tree_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for retrieval debug logs.",
    )
    retrieve_tree_parser.add_argument(
        "--stage3-rerank-model",
        default=None,
        help="Chat model used for Stage 3 hybrid rerank. Falls back to DEMOINDEX_STAGE3_RERANK_MODEL.",
    )
    retrieve_tree_parser.add_argument(
        "--stage3-rerank-fallback-model",
        default=None,
        help="Fallback chat model for Stage 3 hybrid rerank. Falls back to DEMOINDEX_STAGE3_RERANK_FALLBACK_MODEL.",
    )
    retrieve_tree_parser.add_argument(
        "--stage3-shortlist-size",
        type=int,
        default=None,
        help="Shortlist size per document before Stage 3 hybrid rerank.",
    )
    retrieve_tree_parser.add_argument(
        "--stage3-relation-priors-json",
        default=None,
        help="Optional JSON object overriding Stage 3 relation priors.",
    )

    retrieve_evidence_parser = subparsers.add_parser(
        "retrieve-evidence",
        help="Run Stage 1 through Stage 5 retrieval and package final evidence.",
        description=f"Run Stage 1 through Stage 5 retrieval. {CLI_DEFAULTS_NOTE}",
    )
    retrieve_evidence_parser.add_argument("--query", required=True, help="Search query text.")
    retrieve_evidence_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    retrieve_evidence_parser.add_argument("--top-k-dense", type=int, default=None, help="Dense ANN recall limit.")
    retrieve_evidence_parser.add_argument("--top-k-lexical", type=int, default=None, help="Lexical recall limit.")
    retrieve_evidence_parser.add_argument(
        "--top-k-fused-chunks",
        type=int,
        default=None,
        help="Final fused chunk candidate limit.",
    )
    retrieve_evidence_parser.add_argument("--top-k-docs", type=int, default=None, help="Document candidate limit.")
    retrieve_evidence_parser.add_argument(
        "--top-k-sections-per-doc",
        type=int,
        default=None,
        help="Section anchor limit per selected document.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-chunks-per-section",
        type=int,
        default=None,
        help="Supporting chunk limit per selected section.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage3-mode",
        choices=("heuristic", "hybrid"),
        default=None,
        help="Stage 3 localization mode.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-tree-sections-per-doc",
        type=int,
        default=None,
        help="Final localized section limit per selected document.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-anchor-sections-per-doc",
        type=int,
        default=None,
        help="Anchor section limit per selected document.",
    )
    retrieve_evidence_parser.add_argument(
        "--disable-whole-doc-fallback",
        action="store_true",
        help="Disable whole-document fallback when the anchor-local pool is too small.",
    )
    retrieve_evidence_parser.add_argument(
        "--disable-llm-parse",
        action="store_true",
        help="Disable optional query-time LLM enrichment.",
    )
    retrieve_evidence_parser.add_argument(
        "--parse-model",
        default=None,
        help="Chat model used for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--parse-fallback-model",
        default=None,
        help="Fallback chat model for query-time LLM parsing. Falls back to DEMOINDEX_RETRIEVAL_PARSE_FALLBACK_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model used for dense retrieval. Falls back to DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--rrf-k",
        type=int,
        default=None,
        help="Reciprocal rank fusion constant.",
    )
    retrieve_evidence_parser.add_argument(
        "--lexical-score-threshold",
        type=float,
        default=None,
        help="Minimum lexical similarity threshold for candidate generation.",
    )
    retrieve_evidence_parser.add_argument(
        "--doc-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each doc score.",
    )
    retrieve_evidence_parser.add_argument(
        "--section-score-chunk-limit",
        type=int,
        default=None,
        help="How many top fused chunks contribute to each section score.",
    )
    retrieve_evidence_parser.add_argument(
        "--retrieval-profile-path",
        default=None,
        help="Optional retrieval profile JSON path overriding DEMOINDEX_RETRIEVAL_PROFILE_PATH.",
    )
    retrieve_evidence_parser.add_argument(
        "--debug-log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write structured retrieval debug logs and timings. Falls back to DEMOINDEX_DEBUG_LOG.",
    )
    retrieve_evidence_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for retrieval debug logs.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage3-rerank-model",
        default=None,
        help="Chat model used for Stage 3 hybrid rerank. Falls back to DEMOINDEX_STAGE3_RERANK_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage3-rerank-fallback-model",
        default=None,
        help="Fallback chat model for Stage 3 hybrid rerank. Falls back to DEMOINDEX_STAGE3_RERANK_FALLBACK_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage3-shortlist-size",
        type=int,
        default=None,
        help="Shortlist size per document before Stage 3 hybrid rerank.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage3-relation-priors-json",
        default=None,
        help="Optional JSON object overriding Stage 3 relation priors.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-focus-sections-per-doc",
        type=int,
        default=None,
        help="How many localized sections per doc enter Stage 4 expansion.",
    )
    retrieve_evidence_parser.add_argument(
        "--max-ancestor-hops",
        type=int,
        default=None,
        help="Maximum ancestor hops to include for each focus section.",
    )
    retrieve_evidence_parser.add_argument(
        "--max-descendant-depth",
        type=int,
        default=None,
        help="Maximum descendant depth to include for each focus section.",
    )
    retrieve_evidence_parser.add_argument(
        "--max-siblings-per-focus",
        type=int,
        default=None,
        help="Maximum sibling sections to include for each focus section.",
    )
    retrieve_evidence_parser.add_argument(
        "--chunk-neighbor-window",
        type=int,
        default=None,
        help="Neighbor window around supporting chunks inside the focus section.",
    )
    retrieve_evidence_parser.add_argument(
        "--max-evidence-chunks-per-focus",
        type=int,
        default=None,
        help="Maximum evidence chunks kept for each focus section.",
    )
    retrieve_evidence_parser.add_argument(
        "--context-char-budget",
        type=int,
        default=None,
        help="Character budget for each Stage 4 answer-ready context.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage5-relation-mode",
        choices=("heuristic", "hybrid"),
        default=None,
        help="Stage 5 relation-labeling mode.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-evidence-per-doc",
        type=int,
        default=None,
        help="Maximum evidence items kept per document in Stage 5.",
    )
    retrieve_evidence_parser.add_argument(
        "--top-k-total-evidence",
        type=int,
        default=None,
        help="Maximum evidence items kept overall in Stage 5.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage5-relation-model",
        default=None,
        help="Chat model used for Stage 5 hybrid relation labeling. Falls back to DEMOINDEX_STAGE5_RELATION_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage5-relation-fallback-model",
        default=None,
        help="Fallback chat model for Stage 5 hybrid relation labeling. Falls back to DEMOINDEX_STAGE5_RELATION_FALLBACK_MODEL.",
    )
    retrieve_evidence_parser.add_argument(
        "--stage5-relation-shortlist-size",
        type=int,
        default=None,
        help="How many evidence items enter the Stage 5 hybrid relation-labeling pass.",
    )

    return parser.parse_args()


def _resolve_run_input_path(args: argparse.Namespace) -> Path:
    """Resolve the effective build input path for the `run` subcommand."""
    if args.input_path and args.pdf_path:
        raise ValueError("Only one of --input-path or --pdf-path may be provided.")
    selected = args.input_path or args.pdf_path
    if not selected:
        raise ValueError("The run command requires --input-path or --pdf-path.")
    return Path(selected).expanduser().resolve()


def main() -> int:
    """Run the DemoIndex CLI."""
    args = _parse_args()
    config = get_demoindex_config()
    if args.command == "run":
        input_path = _resolve_run_input_path(args)
        resolved_artifacts_dir = args.artifacts_dir or config.build.artifacts_dir
        artifact_root = (
            Path(resolved_artifacts_dir).expanduser().resolve()
            if resolved_artifacts_dir
            else REPO_ROOT / "DemoIndex" / "artifacts" / input_path.stem
        )
        output_path = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else artifact_root / f"{input_path.stem}_pageindex_tree.json"
        )
        result = build_pageindex_tree(
            input_path=str(input_path),
            pdf_path=args.pdf_path,
            output_json=args.output_json,
            artifacts_dir=args.artifacts_dir,
            model=args.model,
            fallback_model=args.fallback_model,
            include_summary=args.include_summary,
            write_postgres=args.write_postgres,
            write_global_index=args.write_global_index,
            global_index_model=args.global_index_model,
            markdown_layout=args.markdown_layout,
            debug_log=args.debug_log,
            debug_log_dir=args.debug_log_dir,
        )
        if not result:
            return 1
        print(output_path)
        return 0

    if args.command == "compare":
        report = compare_tree(args.actual_json, args.expected_json)
        output_path = getattr(args, "output_json", None)
        if output_path:
            Path(output_path).expanduser().resolve().write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(output_path)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "retrieve":
        result = retrieve_candidates(
            query=args.query,
            top_k_dense=args.top_k_dense,
            top_k_lexical=args.top_k_lexical,
            top_k_fused_chunks=args.top_k_fused_chunks,
            top_k_docs=args.top_k_docs,
            top_k_sections_per_doc=args.top_k_sections_per_doc,
            top_k_chunks_per_section=args.top_k_chunks_per_section,
            use_llm_parse=None if args.disable_llm_parse is None else False,
            parse_model=args.parse_model,
            parse_fallback_model=args.parse_fallback_model,
            embedding_model=args.embedding_model,
            rrf_k=args.rrf_k,
            lexical_score_threshold=args.lexical_score_threshold,
            doc_score_chunk_limit=args.doc_score_chunk_limit,
            section_score_chunk_limit=args.section_score_chunk_limit,
            retrieval_profile_path=args.retrieval_profile_path,
            debug_log=args.debug_log,
            debug_log_dir=args.debug_log_dir,
        )
    elif args.command == "retrieve-tree":
        result = retrieve_tree_candidates(
            query=args.query,
            top_k_dense=args.top_k_dense,
            top_k_lexical=args.top_k_lexical,
            top_k_fused_chunks=args.top_k_fused_chunks,
            top_k_docs=args.top_k_docs,
            top_k_sections_per_doc=args.top_k_sections_per_doc,
            top_k_chunks_per_section=args.top_k_chunks_per_section,
            use_llm_parse=None if args.disable_llm_parse is None else False,
            parse_model=args.parse_model,
            parse_fallback_model=args.parse_fallback_model,
            embedding_model=args.embedding_model,
            rrf_k=args.rrf_k,
            lexical_score_threshold=args.lexical_score_threshold,
            doc_score_chunk_limit=args.doc_score_chunk_limit,
            section_score_chunk_limit=args.section_score_chunk_limit,
            retrieval_profile_path=args.retrieval_profile_path,
            stage3_mode=args.stage3_mode,
            top_k_tree_sections_per_doc=args.top_k_tree_sections_per_doc,
            top_k_anchor_sections_per_doc=args.top_k_anchor_sections_per_doc,
            whole_doc_fallback=None if args.disable_whole_doc_fallback is None else False,
            rerank_model=args.stage3_rerank_model,
            rerank_fallback_model=args.stage3_rerank_fallback_model,
            stage3_shortlist_size=args.stage3_shortlist_size,
            stage3_relation_priors=_parse_json_object_arg(
                args.stage3_relation_priors_json,
                arg_name="--stage3-relation-priors-json",
            ),
            debug_log=args.debug_log,
            debug_log_dir=args.debug_log_dir,
        )
    else:
        result = retrieve_evidence(
            query=args.query,
            top_k_dense=args.top_k_dense,
            top_k_lexical=args.top_k_lexical,
            top_k_fused_chunks=args.top_k_fused_chunks,
            top_k_docs=args.top_k_docs,
            top_k_sections_per_doc=args.top_k_sections_per_doc,
            top_k_chunks_per_section=args.top_k_chunks_per_section,
            use_llm_parse=None if args.disable_llm_parse is None else False,
            parse_model=args.parse_model,
            parse_fallback_model=args.parse_fallback_model,
            embedding_model=args.embedding_model,
            rrf_k=args.rrf_k,
            lexical_score_threshold=args.lexical_score_threshold,
            doc_score_chunk_limit=args.doc_score_chunk_limit,
            section_score_chunk_limit=args.section_score_chunk_limit,
            retrieval_profile_path=args.retrieval_profile_path,
            stage3_mode=args.stage3_mode,
            top_k_tree_sections_per_doc=args.top_k_tree_sections_per_doc,
            top_k_anchor_sections_per_doc=args.top_k_anchor_sections_per_doc,
            whole_doc_fallback=None if args.disable_whole_doc_fallback is None else False,
            rerank_model=args.stage3_rerank_model,
            rerank_fallback_model=args.stage3_rerank_fallback_model,
            stage3_shortlist_size=args.stage3_shortlist_size,
            stage3_relation_priors=_parse_json_object_arg(
                args.stage3_relation_priors_json,
                arg_name="--stage3-relation-priors-json",
            ),
            top_k_focus_sections_per_doc=args.top_k_focus_sections_per_doc,
            max_ancestor_hops=args.max_ancestor_hops,
            max_descendant_depth=args.max_descendant_depth,
            max_siblings_per_focus=args.max_siblings_per_focus,
            chunk_neighbor_window=args.chunk_neighbor_window,
            max_evidence_chunks_per_focus=args.max_evidence_chunks_per_focus,
            context_char_budget=args.context_char_budget,
            stage5_relation_mode=args.stage5_relation_mode,
            top_k_evidence_per_doc=args.top_k_evidence_per_doc,
            top_k_total_evidence=args.top_k_total_evidence,
            stage5_relation_model=args.stage5_relation_model,
            stage5_relation_fallback_model=args.stage5_relation_fallback_model,
            stage5_relation_shortlist_size=args.stage5_relation_shortlist_size,
            debug_log=args.debug_log,
            debug_log_dir=args.debug_log_dir,
        )
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    output_path = getattr(args, "output_json", None)
    if output_path:
        Path(output_path).expanduser().resolve().write_text(payload, encoding="utf-8")
        print(output_path)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
