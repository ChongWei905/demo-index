"""PageIndex-aligned pipeline for DemoIndex."""

from __future__ import annotations

import asyncio
import copy
import json
import hashlib
import re
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .build_md_pageindex import PageIndexOptions, sync_build_pageindex_payload
from .debug import DebugRecorder
from .env import (
    REPO_ROOT,
    ensure_pageindex_import_path,
    get_demoindex_config,
)
from .global_index import (
    build_global_chunk_records,
)
from .llm import DashScopeEmbeddingClient, QwenChatClient
from .pdf import extract_outline_entries, extract_page_artifacts, layout_heading_candidates, normalize_text
from .pdf import assess_outline_confidence
from .postgres_store import persist_document_sections, persist_section_chunks, resolve_database_url


def build_pageindex_tree(
    input_path: str | None = None,
    pdf_path: str | None = None,
    output_json: str | None = None,
    artifacts_dir: str | None = None,
    model: str | None = None,
    fallback_model: str | None = None,
    include_summary: bool | None = None,
    write_postgres: bool | None = None,
    write_global_index: bool | None = None,
    global_index_model: str | None = None,
    markdown_layout: str | None = None,
    debug_log: bool | None = None,
    debug_log_dir: str | None = None,
) -> dict[str, Any]:
    """Build a target-format tree from one PDF or Markdown input."""
    ensure_pageindex_import_path()
    config = get_demoindex_config()
    resolved_model = model or config.build.model
    resolved_fallback_model = fallback_model or config.build.fallback_model
    resolved_write_global_index = (
        config.build.write_global_index if write_global_index is None else write_global_index
    )
    resolved_write_postgres = (
        config.build.write_postgres if write_postgres is None else write_postgres
    )
    effective_write_postgres = resolved_write_postgres or resolved_write_global_index
    resolved_include_summary = (
        config.build.include_summary if include_summary is None else include_summary
    )
    effective_include_summary = resolved_include_summary or effective_write_postgres
    resolved_global_index_model = global_index_model or config.build.global_index_model
    resolved_markdown_layout = markdown_layout or config.build.markdown_layout
    resolved_pdf_strategy = config.build.pdf_strategy
    resolved_debug_log = config.debug_log if debug_log is None else debug_log
    resolved_debug_log_dir = debug_log_dir or config.debug_log_dir
    resolved_artifacts_dir = artifacts_dir or config.build.artifacts_dir
    if effective_write_postgres:
        resolve_database_url()

    resolved_input_path = _resolve_input_path(input_path=input_path, pdf_path=pdf_path)
    input_kind = _detect_input_kind(resolved_input_path)

    artifact_root = (
        Path(resolved_artifacts_dir).expanduser().resolve()
        if resolved_artifacts_dir
        else REPO_ROOT / "DemoIndex" / "artifacts" / resolved_input_path.stem
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    debug_recorder = (
        DebugRecorder(
            Path(resolved_debug_log_dir).expanduser().resolve()
            if resolved_debug_log_dir
            else artifact_root / "debug"
        )
        if resolved_debug_log
        else None
    )
    if debug_recorder is not None:
        debug_recorder.set_run_metadata(
            input_path=str(resolved_input_path),
            input_kind=input_kind,
            artifact_root=str(artifact_root),
            output_json=str(Path(output_json).expanduser().resolve()) if output_json else None,
            model=resolved_model,
            fallback_model=resolved_fallback_model,
            include_summary=effective_include_summary,
            write_postgres=effective_write_postgres,
            write_global_index=resolved_write_global_index,
            global_index_model=resolved_global_index_model,
            markdown_layout=resolved_markdown_layout,
            pdf_strategy=resolved_pdf_strategy,
        )

    target_output_path: Path | None = None

    try:
        if input_kind == "pdf":
            output = _build_pdf_output(
                resolved_input_path=resolved_input_path,
                artifact_root=artifact_root,
                model=resolved_model,
                fallback_model=resolved_fallback_model,
                include_summary=effective_include_summary,
                pdf_strategy=resolved_pdf_strategy,
                debug_recorder=debug_recorder,
            )
        else:
            output = _build_markdown_output(
                resolved_input_path=resolved_input_path,
                artifact_root=artifact_root,
                model=resolved_model,
                fallback_model=resolved_fallback_model,
                include_summary=effective_include_summary,
                markdown_layout=resolved_markdown_layout,
                debug_recorder=debug_recorder,
            )
        if effective_write_postgres:
            with _debug_stage(debug_recorder, "persist_document_sections"):
                persistence_report = persist_document_sections(output)
            _save_json(artifact_root / "postgres_write.json", persistence_report)
        if resolved_write_global_index:
            utils_module = _load_pageindex_utils()
            with _debug_stage(debug_recorder, "build_global_chunk_records"):
                embedding_client = DashScopeEmbeddingClient(
                    model_name=resolved_global_index_model,
                    debug_recorder=debug_recorder,
                )
                chunk_records, chunk_report = build_global_chunk_records(
                    output,
                    count_tokens=utils_module.count_tokens,
                    embedding_client=embedding_client,
                    embedding_model=resolved_global_index_model,
                )
            with _debug_stage(debug_recorder, "persist_section_chunks"):
                chunk_persistence_report = persist_section_chunks(
                    chunk_records,
                    doc_id=str(output.get("doc_id") or ""),
                )
            _save_json(
                artifact_root / "global_index_write.json",
                {
                    **chunk_report,
                    "table_name": chunk_persistence_report["table_name"],
                    "doc_id": chunk_persistence_report["doc_id"],
                    "row_count": chunk_persistence_report["row_count"],
                    "records": chunk_persistence_report["records"],
                },
            )

        target_output_path = (
            Path(output_json).expanduser().resolve()
            if output_json
            else artifact_root / f"{resolved_input_path.stem}_pageindex_tree.json"
        )
        with _debug_stage(debug_recorder, "save_final_output"):
            _save_json(target_output_path, output)
        return output
    except Exception as exc:
        if debug_recorder is not None:
            debug_recorder.log_event(
                "run_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        raise
    finally:
        if debug_recorder is not None:
            debug_recorder.write_summary(
                target_output_path=str(target_output_path) if target_output_path else None,
                pageindex_raw_path=str(artifact_root / "pageindex_raw.json"),
                outline_entries_path=str(artifact_root / "outline_entries.json"),
                seeded_outline_path=str(artifact_root / "seeded_outline.json"),
                toc_assessment_path=str(artifact_root / "toc_assessment.json"),
                candidate_selection_path=str(artifact_root / "candidate_selection.json"),
            )


def _build_pdf_output(
    *,
    resolved_input_path: Path,
    artifact_root: Path,
    model: str,
    fallback_model: str | None,
    include_summary: bool,
    pdf_strategy: str,
    debug_recorder: DebugRecorder | None,
) -> dict[str, Any]:
    """Build the final output payload for one PDF input."""
    pageindex_module, utils_module, config_loader = _patch_pageindex_llm(
        model=model,
        fallback_model=fallback_model,
        debug_recorder=debug_recorder,
    )
    with _debug_stage(debug_recorder, "load_pageindex_config"):
        opt = config_loader.load(
            {
                "model": model,
                "if_add_node_id": "yes",
                "if_add_node_summary": "yes" if include_summary else "no",
                "if_add_doc_description": "no",
                "if_add_node_text": "yes",
            }
        )
    with _debug_stage(debug_recorder, "get_page_tokens"):
        page_list = _sanitize_page_token_list(
            utils_module.get_page_tokens(str(resolved_input_path), model=model)
        )
    with _debug_stage(debug_recorder, "extract_page_artifacts"):
        page_artifacts = extract_page_artifacts(resolved_input_path, artifact_root)
    with _debug_stage(debug_recorder, "extract_outline_entries"):
        toc_page_number, outline_entries = extract_outline_entries(page_artifacts)
    _save_json(artifact_root / "outline_entries.json", [asdict(entry) for entry in outline_entries])
    toc_assessment = assess_outline_confidence(page_artifacts, toc_page_number, outline_entries)
    _save_json(artifact_root / "toc_assessment.json", toc_assessment)

    candidate_plan = _resolve_pdf_candidate_plan(
        pdf_strategy=pdf_strategy,
        toc_assessment=toc_assessment,
        outline_entries=outline_entries,
    )
    if debug_recorder is not None:
        debug_recorder.log_event(
            "pdf_candidate_plan",
            requested_strategy=pdf_strategy,
            candidate_plan=candidate_plan,
            toc_assessment=toc_assessment,
        )

    candidate_records, successful_candidates = _collect_pdf_build_candidates(
        candidate_plan=candidate_plan,
        resolved_input_path=resolved_input_path,
        artifact_root=artifact_root,
        page_artifacts=page_artifacts,
        page_list=page_list,
        outline_entries=outline_entries,
        toc_page_number=toc_page_number,
        include_summary=include_summary,
        pageindex_module=pageindex_module,
        utils_module=utils_module,
        opt=opt,
        debug_recorder=debug_recorder,
    )
    if not successful_candidates:
        fallback_candidate: dict[str, Any] | None = None
        with _debug_stage(debug_recorder, "candidate_minimal_fallback"):
            try:
                fallback_candidate = _build_minimal_pdf_fallback_candidate(
                    resolved_input_path=resolved_input_path,
                    artifact_root=artifact_root,
                    page_artifacts=page_artifacts,
                    include_summary=include_summary,
                )
            except Exception as exc:  # noqa: PERF203
                if debug_recorder is not None:
                    debug_recorder.log_event(
                        "pdf_candidate_error",
                        strategy="minimal_fallback",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                candidate_records.append(
                    {
                        "strategy": "minimal_fallback",
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
            else:
                if fallback_candidate is not None:
                    successful_candidates.append(fallback_candidate)
                    candidate_records.append(
                        {
                            "strategy": fallback_candidate["strategy"],
                            "status": "success",
                            "score": fallback_candidate["score"],
                            "score_breakdown": fallback_candidate["score_breakdown"],
                            "metrics": fallback_candidate["metrics"],
                            "artifact_paths": fallback_candidate["artifact_paths"],
                        }
                    )
                else:
                    candidate_records.append(
                        {
                            "strategy": "minimal_fallback",
                            "status": "unsupported",
                            "error_type": "UnsupportedDocumentError",
                            "error_message": (
                                "PDF has insufficient extractable text for text-only tree "
                                "building and requires OCR or VLM support."
                            ),
                        }
                    )
    if not successful_candidates:
        if _document_has_extractable_text(page_artifacts):
            raise RuntimeError("All PDF build strategies failed to produce a candidate tree.")
        raise RuntimeError(
            "PDF has insufficient extractable text for text-only tree building and requires "
            "OCR or VLM support."
        )

    selected_candidate = _select_pdf_candidate(successful_candidates)
    selected_seeded_outline = selected_candidate.get("seeded_outline") or []
    _save_json(artifact_root / "seeded_outline.json", selected_seeded_outline)
    _save_json(
        artifact_root / "candidate_selection.json",
        {
            "requested_strategy": pdf_strategy,
            "candidate_plan": candidate_plan,
            "toc_assessment": toc_assessment,
            "selected_strategy": selected_candidate["strategy"],
            "candidates": candidate_records,
        },
    )

    raw_result = {
        "doc_name": resolved_input_path.name,
        "structure": selected_candidate["tree"],
    }
    _save_json(artifact_root / "pageindex_raw.json", raw_result)

    with _debug_stage(debug_recorder, "finalize_selected_candidate"):
        return {
            "doc_id": _stable_doc_id(resolved_input_path),
            "status": "completed",
            "retrieval_ready": False,
            "result": selected_candidate["result"],
        }


def _collect_pdf_build_candidates(
    *,
    candidate_plan: list[str],
    resolved_input_path: Path,
    artifact_root: Path,
    page_artifacts,
    page_list: list[tuple[str, int]],
    outline_entries,
    toc_page_number: int | None,
    include_summary: bool,
    pageindex_module,
    utils_module,
    opt,
    debug_recorder: DebugRecorder | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build, score, and persist PDF candidate trees for the requested strategy plan."""
    candidate_records: list[dict[str, Any]] = []
    successful_candidates: list[dict[str, Any]] = []
    for strategy in candidate_plan:
        with _debug_stage(debug_recorder, f"candidate_{strategy}"):
            try:
                candidate = _build_pdf_candidate(
                    strategy=strategy,
                    resolved_input_path=resolved_input_path,
                    artifact_root=artifact_root,
                    page_artifacts=page_artifacts,
                    page_list=page_list,
                    outline_entries=outline_entries,
                    toc_page_number=toc_page_number,
                    include_summary=include_summary,
                    pageindex_module=pageindex_module,
                    utils_module=utils_module,
                    opt=opt,
                    debug_recorder=debug_recorder,
                )
            except Exception as exc:  # noqa: PERF203
                if debug_recorder is not None:
                    debug_recorder.log_event(
                        "pdf_candidate_error",
                        strategy=strategy,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                candidate_records.append(
                    {
                        "strategy": strategy,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                continue
        successful_candidates.append(candidate)
        candidate_records.append(
            {
                "strategy": candidate["strategy"],
                "status": "success",
                "score": candidate["score"],
                "score_breakdown": candidate["score_breakdown"],
                "metrics": candidate["metrics"],
                "artifact_paths": candidate["artifact_paths"],
            }
        )
    return candidate_records, successful_candidates


def _build_pdf_candidate(
    *,
    strategy: str,
    resolved_input_path: Path,
    artifact_root: Path,
    page_artifacts,
    page_list: list[tuple[str, int]],
    outline_entries,
    toc_page_number: int | None,
    include_summary: bool,
    pageindex_module,
    utils_module,
    opt,
    debug_recorder: DebugRecorder | None,
) -> dict[str, Any]:
    """Build and score one PDF candidate tree for a concrete strategy."""
    candidate_root = artifact_root / "candidate_trees" / strategy
    candidate_root.mkdir(parents=True, exist_ok=True)
    seeded_outline: list[dict[str, Any]] = []

    if strategy == "toc_seeded":
        seeded_outline = _build_seeded_outline(
            page_artifacts=page_artifacts,
            outline_entries=outline_entries,
            toc_page_number=toc_page_number,
            allow_layout_fallback=False,
        )
        if not seeded_outline:
            raise ValueError("TOC-seeded strategy requires non-empty outline entries.")
        tree = asyncio.run(
            _build_tree_from_seeded_outline(
                seeded_outline=seeded_outline,
                page_list=page_list,
                pageindex_module=pageindex_module,
                utils_module=utils_module,
                opt=opt,
                debug_recorder=debug_recorder,
            )
        )
    elif strategy == "layout_fallback":
        seeded_outline = _build_heading_candidate_seeded_outline(page_artifacts)
        if not seeded_outline:
            raise ValueError("Layout fallback strategy could not find heading candidates.")
        tree = asyncio.run(
            _build_tree_from_seeded_outline(
                seeded_outline=seeded_outline,
                page_list=page_list,
                pageindex_module=pageindex_module,
                utils_module=utils_module,
                opt=opt,
                debug_recorder=debug_recorder,
            )
        )
    elif strategy == "pageindex_native":
        tree = asyncio.run(
            _build_tree_with_pageindex_native(
                page_list=page_list,
                pageindex_module=pageindex_module,
                utils_module=utils_module,
                opt=opt,
                debug_recorder=debug_recorder,
            )
        )
    else:
        raise ValueError(f"Unsupported PDF candidate strategy: {strategy}")

    utils_module.write_node_id(tree)
    utils_module.add_node_text(tree, page_list)
    if include_summary:
        asyncio.run(utils_module.generate_summaries_for_structure(tree, model=opt.model))

    raw_result = {
        "doc_name": resolved_input_path.name,
        "structure": tree,
    }
    converted_result = _convert_pageindex_structure(tree, include_summary=include_summary)
    normalized_result = _prepare_output_tree(converted_result, page_artifacts=page_artifacts)
    metrics = _summarize_output_tree(normalized_result, page_artifacts=page_artifacts)
    score, score_breakdown = _score_output_tree(metrics)

    artifact_paths = {
        "raw_tree_json": str(candidate_root / "pageindex_raw.json"),
        "result_json": str(candidate_root / "result.json"),
        "report_json": str(candidate_root / "report.json"),
    }
    if seeded_outline:
        artifact_paths["seeded_outline_json"] = str(candidate_root / "seeded_outline.json")
        _save_json(candidate_root / "seeded_outline.json", seeded_outline)
    _save_json(candidate_root / "pageindex_raw.json", raw_result)
    _save_json(candidate_root / "result.json", normalized_result)
    _save_json(
        candidate_root / "report.json",
        {
            "strategy": strategy,
            "score": score,
            "score_breakdown": score_breakdown,
            "metrics": metrics,
            "artifact_paths": artifact_paths,
        },
    )
    return {
        "strategy": strategy,
        "tree": tree,
        "result": normalized_result,
        "seeded_outline": seeded_outline,
        "score": score,
        "score_breakdown": score_breakdown,
        "metrics": metrics,
        "artifact_paths": artifact_paths,
    }


def _build_minimal_pdf_fallback_candidate(
    *,
    resolved_input_path: Path,
    artifact_root: Path,
    page_artifacts,
    include_summary: bool,
) -> dict[str, Any] | None:
    """Build one non-LLM PDF candidate directly from extractable page text and headings."""
    if not _document_has_extractable_text(page_artifacts):
        return None

    candidate_root = artifact_root / "candidate_trees" / "minimal_fallback"
    candidate_root.mkdir(parents=True, exist_ok=True)

    raw_tree = _build_minimal_text_fallback_tree(
        page_artifacts=page_artifacts,
        include_summary=include_summary,
    )
    if not raw_tree:
        return None

    normalized_result = _prepare_output_tree(raw_tree, page_artifacts=page_artifacts)
    metrics = _summarize_output_tree(normalized_result, page_artifacts=page_artifacts)
    score, score_breakdown = _score_output_tree(metrics)
    artifact_paths = {
        "raw_tree_json": str(candidate_root / "pageindex_raw.json"),
        "result_json": str(candidate_root / "result.json"),
        "report_json": str(candidate_root / "report.json"),
    }
    _save_json(
        candidate_root / "pageindex_raw.json",
        {
            "doc_name": resolved_input_path.name,
            "structure": raw_tree,
            "strategy": "minimal_fallback",
        },
    )
    _save_json(candidate_root / "result.json", normalized_result)
    _save_json(
        candidate_root / "report.json",
        {
            "strategy": "minimal_fallback",
            "score": score,
            "score_breakdown": score_breakdown,
            "metrics": metrics,
            "artifact_paths": artifact_paths,
        },
    )
    return {
        "strategy": "minimal_fallback",
        "tree": raw_tree,
        "result": normalized_result,
        "seeded_outline": [],
        "score": score,
        "score_breakdown": score_breakdown,
        "metrics": metrics,
        "artifact_paths": artifact_paths,
    }


async def _build_tree_with_pageindex_native(
    *,
    page_list: list[tuple[str, int]],
    pageindex_module,
    utils_module,
    opt,
    debug_recorder: DebugRecorder | None = None,
) -> list[dict[str, Any]]:
    """Delegate PDF tree extraction to the native PageIndex parser pipeline."""
    logger = _DebugLogger(debug_recorder) if debug_recorder is not None else _NullLogger()
    try:
        return await pageindex_module.tree_parser(page_list, opt, logger=logger)
    except KeyError as exc:
        if str(exc) not in {"'toc_detected'", "'page_index_given_in_toc'"}:
            raise
        logger.error(f"Falling back to process_no_toc after native TOC parsing error: {exc}")
        toc_with_page_number = await pageindex_module.meta_processor(
            page_list,
            mode="process_no_toc",
            start_index=1,
            opt=opt,
            logger=logger,
        )
        toc_with_page_number = pageindex_module.add_preface_if_needed(toc_with_page_number)
        toc_with_page_number = await pageindex_module.check_title_appearance_in_start_concurrent(
            toc_with_page_number,
            page_list,
            model=opt.model,
            logger=logger,
        )
        valid_toc_items = [
            item for item in toc_with_page_number if item.get("physical_index") is not None
        ]
        toc_tree = utils_module.post_processing(valid_toc_items, len(page_list))
        tasks = [
            pageindex_module.process_large_node_recursively(node, page_list, opt, logger=logger)
            for node in toc_tree
        ]
        await asyncio.gather(*tasks)
        return toc_tree


def _resolve_pdf_candidate_plan(
    *,
    pdf_strategy: str,
    toc_assessment: dict[str, Any],
    outline_entries,
) -> list[str]:
    """Resolve the ordered PDF candidate plan for one build run."""
    if pdf_strategy != "auto":
        return [pdf_strategy]
    if toc_assessment.get("confidence") == "high":
        return ["toc_seeded", "pageindex_native", "layout_fallback"]
    if toc_assessment.get("confidence") == "low" and outline_entries:
        return ["pageindex_native", "toc_seeded", "layout_fallback"]
    return ["pageindex_native", "layout_fallback"]


def _select_pdf_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the highest-quality PDF candidate using score and structural tie-breakers."""
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(candidate["score"]),
            int(candidate["metrics"]["max_depth"]),
            -int(candidate["metrics"]["root_count"]),
            float(candidate["metrics"]["heading_alignment_ratio"]),
        ),
        reverse=True,
    )
    best = ranked[0]
    if len(ranked) == 1:
        return best
    runner_up = ranked[1]
    if abs(float(best["score"]) - float(runner_up["score"])) <= 1.0:
        best_key = (
            int(best["metrics"]["max_depth"]),
            -int(best["metrics"]["root_count"]),
            float(best["metrics"]["heading_alignment_ratio"]),
        )
        runner_key = (
            int(runner_up["metrics"]["max_depth"]),
            -int(runner_up["metrics"]["root_count"]),
            float(runner_up["metrics"]["heading_alignment_ratio"]),
        )
        if runner_key > best_key:
            return runner_up
    return best


def _build_markdown_output(
    *,
    resolved_input_path: Path,
    artifact_root: Path,
    model: str,
    fallback_model: str | None,
    include_summary: bool,
    markdown_layout: str,
    debug_recorder: DebugRecorder | None,
) -> dict[str, Any]:
    """Build the final output payload for one Markdown input."""
    resolved_layout = _resolve_markdown_layout(resolved_input_path, markdown_layout)
    if debug_recorder is not None:
        debug_recorder.log_event(
            "markdown_layout_selected",
            input_path=str(resolved_input_path),
            requested_layout=markdown_layout,
            resolved_layout=resolved_layout,
        )
    with _debug_stage(debug_recorder, "build_markdown_pageindex"):
        payload = sync_build_pageindex_payload(
            resolved_input_path,
            PageIndexOptions(
                doc_id=_stable_doc_id(resolved_input_path),
                status="completed",
                retrieval_ready=False,
                if_add_summary=include_summary,
                model=model,
            ),
            llm_factory=lambda: QwenChatClient(
                primary_model=model,
                fallback_model=fallback_model,
                enable_thinking=False,
                strip_thinking_field=True,
                debug_recorder=debug_recorder,
            ),
            layout=resolved_layout,
        )
    _save_json(artifact_root / "pageindex_raw.json", payload)
    return payload


def _resolve_input_path(*, input_path: str | None, pdf_path: str | None) -> Path:
    """Resolve the one allowed build input path and validate its existence."""
    provided_paths = [value for value in (input_path, pdf_path) if value]
    if not provided_paths:
        raise ValueError("Either input_path or pdf_path must be provided.")
    if input_path and pdf_path:
        raise ValueError("Only one of input_path or pdf_path may be provided.")
    resolved_path = Path(provided_paths[0]).expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Input file not found: {resolved_path}")
    return resolved_path


def _detect_input_kind(input_path: Path) -> str:
    """Return the normalized input kind for one build input path."""
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    raise ValueError(f"Unsupported input extension for DemoIndex build: {input_path.suffix}")


def _resolve_markdown_layout(input_path: Path, requested_layout: str) -> str:
    """Resolve the effective Markdown layout for one Markdown input."""
    if requested_layout != "auto":
        return requested_layout
    content = input_path.read_text(encoding="utf-8")
    if "<!-- page:" in content:
        return "page_per_page"
    return "h1_forest"


async def _build_tree_from_seeded_outline(
    *,
    seeded_outline: list[dict[str, Any]],
    page_list: list[tuple[str, int]],
    pageindex_module,
    utils_module,
    opt,
    debug_recorder: DebugRecorder | None = None,
) -> list[dict[str, Any]]:
    """Build a tree from a seeded top-level outline using PageIndex recursion."""
    logger = _DebugLogger(debug_recorder) if debug_recorder is not None else _NullLogger()
    outline_with_start_flags = await pageindex_module.check_title_appearance_in_start_concurrent(
        seeded_outline,
        page_list,
        model=opt.model,
        logger=logger,
    )
    valid_outline = [item for item in outline_with_start_flags if item.get("physical_index") is not None]
    tree = utils_module.post_processing(valid_outline, len(page_list))
    tasks = [
        pageindex_module.process_large_node_recursively(node, page_list, opt=opt, logger=logger)
        for node in tree
    ]
    await asyncio.gather(*tasks)
    return tree


def _normalize_output_tree(
    nodes: list[dict[str, Any]],
    *,
    page_artifacts,
) -> list[dict[str, Any]]:
    """Normalize one converted output tree with generic hierarchy and title cleanup."""
    normalized = _normalize_output_sibling_group(copy.deepcopy(nodes))
    pruned = _prune_low_quality_roots(normalized, page_artifacts=page_artifacts)
    return pruned or normalized


def _prepare_output_tree(
    nodes: list[dict[str, Any]],
    *,
    page_artifacts,
) -> list[dict[str, Any]]:
    """Normalize one output tree, then fill any missing IDs or summaries deterministically."""
    normalized = _normalize_output_tree(nodes, page_artifacts=page_artifacts)
    return _ensure_output_tree_integrity(normalized)


def _ensure_output_tree_integrity(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill missing node IDs and summaries after structural normalization."""
    seen_node_ids: set[str] = set()
    return _ensure_output_tree_integrity_group(nodes, path_prefix=[], seen_node_ids=seen_node_ids)


def _ensure_output_tree_integrity_group(
    nodes: list[dict[str, Any]],
    *,
    path_prefix: list[int],
    seen_node_ids: set[str],
) -> list[dict[str, Any]]:
    """Apply deterministic integrity fixes to one sibling group recursively."""
    normalized_group: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        current_path = [*path_prefix, index]
        normalized_node = dict(node)
        title = _sanitize_output_title(str(normalized_node.get("title") or "")).strip()
        normalized_node["title"] = title or f"Section {'.'.join(str(value) for value in current_path)}"
        normalized_node["text"] = str(normalized_node.get("text") or "")
        children = _ensure_output_tree_integrity_group(
            normalized_node.get("nodes") or [],
            path_prefix=current_path,
            seen_node_ids=seen_node_ids,
        )
        if children:
            normalized_node["nodes"] = children
        else:
            normalized_node.pop("nodes", None)
        normalized_node["node_id"] = _reserve_output_node_id(
            existing_node_id=str(normalized_node.get("node_id") or "").strip(),
            title=str(normalized_node["title"]),
            page_index=_coerce_output_page_index(normalized_node.get("page_index")),
            path_indexes=current_path,
            seen_node_ids=seen_node_ids,
        )
        summary = str(normalized_node.get("summary") or "").strip()
        if not summary:
            summary = _build_output_node_summary(
                title=str(normalized_node["title"]),
                text=str(normalized_node.get("text") or ""),
                child_nodes=children,
            )
        normalized_node["summary"] = summary
        normalized_group.append(normalized_node)
    return normalized_group


def _reserve_output_node_id(
    *,
    existing_node_id: str,
    title: str,
    page_index: int | None,
    path_indexes: list[int],
    seen_node_ids: set[str],
) -> str:
    """Reserve one unique node ID for the final output tree."""
    candidate = existing_node_id.strip()
    if candidate and candidate not in seen_node_ids:
        seen_node_ids.add(candidate)
        return candidate
    regenerated = _build_synthetic_output_node_id(
        title=title,
        page_index=page_index,
        path_indexes=path_indexes,
    )
    while regenerated in seen_node_ids:
        regenerated = f"{regenerated}:{len(seen_node_ids) + 1}"
    seen_node_ids.add(regenerated)
    return regenerated


def _build_synthetic_output_node_id(
    *,
    title: str,
    page_index: int | None,
    path_indexes: list[int],
) -> str:
    """Build one deterministic synthetic node ID from stable structural anchors."""
    path_key = ".".join(str(value) for value in path_indexes)
    title_key = _normalized_title_key(title) or "untitled"
    page_key = str(page_index) if page_index is not None else "na"
    return f"synthetic:{page_key}:{path_key}:{title_key}"


def _build_output_node_summary(
    *,
    title: str,
    text: str,
    child_nodes: list[dict[str, Any]],
) -> str:
    """Build one concise fallback summary for nodes that lack a model-generated summary."""
    compact_text = " ".join(text.split()).strip()
    if compact_text:
        return _truncate_summary(compact_text)
    child_titles = [
        str(child.get("title") or "").strip()
        for child in child_nodes
        if str(child.get("title") or "").strip()
    ]
    if child_titles:
        preview = "；".join(child_titles[:2])
        if title and title not in preview and not _is_chapter_marker_title(title):
            return _truncate_summary(f"{title}：{preview}")
        return _truncate_summary(preview)
    return _truncate_summary(title or "Section")


def _truncate_summary(text: str, limit: int = 160) -> str:
    """Clamp one synthesized summary to a small storage-friendly length."""
    stripped = str(text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip("，。；;:： ") + "…"


def _coerce_output_page_index(value: Any) -> int | None:
    """Convert one output page index into an int when possible."""
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_output_sibling_group(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize one sibling group recursively before tree scoring or persistence."""
    normalized_group: list[dict[str, Any]] = []
    for node in nodes:
        normalized_node = dict(node)
        normalized_node["title"] = _sanitize_output_title(str(node.get("title") or ""))
        children = node.get("nodes") or []
        if children:
            normalized_children = _normalize_output_sibling_group(children)
            if normalized_children:
                normalized_node["nodes"] = normalized_children
            else:
                normalized_node.pop("nodes", None)
        else:
            normalized_node.pop("nodes", None)
        normalized_group.append(normalized_node)
    normalized_group = _group_nodes_under_chapter_markers(normalized_group)
    normalized_group = _restructure_numbered_siblings(normalized_group)
    return _dedupe_sibling_titles(normalized_group)


def _group_nodes_under_chapter_markers(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive siblings under chapter marker roots such as `01` or `第二章`."""
    if sum(1 for node in nodes if _is_chapter_marker_title(str(node.get("title") or ""))) < 2:
        return nodes
    grouped: list[dict[str, Any]] = []
    current_chapter: dict[str, Any] | None = None
    for node in nodes:
        title = str(node.get("title") or "")
        if _is_chapter_marker_title(title):
            grouped.append(node)
            current_chapter = node
            continue
        if current_chapter is None:
            grouped.append(node)
            continue
        current_page = current_chapter.get("page_index")
        node_page = node.get("page_index")
        if (
            current_page is not None
            and node_page is not None
            and int(node_page) >= int(current_page)
        ):
            current_chapter.setdefault("nodes", []).append(node)
            continue
        grouped.append(node)
    return grouped


def _restructure_numbered_siblings(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restructure flat numbered siblings into a deeper hierarchy when parents are implicit."""
    numbered_nodes = [
        node
        for node in nodes
        if _parse_section_number(str(node.get("title") or "")) is not None
    ]
    if len(numbered_nodes) < 2:
        return nodes

    root_nodes: list[dict[str, Any]] = []
    keyed_nodes: dict[str, dict[str, Any]] = {}
    for node in nodes:
        info = _parse_section_number(str(node.get("title") or ""))
        if info is None:
            root_nodes.append(node)
            continue
        key = str(info["key"])
        level = int(info["level"])
        if level <= 1:
            root_nodes.append(node)
            keyed_nodes[key] = node
            continue
        parent_key = _parent_number_key(key)
        if not parent_key:
            root_nodes.append(node)
            keyed_nodes[key] = node
            continue
        parent_node = keyed_nodes.get(parent_key)
        if parent_node is None:
            parent_node = _ensure_numbered_parent_chain(
                parent_key=parent_key,
                keyed_nodes=keyed_nodes,
                root_nodes=root_nodes,
                reference_node=node,
            )
        parent_node.setdefault("nodes", []).append(node)
        keyed_nodes[key] = node
    return root_nodes


def _ensure_numbered_parent_chain(
    *,
    parent_key: str,
    keyed_nodes: dict[str, dict[str, Any]],
    root_nodes: list[dict[str, Any]],
    reference_node: dict[str, Any],
) -> dict[str, Any]:
    """Create missing numbered ancestors using the observed section marker as the title."""
    current_parent: dict[str, Any] | None = None
    for depth in range(1, len(parent_key.split(".")) + 1):
        key = ".".join(parent_key.split(".")[:depth])
        if key in keyed_nodes:
            current_parent = keyed_nodes[key]
            continue
        synthetic_parent = {
            "title": key,
            "node_id": None,
            "page_index": reference_node.get("page_index"),
            "text": "",
            "nodes": [],
        }
        if current_parent is None:
            root_nodes.append(synthetic_parent)
        else:
            current_parent.setdefault("nodes", []).append(synthetic_parent)
        keyed_nodes[key] = synthetic_parent
        current_parent = synthetic_parent
    return keyed_nodes[parent_key]


def _dedupe_sibling_titles(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge exact sibling duplicates by normalized title and page anchor."""
    deduped: list[dict[str, Any]] = []
    seen: dict[tuple[str, int | None], dict[str, Any]] = {}
    for node in nodes:
        key = (_normalized_title_key(str(node.get("title") or "")), node.get("page_index"))
        existing = seen.get(key)
        if existing is None:
            deduped.append(node)
            seen[key] = node
            continue
        existing_children = existing.setdefault("nodes", [])
        for child in node.get("nodes") or []:
            existing_children.append(child)
        if len(str(node.get("text") or "")) > len(str(existing.get("text") or "")):
            existing["text"] = node.get("text", "")
        if node.get("summary") and not existing.get("summary"):
            existing["summary"] = node["summary"]
    for node in deduped:
        if node.get("nodes"):
            node["nodes"] = _dedupe_sibling_titles(node["nodes"])
            if not node["nodes"]:
                node.pop("nodes", None)
    return deduped


def _prune_low_quality_roots(
    nodes: list[dict[str, Any]],
    *,
    page_artifacts,
) -> list[dict[str, Any]]:
    """Drop clearly sentence-like or caption-like roots when the page heading evidence is weak."""
    if len(nodes) < 8:
        return nodes
    pruned = [
        node
        for node in nodes
        if not _is_low_quality_root(node, page_artifacts=page_artifacts)
    ]
    return pruned or nodes


def _is_low_quality_root(node: dict[str, Any], *, page_artifacts) -> bool:
    """Return whether one leaf root looks like a sentence fragment instead of a section heading."""
    if node.get("nodes"):
        return False
    if _matches_page_heading(node, page_artifacts=page_artifacts):
        return False
    title = str(node.get("title") or "")
    return _is_sentence_like_title(title) or _is_caption_like_title(title)


def _summarize_output_tree(
    nodes: list[dict[str, Any]],
    *,
    page_artifacts,
) -> dict[str, Any]:
    """Compute one generic quality summary for a normalized output tree."""
    flattened = _flatten_tree_with_depth(nodes)
    node_count = len(flattened)
    root_count = len(nodes)
    max_depth = max((item["depth"] for item in flattened), default=0)
    unique_pages = sorted(
        {
            int(item["page_index"])
            for item in flattened
            if isinstance(item.get("page_index"), int)
        }
    )
    title_keys = [_normalized_title_key(str(item.get("title") or "")) for item in flattened]
    duplicate_title_count = node_count - len(set(title_keys))
    heading_alignment_count = sum(
        1 for item in flattened if _matches_page_heading(item, page_artifacts=page_artifacts)
    )
    numbered_nodes = [
        item
        for item in flattened
        if _parse_section_number(str(item.get("title") or "")) is not None
    ]
    numbered_consistent = sum(
        1
        for item in numbered_nodes
        if _is_numbering_depth_consistent(str(item.get("title") or ""), int(item["depth"]))
    )
    ordered_pages = [
        int(item["page_index"])
        for item in flattened
        if isinstance(item.get("page_index"), int)
    ]
    page_backtrack_count = sum(
        1
        for index in range(1, len(ordered_pages))
        if ordered_pages[index] < ordered_pages[index - 1]
    )
    depth_histogram = Counter(str(item["depth"]) for item in flattened)
    bad_root_count = sum(
        1 for node in nodes if _is_low_quality_root(node, page_artifacts=page_artifacts)
    )
    total_pages = max(1, len(page_artifacts))
    internal_node_count = sum(1 for item in flattened if item.get("nodes"))
    return {
        "root_count": root_count,
        "node_count": node_count,
        "max_depth": max_depth,
        "depth_histogram": dict(sorted(depth_histogram.items())),
        "unique_page_count": len(unique_pages),
        "page_coverage_ratio": round(len(unique_pages) / total_pages, 4),
        "duplicate_title_count": duplicate_title_count,
        "title_uniqueness_ratio": round(
            1.0 - (duplicate_title_count / node_count if node_count else 0.0),
            4,
        ),
        "heading_alignment_ratio": round(
            heading_alignment_count / node_count if node_count else 0.0,
            4,
        ),
        "numbered_consistency_ratio": round(
            numbered_consistent / len(numbered_nodes) if numbered_nodes else 0.0,
            4,
        ),
        "page_backtrack_count": page_backtrack_count,
        "bad_root_count": bad_root_count,
        "internal_node_count": internal_node_count,
    }


def _score_output_tree(metrics: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Turn one generic tree quality summary into a scalar score plus breakdown."""
    node_count = int(metrics["node_count"])
    root_count = int(metrics["root_count"])
    max_depth = int(metrics["max_depth"])
    internal_node_count = int(metrics["internal_node_count"])
    root_ratio = (root_count / node_count) if node_count else 1.0
    breakdown = {
        "non_empty": 30.0 if node_count > 0 else -1000.0,
        "page_coverage": round(float(metrics["page_coverage_ratio"]) * 20.0, 3),
        "depth": round(min(18.0, max(0, max_depth - 1) * 8.0), 3),
        "internal_nodes": round(min(12.0, internal_node_count * 2.0), 3),
        "root_shape": round(max(0.0, 12.0 - (root_ratio * 12.0)), 3),
        "heading_alignment": round(float(metrics["heading_alignment_ratio"]) * 16.0, 3),
        "numbering": round(float(metrics["numbered_consistency_ratio"]) * 12.0, 3),
        "title_uniqueness": round(float(metrics["title_uniqueness_ratio"]) * 10.0, 3),
        "penalty_flat": -10.0 if node_count >= 10 and root_count == node_count else 0.0,
        "penalty_shallow": -8.0 if node_count >= 10 and max_depth <= 1 else 0.0,
        "penalty_root_explosion": round(
            -2.0 * max(0, root_count - max(8, max_depth * 4)),
            3,
        ),
        "penalty_bad_roots": round(-4.0 * int(metrics["bad_root_count"]), 3),
        "penalty_duplicates": round(-0.75 * int(metrics["duplicate_title_count"]), 3),
        "penalty_backtrack": round(-2.5 * int(metrics["page_backtrack_count"]), 3),
    }
    score = round(sum(breakdown.values()), 3)
    return score, breakdown


def _flatten_tree_with_depth(
    nodes: list[dict[str, Any]],
    depth: int = 1,
) -> list[dict[str, Any]]:
    """Flatten one output tree while preserving depth metadata for scoring."""
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        flattened.append(
            {
                **node,
                "depth": depth,
            }
        )
        flattened.extend(_flatten_tree_with_depth(node.get("nodes") or [], depth + 1))
    return flattened


def _matches_page_heading(node: dict[str, Any], *, page_artifacts) -> bool:
    """Return whether one node title aligns with the page's prominent heading candidates."""
    page_index = node.get("page_index")
    if not isinstance(page_index, int) or page_index < 1 or page_index > len(page_artifacts):
        return False
    node_title = _strip_structural_prefix(str(node.get("title") or ""))
    node_normalized = normalize_text(node_title)
    if not node_normalized:
        return False
    candidates = layout_heading_candidates(page_artifacts[page_index - 1], limit=5)
    return any(
        _normalized_titles_overlap(
            node_normalized,
            normalize_text(_strip_structural_prefix(str(candidate.get("title") or ""))),
        )
        for candidate in candidates
    )


def _normalized_titles_overlap(left: str, right: str) -> bool:
    """Return whether two normalized titles likely describe the same heading."""
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    shared = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        shared += 1
    return shared >= 4


def _normalized_title_key(title: str) -> str:
    """Build one normalized title key for dedupe and comparison logic."""
    return normalize_text(_strip_structural_prefix(_sanitize_output_title(title)))


def _strip_structural_prefix(title: str) -> str:
    """Remove one common numbering prefix from a section title when present."""
    info = _parse_section_number(title)
    if info is None:
        return title.strip()
    remainder = str(info.get("title_without_prefix") or "").strip()
    return remainder or title.strip()


def _parse_section_number(title: str) -> dict[str, Any] | None:
    """Parse one generic numbering prefix from a title when it looks like a section marker."""
    stripped = title.strip()
    patterns = [
        re.compile(r"^(?P<prefix>\d+(?:\.\d+)+)\s*(?P<rest>.*)$"),
        re.compile(r"^(?P<prefix>\d{1,2})(?:[\s、.．:：\-]+(?P<rest>.*))?$"),
        re.compile(r"^第(?P<prefix>[一二三四五六七八九十\d]+)章\s*(?P<rest>.*)$"),
        re.compile(r"^(?:[（(](?P<prefix>[一二三四五六七八九十\d]+)[）)]|(?P<prefix2>[一二三四五六七八九十]+)[、.])\s*(?P<rest>.*)$"),
    ]
    for pattern in patterns:
        match = pattern.match(stripped)
        if match is None:
            continue
        prefix = match.groupdict().get("prefix") or match.groupdict().get("prefix2")
        if not prefix:
            continue
        if prefix.isdigit() and len(prefix) > 2 and "." not in prefix:
            continue
        rest = (match.groupdict().get("rest") or "").strip()
        key = prefix
        level = key.count(".") + 1 if "." in key else 1
        return {
            "key": key,
            "level": level,
            "title_without_prefix": rest,
        }
    return None


def _parent_number_key(number_key: str) -> str | None:
    """Return the parent section key for one dotted numbering scheme."""
    if "." not in number_key:
        return None
    return number_key.rsplit(".", 1)[0]


def _is_numbering_depth_consistent(title: str, depth: int) -> bool:
    """Return whether one title's numbering level broadly matches its tree depth."""
    info = _parse_section_number(title)
    if info is None:
        return True
    return abs(int(info["level"]) - depth) <= 1


def _is_chapter_marker_title(title: str) -> bool:
    """Return whether one title is a standalone chapter marker such as `01` or `第三章`."""
    stripped = title.strip()
    return bool(
        re.fullmatch(r"\d{2}", stripped)
        or re.fullmatch(r"第[一二三四五六七八九十\d]+章", stripped)
        or re.fullmatch(r"[一二三四五六七八九十]+", stripped)
    )


def _is_sentence_like_title(title: str) -> bool:
    """Return whether one title resembles a sentence fragment instead of a heading."""
    stripped = title.strip()
    if len(stripped) >= 36:
        return True
    if any(punctuation in stripped for punctuation in ("，", "。", "；", ";", "？", "！", "、")):
        return True
    return stripped.count(" ") >= 5 and len(stripped) >= 24


def _is_caption_like_title(title: str) -> bool:
    """Return whether one title resembles a figure/table caption."""
    stripped = title.strip()
    return bool(
        re.match(r"^[图表]\s*\d+", stripped)
        or stripped.startswith("资料来源")
        or stripped.startswith("来源：")
    )


def compare_tree(actual_json: str, expected_json: str) -> dict[str, Any]:
    """Compare two PageIndex-style tree JSON files."""
    actual = json.loads(Path(actual_json).expanduser().resolve().read_text(encoding="utf-8"))
    expected = json.loads(Path(expected_json).expanduser().resolve().read_text(encoding="utf-8"))

    actual_roots = actual.get("result") or []
    expected_roots = expected.get("result") or []
    actual_nodes = _flatten_tree(actual_roots)
    expected_nodes = _flatten_tree(expected_roots)
    actual_nodes_with_depth = _flatten_tree_with_depth(actual_roots)
    expected_nodes_with_depth = _flatten_tree_with_depth(expected_roots)
    actual_titles = [node["title"] for node in actual_nodes]
    expected_titles = [node["title"] for node in expected_nodes]

    actual_title_set = set(actual_titles)
    expected_title_set = set(expected_titles)
    matched_titles = sorted(actual_title_set & expected_title_set)

    page_matches = 0
    exact_page_matches = 0
    page_examples: list[dict[str, Any]] = []
    expected_by_title = {node["title"]: node for node in expected_nodes}
    expected_depth_by_title = {
        node["title"]: int(node["depth"])
        for node in expected_nodes_with_depth
    }
    actual_depth_by_title = {
        node["title"]: int(node["depth"])
        for node in actual_nodes_with_depth
    }
    for title in matched_titles:
        actual_page = next(node["page_index"] for node in actual_nodes if node["title"] == title)
        expected_page = expected_by_title[title]["page_index"]
        if actual_page == expected_page:
            exact_page_matches += 1
        is_match = (
            actual_page is not None
            and expected_page is not None
            and abs(int(actual_page) - int(expected_page)) <= 1
        )
        if is_match:
            page_matches += 1
        if len(page_examples) < 20 and not is_match:
            page_examples.append(
                {
                    "title": title,
                    "actual_page_index": actual_page,
                    "expected_page_index": expected_page,
                    "actual_depth": actual_depth_by_title.get(title),
                    "expected_depth": expected_depth_by_title.get(title),
                }
            )

    return {
        "actual_root_titles": [node["title"] for node in actual_roots],
        "expected_root_titles": [node["title"] for node in expected_roots],
        "top_level_schema_match": list(actual.keys()) == list(expected.keys()),
        "actual_root_count": len(actual_roots),
        "expected_root_count": len(expected_roots),
        "actual_node_count": len(actual_nodes),
        "expected_node_count": len(expected_nodes),
        "actual_max_depth": max((node["depth"] for node in actual_nodes_with_depth), default=0),
        "expected_max_depth": max((node["depth"] for node in expected_nodes_with_depth), default=0),
        "matched_title_count": len(matched_titles),
        "exact_page_match_count": exact_page_matches,
        "title_precision": _ratio(len(matched_titles), len(actual_title_set)),
        "title_recall": _ratio(len(matched_titles), len(expected_title_set)),
        "page_match_ratio_within_one": _ratio(page_matches, len(matched_titles)),
        "actual_depth_histogram": dict(
            sorted(Counter(str(node["depth"]) for node in actual_nodes_with_depth).items())
        ),
        "expected_depth_histogram": dict(
            sorted(Counter(str(node["depth"]) for node in expected_nodes_with_depth).items())
        ),
        "missing_titles": sorted(expected_title_set - actual_title_set)[:30],
        "unexpected_titles": sorted(actual_title_set - expected_title_set)[:30],
        "page_mismatches": page_examples,
        "text_length_examples": [
            {
                "title": title,
                "actual_text_length": len(next(node["text"] for node in actual_nodes if node["title"] == title) or ""),
                "expected_text_length": len(expected_by_title[title]["text"] or ""),
            }
            for title in matched_titles[:15]
        ],
    }


def _patch_pageindex_llm(
    model: str,
    fallback_model: str | None,
    debug_recorder: DebugRecorder | None = None,
):
    """Patch PageIndex modules to use the local qwen-compatible client."""
    import importlib

    pageindex_module = importlib.import_module("pageindex.page_index")
    utils_module = importlib.import_module("pageindex.utils")
    config = get_demoindex_config()

    client = QwenChatClient(
        primary_model=model,
        fallback_model=fallback_model,
        timeout_seconds=config.llm.timeout_seconds,
        max_retries=config.llm.max_retries,
        retry_base_seconds=config.llm.retry_base_seconds,
        max_concurrency=config.llm.max_concurrency,
        enable_thinking=False,
        strip_thinking_field=True,
        debug_recorder=debug_recorder,
    )
    utils_module.llm_completion = client.completion
    utils_module.llm_acompletion = client.acompletion
    pageindex_module.llm_completion = client.completion
    pageindex_module.llm_acompletion = client.acompletion
    return pageindex_module, utils_module, utils_module.ConfigLoader()


def _load_pageindex_utils():
    """Load the PageIndex utilities module for shared helpers such as token counting."""
    import importlib

    return importlib.import_module("pageindex.utils")


def _build_seeded_outline(
    *,
    page_artifacts,
    outline_entries,
    toc_page_number: int | None,
    allow_layout_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Build a seeded outline that mirrors PageIndex's flat TOC item format."""
    if not outline_entries:
        if allow_layout_fallback:
            return _build_heading_candidate_seeded_outline(page_artifacts)
        return []

    seeded: list[dict[str, Any]] = []
    cover_title = _extract_cover_title(page_artifacts)
    include_cover = bool(cover_title and toc_page_number and toc_page_number > 1)
    include_toc = bool(include_cover and toc_page_number is not None)

    if include_cover:
        seeded.append({"structure": "0", "title": cover_title, "physical_index": 1})
    if include_toc and toc_page_number is not None:
        seeded.append({"structure": "0.1", "title": "目录", "physical_index": toc_page_number})

    counters: list[int] = []
    top_level_seed = 1 if include_toc else 0
    for entry in outline_entries:
        if entry.physical_page is None:
            continue
        level = _effective_outline_level(entry, page_artifacts)
        while len(counters) < level:
            counters.append(0)
        counters = counters[:level]
        if level == 1 and counters[0] == 0:
            counters[0] = top_level_seed
        counters[-1] += 1
        structure_parts = [str(value) for value in counters]
        if include_cover:
            structure = ".".join(["0", *structure_parts])
        else:
            structure = ".".join(structure_parts)
        seeded.append(
            {
                "structure": structure,
                "title": _resolved_entry_title(entry, page_artifacts),
                "physical_index": entry.physical_page,
            }
        )
    return seeded


def _build_heading_candidate_seeded_outline(page_artifacts) -> list[dict[str, Any]]:
    """Build top-level seeded outline items from per-page heading candidates when no TOC is available."""
    seeded: list[dict[str, Any]] = []
    last_normalized_title: str | None = None
    for page_artifact in page_artifacts:
        title = _select_heading_candidate_title(page_artifact)
        if not title:
            continue
        normalized_title = normalize_text(title)
        if not normalized_title or normalized_title == last_normalized_title:
            continue
        seeded.append(
            {
                "structure": str(len(seeded) + 1),
                "title": title,
                "physical_index": page_artifact.page_number,
            }
        )
        last_normalized_title = normalized_title
    return seeded


def _build_minimal_text_fallback_tree(
    *,
    page_artifacts,
    include_summary: bool,
) -> list[dict[str, Any]]:
    """Build one minimal page-level tree directly from extractable text without LLM calls."""
    nodes: list[dict[str, Any]] = []
    last_normalized_title: str | None = None
    for page_artifact in page_artifacts:
        if not _page_has_extractable_text(page_artifact):
            continue
        title = _select_minimal_fallback_title(page_artifact)
        if not title:
            continue
        normalized_title = _normalized_title_key(title)
        if normalized_title and normalized_title == last_normalized_title:
            continue
        text = str(page_artifact.plain_text or "").strip()
        if not text:
            continue
        node: dict[str, Any] = {
            "title": title,
            "page_index": page_artifact.page_number,
            "text": text,
        }
        if include_summary:
            node["summary"] = _build_output_node_summary(
                title=title,
                text=text,
                child_nodes=[],
            )
        nodes.append(node)
        last_normalized_title = normalized_title
    return nodes


def _document_has_extractable_text(page_artifacts) -> bool:
    """Return whether a PDF contains enough text to justify text-only tree building."""
    meaningful_pages = sum(1 for page_artifact in page_artifacts if _page_has_extractable_text(page_artifact))
    total_text_length = sum(len(str(page_artifact.plain_text or "").strip()) for page_artifact in page_artifacts)
    return meaningful_pages >= 2 and total_text_length >= 80


def _page_has_extractable_text(page_artifact) -> bool:
    """Return whether one page exposes enough text for a non-OCR fallback."""
    return len(str(page_artifact.plain_text or "").strip()) >= 8 or page_artifact.text_block_count >= 2


def _select_minimal_fallback_title(page_artifact) -> str | None:
    """Select one robust page title for the minimal text fallback tree."""
    heading_title = _select_heading_candidate_title(page_artifact)
    if _is_viable_minimal_fallback_title(heading_title):
        return heading_title

    line_title = _top_line_fallback_title(page_artifact)
    if _is_viable_minimal_fallback_title(line_title):
        return line_title

    plain_text_title = _plain_text_fallback_title(str(page_artifact.plain_text or ""))
    if _is_viable_minimal_fallback_title(plain_text_title):
        return plain_text_title
    return None


def _top_line_fallback_title(page_artifact) -> str | None:
    """Combine prominent top-of-page lines into one concise fallback title."""
    top_lines = [
        " ".join(str(line_text).split()).strip()
        for line_text, bbox in sorted(page_artifact.lines, key=lambda item: (item[1][1], item[1][0]))
        if bbox[1] <= page_artifact.page_height * 0.35
    ]
    if not top_lines:
        return None

    selected_parts: list[str] = []
    for line_text in top_lines[:3]:
        if not line_text:
            continue
        selected_parts.append(line_text)
        combined = _sanitize_output_title(" ".join(selected_parts))
        if len(combined) >= 6 or _parse_section_number(combined) is not None:
            return combined[:80].strip()
    combined = _sanitize_output_title(" ".join(selected_parts))
    return combined[:80].strip() or None


def _plain_text_fallback_title(text: str) -> str | None:
    """Derive one short title candidate from the page's plain text prefix."""
    compact = " ".join(str(text or "").split()).strip()
    if not compact:
        return None
    sentence = re.split(r"[。！？!?；;\n]", compact, maxsplit=1)[0].strip()
    sentence = _sanitize_output_title(sentence)
    if len(sentence) > 80:
        sentence = sentence[:80].rstrip("，。；;:： ")
    return sentence or None


def _is_viable_minimal_fallback_title(title: str | None) -> bool:
    """Return whether one text-derived title is concise enough to anchor a fallback node."""
    cleaned = str(title or "").strip()
    if not cleaned or normalize_text(cleaned) == "目录":
        return False
    if len(cleaned) < 4 and _parse_section_number(cleaned) is None and not re.match(r"^Q\d+$", cleaned, flags=re.I):
        return False
    if _is_sentence_like_title(cleaned) and _parse_section_number(cleaned) is None:
        return False
    return True


def _select_heading_candidate_title(page_artifact) -> str | None:
    """Return the best concise page heading title for TOC-less fallback seeding."""
    for candidate in layout_heading_candidates(page_artifact, limit=5):
        title = " ".join(str(candidate.get("title") or "").split()).strip()
        normalized = normalize_text(title)
        if not normalized or normalized == "目录":
            continue
        if len(title) < 4 and _parse_section_number(title) is None:
            continue
        return title
    return None


def _extract_cover_title(page_artifacts) -> str | None:
    """Extract a likely cover title from the first page."""
    if not page_artifacts:
        return None
    prominent_lines = [
        " ".join(line_text.split()).strip()
        for line_text, bbox in page_artifacts[0].lines
        if bbox[1] < 260 and len(" ".join(line_text.split()).strip()) <= 20
    ]
    if len(prominent_lines) >= 2:
        combined = "".join(prominent_lines[:2]).strip()
        if len(combined) >= 8:
            return combined
    candidates = layout_heading_candidates(page_artifacts[0], limit=3)
    if candidates:
        return str(candidates[0]["title"]).strip()
    for line_text, _bbox in page_artifacts[0].lines:
        cleaned = " ".join(line_text.split()).strip()
        if len(cleaned) >= 4:
            return cleaned
    return None


def _effective_outline_level(entry, page_artifacts) -> int:
    """Adjust TOC indentation levels using the actual page heading scale."""
    level = max(1, int(entry.level_hint))
    if entry.physical_page is None or entry.physical_page > len(page_artifacts):
        return level
    page = page_artifacts[int(entry.physical_page) - 1]
    dominant_title, dominant_size = _page_dominant_heading(page)
    if (
        dominant_title
        and dominant_size >= 32.0
        and (
            normalize_text(entry.title) in normalize_text(dominant_title)
            or normalize_text(dominant_title) in normalize_text(entry.title)
        )
    ):
        return 1
    return level


def _page_dominant_heading(page_artifact) -> tuple[str | None, float]:
    """Return the dominant heading text and size for one page."""
    candidates = layout_heading_candidates(page_artifact, limit=5)
    if not candidates:
        return None, 0.0
    max_size = max(float(item["size"]) for item in candidates)
    dominant_parts = [
        str(item["title"]).strip()
        for item in candidates
        if float(item["size"]) >= max_size * 0.95
    ]
    return "".join(dominant_parts).strip() or None, max_size


def _convert_pageindex_structure(
    nodes: list[dict[str, Any]],
    *,
    include_summary: bool = False,
) -> list[dict[str, Any]]:
    """Convert PageIndex's PDF structure into the target JSON schema."""
    converted: list[dict[str, Any]] = []
    for node in nodes:
        item = {
            "title": _sanitize_output_title(str(node.get("title") or "")),
            "node_id": node.get("node_id"),
            "page_index": node.get("start_index"),
            "text": node.get("text", ""),
        }
        if include_summary and node.get("summary"):
            item["summary"] = node.get("summary")
        children = _convert_pageindex_structure(
            node.get("nodes") or [],
            include_summary=include_summary,
        )
        if children:
            item["nodes"] = children
        converted.append(item)
    return _reshape_root_nodes(converted)


def _flatten_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a tree into a list while preserving title, page, and text."""
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        flattened.append(
            {
                "title": node.get("title"),
                "page_index": node.get("page_index"),
                "text": node.get("text", ""),
            }
        )
        flattened.extend(_flatten_tree(node.get("nodes") or []))
    return flattened


def _save_json(path: Path, payload: Any) -> None:
    """Write a JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _debug_stage(debug_recorder: DebugRecorder | None, stage_name: str):
    """Return a no-op or structured debug stage context manager."""
    if debug_recorder is None:
        return _NoOpContextManager()
    return debug_recorder.stage(stage_name)


def _stable_doc_id(pdf_path: Path) -> str:
    """Build a stable document id from PDF content."""
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, digest))


def _sanitize_page_token_list(page_list: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Clean raw PageIndex page text before any downstream LLM prompts consume it."""
    return [
        (_sanitize_llm_text(page_text), int(token_count))
        for page_text, token_count in page_list
    ]


def _sanitize_llm_text(text: str) -> str:
    """Remove text bytes that break UTF-8 request serialization."""
    normalized = str(text or "")
    return normalized.replace("\x00", "").encode("utf-8", errors="ignore").decode(
        "utf-8",
        errors="ignore",
    )


def _ratio(numerator: int, denominator: int) -> float:
    """Return a safe ratio."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _sanitize_output_title(title: str) -> str:
    """Apply lightweight title cleanup for final tree output."""
    cleaned = " ".join(title.split())
    cleaned = _collapse_cjk_spaces(cleaned)
    if cleaned.startswith("前言："):
        return cleaned.removeprefix("前言：").strip()
    if cleaned.startswith("前言:"):
        return cleaned.removeprefix("前言:").strip()
    return cleaned


def _collapse_cjk_spaces(text: str) -> str:
    """Remove spaces inside titles when both neighbors are CJK, digits, or punctuation."""
    collapsed: list[str] = []
    for index, char in enumerate(text):
        if char != " ":
            collapsed.append(char)
            continue
        prev_char = text[index - 1] if index > 0 else ""
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if _is_cjkish(prev_char) and _is_cjkish(next_char):
            continue
        collapsed.append(char)
    return "".join(collapsed).strip()


def _is_cjkish(char: str) -> bool:
    """Return whether a character belongs to a CJK-ish title token set."""
    if not char:
        return False
    return "\u4e00" <= char <= "\u9fff" or char.isdigit() or char in {"年", "月", "日", "！", "：", ":", "（", "）", "(", ")", "·"}


def _reshape_root_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote main sections out of the cover root while keeping TOC and preface nested."""
    if not nodes:
        return nodes
    first_root = nodes[0]
    children = first_root.get("nodes") or []
    if first_root.get("page_index") != 1 or len(children) < 3:
        return nodes
    if children[0].get("title") != "目录":
        return nodes
    first_root["nodes"] = children[:2]
    return [first_root, *children[2:], *nodes[1:]]


def _resolved_entry_title(entry, page_artifacts) -> str:
    """Prefer the page-visible heading text when it clearly matches the TOC title."""
    if entry.physical_page is None or entry.physical_page > len(page_artifacts):
        return entry.title
    dominant_title, dominant_size = _page_dominant_heading(page_artifacts[int(entry.physical_page) - 1])
    if dominant_title and dominant_size >= 22.0 and _titles_share_prefix(entry.title, dominant_title):
        return dominant_title
    return entry.title


def _titles_share_prefix(left_title: str, right_title: str) -> bool:
    """Return whether two titles likely refer to the same visible heading."""
    left = normalize_text(left_title)
    right = normalize_text(right_title)
    shared = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        shared += 1
    return shared >= 4


class _NullLogger:
    """A tiny logger compatible with the PageIndex call sites."""

    def info(self, _message: Any) -> None:
        """Ignore info logs."""
        return None

    def error(self, _message: Any) -> None:
        """Ignore error logs."""
        return None


class _DebugLogger:
    """Bridge PageIndex logger calls into DemoIndex debug events."""

    def __init__(self, debug_recorder: DebugRecorder) -> None:
        self._debug_recorder = debug_recorder

    def info(self, message: Any) -> None:
        """Record an info-level PageIndex log line."""
        self._debug_recorder.log_event("pageindex_log", level="info", message=str(message))

    def error(self, message: Any) -> None:
        """Record an error-level PageIndex log line."""
        self._debug_recorder.log_event("pageindex_log", level="error", message=str(message))


class _NoOpContextManager:
    """Provide a no-op context manager for disabled debug logging."""

    def __enter__(self) -> None:
        """Enter the no-op context."""
        return None

    def __exit__(self, _exc_type, _exc, _tb) -> bool:
        """Exit the no-op context without suppressing exceptions."""
        return False
