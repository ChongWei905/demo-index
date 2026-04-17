"""CLI for DemoIndex."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


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
            or (len(sys.argv) > 1 and sys.argv[1] == "retrieve")
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
from .retrieval import retrieve_candidates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and compare DemoIndex trees.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Build a PageIndex-style tree from a PDF.")
    run_parser.add_argument("--pdf-path", required=True, help="Path to the PDF file.")
    run_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    run_parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Optional artifact directory. Defaults to DemoIndex/artifacts/<pdf_stem>.",
    )
    run_parser.add_argument(
        "--model",
        default="dashscope/qwen3.6-plus",
        help="Primary DashScope model for page transcription.",
    )
    run_parser.add_argument(
        "--fallback-model",
        default="dashscope/qwen3.5-plus",
        help="Fallback DashScope model when the primary model fails.",
    )
    run_parser.add_argument(
        "--include-summary",
        action="store_true",
        help="Generate PageIndex-style node summaries and include them in the output.",
    )
    run_parser.add_argument(
        "--write-postgres",
        action="store_true",
        help="Persist the final document tree into PostgreSQL using DATABASE_URL.",
    )
    run_parser.add_argument(
        "--write-global-index",
        action="store_true",
        help="Build and persist global chunk vectors into PostgreSQL using DATABASE_URL.",
    )
    run_parser.add_argument(
        "--global-index-model",
        default="text-embedding-v4",
        help="DashScope embedding model used for global chunk indexing.",
    )
    run_parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Write structured debug logs, API usage, and stage timings under the artifact directory.",
    )
    run_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for structured debug logs. Defaults to <artifacts-dir>/debug.",
    )

    compare_parser = subparsers.add_parser("compare", help="Compare two tree JSON files.")
    compare_parser.add_argument("--actual-json", required=True, help="Generated tree JSON path.")
    compare_parser.add_argument("--expected-json", required=True, help="Expected tree JSON path.")
    compare_parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path for saving the comparison report as JSON.",
    )

    retrieve_parser = subparsers.add_parser("retrieve", help="Run Stage 1 and Stage 2 retrieval.")
    retrieve_parser.add_argument("--query", required=True, help="Search query text.")
    retrieve_parser.add_argument("--output-json", default=None, help="Optional output JSON path.")
    retrieve_parser.add_argument("--top-k-dense", type=int, default=60, help="Dense ANN recall limit.")
    retrieve_parser.add_argument("--top-k-lexical", type=int, default=60, help="Lexical recall limit.")
    retrieve_parser.add_argument(
        "--top-k-fused-chunks",
        type=int,
        default=80,
        help="Final fused chunk candidate limit.",
    )
    retrieve_parser.add_argument("--top-k-docs", type=int, default=10, help="Document candidate limit.")
    retrieve_parser.add_argument(
        "--top-k-sections-per-doc",
        type=int,
        default=3,
        help="Section anchor limit per selected document.",
    )
    retrieve_parser.add_argument(
        "--top-k-chunks-per-section",
        type=int,
        default=2,
        help="Supporting chunk limit per selected section.",
    )
    retrieve_parser.add_argument(
        "--disable-llm-parse",
        action="store_true",
        help="Disable optional query-time LLM enrichment.",
    )
    retrieve_parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Write structured retrieval debug logs and timings.",
    )
    retrieve_parser.add_argument(
        "--debug-log-dir",
        default=None,
        help="Optional directory for retrieval debug logs.",
    )

    return parser.parse_args()


def main() -> int:
    """Run the DemoIndex CLI."""
    args = _parse_args()
    if args.command == "run":
        artifact_root = (
            Path(args.artifacts_dir).expanduser().resolve()
            if args.artifacts_dir
            else REPO_ROOT / "DemoIndex" / "artifacts" / Path(args.pdf_path).stem
        )
        output_path = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else artifact_root / f"{Path(args.pdf_path).stem}_pageindex_tree.json"
        )
        result = build_pageindex_tree(
            pdf_path=args.pdf_path,
            output_json=args.output_json,
            artifacts_dir=args.artifacts_dir,
            model=args.model,
            fallback_model=args.fallback_model,
            include_summary=args.include_summary,
            write_postgres=args.write_postgres,
            write_global_index=args.write_global_index,
            global_index_model=args.global_index_model,
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

    result = retrieve_candidates(
        query=args.query,
        top_k_dense=args.top_k_dense,
        top_k_lexical=args.top_k_lexical,
        top_k_fused_chunks=args.top_k_fused_chunks,
        top_k_docs=args.top_k_docs,
        top_k_sections_per_doc=args.top_k_sections_per_doc,
        top_k_chunks_per_section=args.top_k_chunks_per_section,
        use_llm_parse=not args.disable_llm_parse,
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
