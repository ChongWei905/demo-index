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

    compare_parser = subparsers.add_parser("compare", help="Compare two tree JSON files.")
    compare_parser.add_argument("--actual-json", required=True, help="Generated tree JSON path.")
    compare_parser.add_argument("--expected-json", required=True, help="Expected tree JSON path.")
    compare_parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path for saving the comparison report as JSON.",
    )

    return parser.parse_args()


def main() -> int:
    """Run the DemoIndex CLI."""
    args = _parse_args()
    if args.command == "run":
        output_path = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else REPO_ROOT / "DemoIndex" / "artifacts" / Path(args.pdf_path).stem / f"{Path(args.pdf_path).stem}_pageindex_tree.json"
        )
        result = build_pageindex_tree(
            pdf_path=args.pdf_path,
            output_json=args.output_json,
            artifacts_dir=args.artifacts_dir,
            model=args.model,
            fallback_model=args.fallback_model,
        )
        if not result:
            return 1
        print(output_path)
        return 0

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


if __name__ == "__main__":
    raise SystemExit(main())
