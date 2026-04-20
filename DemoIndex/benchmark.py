"""Benchmark helpers for comparing DemoIndex PDF build strategies."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .env import REPO_ROOT
from .pipeline import build_pageindex_tree, compare_tree

DEFAULT_BENCHMARK_STRATEGIES = (
    "auto",
    "toc_seeded",
    "pageindex_native",
    "layout_fallback",
)
DEFAULT_BENCHMARK_PDFS = (
    REPO_ROOT / "DemoIndex" / "batch_10pdf_20260418" / "2024 TapTap移动游戏行业白皮书-50页.pdf",
    REPO_ROOT / "DemoIndex" / "batch_10pdf_20260418" / "2024中国移动游戏广告营销报告-39页.pdf",
)
DEFAULT_OFFICIAL_TREE_PATHS = {
    "2024 TapTap移动游戏行业白皮书-50页.pdf": REPO_ROOT
    / "playground"
    / "2024_taptap_game_whitepaper_pageindex_tree_sdk_20260419.json",
    "2024中国移动游戏广告营销报告-39页.pdf": REPO_ROOT
    / "playground"
    / "2024_mobile_game_ad_marketing_report_pageindex_tree_sdk_20260419.json",
}


def benchmark_pdf_strategies(
    *,
    pdf_paths: list[str] | None = None,
    official_tree_paths: dict[str, str] | None = None,
    output_dir: str,
    strategies: list[str] | None = None,
    model: str | None = None,
    fallback_model: str | None = None,
    include_summary: bool = False,
) -> dict[str, Any]:
    """Run multiple DemoIndex PDF build strategies and compare them with official trees."""
    resolved_output_dir = Path(output_dir).expanduser().resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_pdf_paths = [
        Path(path).expanduser().resolve()
        for path in (pdf_paths or [str(path) for path in DEFAULT_BENCHMARK_PDFS])
    ]
    resolved_official_paths = {
        name: Path(path).expanduser().resolve()
        for name, path in (official_tree_paths or _default_official_tree_paths()).items()
    }
    resolved_strategies = list(strategies or DEFAULT_BENCHMARK_STRATEGIES)

    benchmark_rows: list[dict[str, Any]] = []
    for pdf_path in resolved_pdf_paths:
        strategy_rows: list[dict[str, Any]] = []
        official_tree_path = resolved_official_paths.get(pdf_path.name)
        for strategy in resolved_strategies:
            strategy_dir = resolved_output_dir / pdf_path.stem / strategy
            tree_output_path = strategy_dir / "tree.json"
            debug_dir = strategy_dir / "debug"
            artifacts_dir = strategy_dir / "artifacts"
            try:
                with _temporary_env("DEMOINDEX_BUILD_PDF_STRATEGY", strategy):
                    payload = build_pageindex_tree(
                        input_path=str(pdf_path),
                        output_json=str(tree_output_path),
                        artifacts_dir=str(artifacts_dir),
                        model=model,
                        fallback_model=fallback_model,
                        include_summary=include_summary,
                        debug_log=True,
                        debug_log_dir=str(debug_dir),
                    )
            except Exception as exc:  # noqa: PERF203
                strategy_rows.append(
                    {
                        "strategy": strategy,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "tree_output_path": str(tree_output_path),
                        "debug_summary_path": str(debug_dir / "run_summary.json"),
                    }
                )
                continue

            run_summary = _read_json(debug_dir / "run_summary.json")
            candidate_selection = _read_json(artifacts_dir / "candidate_selection.json")
            comparison = (
                compare_tree(str(tree_output_path), str(official_tree_path))
                if official_tree_path and official_tree_path.exists()
                else None
            )
            strategy_rows.append(
                {
                    "strategy": strategy,
                    "status": "success",
                    "tree_output_path": str(tree_output_path),
                    "debug_summary_path": str(debug_dir / "run_summary.json"),
                    "candidate_selection_path": (
                        str(artifacts_dir / "candidate_selection.json")
                        if (artifacts_dir / "candidate_selection.json").exists()
                        else None
                    ),
                    "selected_strategy": (
                        candidate_selection.get("selected_strategy")
                        if isinstance(candidate_selection, dict)
                        else None
                    ),
                    "tree_metrics": _tree_metrics(payload.get("result") or []),
                    "run_metrics": _run_metrics(run_summary),
                    "official_comparison": comparison,
                }
            )

        benchmark_rows.append(
            {
                "pdf_path": str(pdf_path),
                "official_tree_path": str(official_tree_path) if official_tree_path else None,
                "strategies": strategy_rows,
            }
        )
    return {
        "output_dir": str(resolved_output_dir),
        "benchmarks": benchmark_rows,
    }


@contextmanager
def _temporary_env(name: str, value: str) -> Iterator[None]:
    """Temporarily set one environment variable for the current Python process."""
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _default_official_tree_paths() -> dict[str, str]:
    """Return the default benchmark official-tree mapping."""
    return {name: str(path) for name, path in DEFAULT_OFFICIAL_TREE_PATHS.items()}


def _tree_metrics(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute lightweight tree metrics for benchmark summaries."""
    flat_rows = _flatten_tree(nodes)
    depth_histogram: dict[str, int] = {}
    for row in flat_rows:
        key = str(row["depth"])
        depth_histogram[key] = depth_histogram.get(key, 0) + 1
    return {
        "root_count": len(nodes),
        "node_count": len(flat_rows),
        "max_depth": max((row["depth"] for row in flat_rows), default=0),
        "depth_histogram": dict(sorted(depth_histogram.items())),
    }


def _flatten_tree(nodes: list[dict[str, Any]], depth: int = 1) -> list[dict[str, Any]]:
    """Flatten one result tree while preserving per-node depth."""
    rows: list[dict[str, Any]] = []
    for node in nodes:
        rows.append(
            {
                "title": node.get("title"),
                "page_index": node.get("page_index"),
                "depth": depth,
            }
        )
        rows.extend(_flatten_tree(node.get("nodes") or [], depth + 1))
    return rows


def _run_metrics(run_summary: dict[str, Any] | None) -> dict[str, Any] | None:
    """Summarize timings and token usage from one structured debug run summary."""
    if not isinstance(run_summary, dict):
        return None
    stage_records = run_summary.get("stage_records") or []
    total_duration_ms = sum(int(stage.get("duration_ms") or 0) for stage in stage_records)
    return {
        "total_duration_ms": total_duration_ms,
        "llm_usage_totals": run_summary.get("llm_usage_totals") or {},
        "llm_call_counts": run_summary.get("llm_call_counts") or {},
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read one JSON file when it exists and parses cleanly."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
