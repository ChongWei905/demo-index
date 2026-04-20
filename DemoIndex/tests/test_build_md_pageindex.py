"""Unit tests for Markdown PageIndex building and unified build routing."""

from __future__ import annotations

import asyncio
import textwrap
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from DemoIndex.build_md_pageindex import (
    PageIndexOptions,
    build_forest_from_markdown,
    build_forest_page_per_page_with_doc_root,
    compute_line_count,
    normalize_display_title,
    parse_page_comments,
    sync_build_pageindex_payload,
    sync_build_pageindex_payload_from_lines,
)
from DemoIndex.models import OutlineEntry, PageArtifact
from DemoIndex.pdf import assess_outline_confidence, detect_toc_page, extract_outline_entries
from DemoIndex.pipeline import (
    _build_minimal_text_fallback_tree,
    _build_seeded_outline,
    _build_tree_with_pageindex_native,
    _ensure_output_tree_integrity,
    _normalize_output_tree,
    _resolve_pdf_candidate_plan,
    _sanitize_page_token_list,
    _score_output_tree,
    _summarize_output_tree,
    build_pageindex_tree,
)


class MarkdownBuildTests(unittest.TestCase):
    """Cover Markdown builder helpers and unified build input routing."""

    @staticmethod
    def _make_page_artifact(
        page_number: int,
        *,
        plain_text: str = "",
        text_block_count: int = 0,
        lines: list[tuple[str, tuple[float, float, float, float]]] | None = None,
    ) -> PageArtifact:
        """Build a minimal `PageArtifact` for unit tests."""
        return PageArtifact(
            page_number=page_number,
            page_image_path=Path(f"/tmp/page_{page_number:03d}.png"),
            plain_text=plain_text,
            spans=[],
            lines=lines or [],
            page_width=1000.0,
            page_height=1000.0,
            text_block_count=text_block_count,
        )

    def test_compute_line_count(self) -> None:
        """Markdown line counting should follow the standard newline-plus-one rule."""
        self.assertEqual(compute_line_count(""), 1)
        self.assertEqual(compute_line_count("a"), 1)
        self.assertEqual(compute_line_count("a\nb\nc"), 3)

    def test_parse_page_comments_tracks_page_switch_for_following_lines(self) -> None:
        """Page comments should affect subsequent lines without re-tagging the comment line."""
        lines = ["line0", "<!-- page:3 -->", "after break"]
        self.assertEqual(parse_page_comments(lines), [1, 1, 3])

    def test_normalize_display_title_cleans_prefix_and_year_spacing(self) -> None:
        """Display titles should remove the qianyan prefix and collapse year spacing."""
        self.assertEqual(normalize_display_title("前言：移动游戏", 2), "移动游戏")
        self.assertEqual(normalize_display_title("2025 年报告", 1), "2025年报告")

    def test_build_forest_from_markdown_creates_two_h1_roots(self) -> None:
        """The h1_forest layout should split the document into multiple H1-rooted trees."""
        markdown = textwrap.dedent(
            """\
            # Alpha

            intro

            ## Beta

            beta body

            <!-- page:2 -->

            # Gamma

            gamma only
            """
        )
        lines = markdown.strip().split("\n")
        forest = build_forest_from_markdown(lines, parse_page_comments(lines))
        self.assertEqual(len(forest), 2)
        self.assertEqual(forest[0]["title"], "Alpha")
        self.assertEqual(forest[0]["nodes"][0]["title"], "Beta")
        self.assertEqual(forest[1]["title"], "Gamma")

    def test_build_forest_page_per_page_with_doc_root_creates_page_children(self) -> None:
        """The page_per_page layout should create one doc root plus one child per logical page."""
        markdown = textwrap.dedent(
            """\
            <!-- page:1 -->
            # RootTitle

            intro

            <!-- page:2 -->
            ## PageTwoOnly
            body
            """
        ).strip()
        lines = markdown.split("\n")
        forest = build_forest_page_per_page_with_doc_root(lines, parse_page_comments(lines))
        self.assertEqual(len(forest), 1)
        root = forest[0]
        self.assertEqual(root["title"], "RootTitle")
        self.assertEqual(len(root["nodes"]), 2)
        self.assertEqual(root["nodes"][0]["title"], "RootTitle")
        self.assertEqual(root["nodes"][1]["title"], "PageTwoOnly")

    def test_sync_build_pageindex_payload_from_lines_supports_page_per_page_layout(self) -> None:
        """The line-based builder should preserve page-per-page output shape."""
        lines = [
            "<!-- page:1 -->",
            "# OnlyH1",
            "",
            "x",
            "<!-- page:2 -->",
            "## PageTwo",
            "no heading line",
        ]
        payload = sync_build_pageindex_payload_from_lines(
            lines,
            PageIndexOptions(if_add_summary=False, doc_id="00000000-0000-0000-0000-000000000001"),
            llm_factory=None,
            layout="page_per_page",
        )
        self.assertEqual(len(payload["result"]), 1)
        root = payload["result"][0]
        self.assertEqual(root["title"], "OnlyH1")
        self.assertEqual(len(root["nodes"]), 2)
        self.assertEqual(root["nodes"][1]["title"], "PageTwo")

    def test_sync_build_pageindex_payload_summary_off_clears_summaries(self) -> None:
        """Turning summaries off should clear both doc and node summaries."""
        with self.subTest("summary_off"):
            tmp_path = Path(self.id().replace(".", "_") + ".md")
            try:
                tmp_path.write_text("# One\n\nHello\n", encoding="utf-8")
                payload = sync_build_pageindex_payload(
                    tmp_path,
                    PageIndexOptions(if_add_summary=False, doc_id=str(uuid.uuid4())),
                    llm_factory=None,
                )
                self.assertEqual(payload["summary"], "")
                self.assertEqual(payload["result"][0]["summary"], "")
            finally:
                tmp_path.unlink(missing_ok=True)

    def test_sync_build_pageindex_payload_uses_heuristic_summary_without_llm(self) -> None:
        """Markdown payload building should produce summaries without an LLM when enabled."""
        tmp_path = Path(self.id().replace(".", "_") + ".md")
        try:
            tmp_path.write_text("# One\n\nHello world.\n\n## Sub\n\nShort.\n", encoding="utf-8")
            fixed_id = "11111111-1111-1111-1111-111111111111"
            payload = sync_build_pageindex_payload(
                tmp_path,
                PageIndexOptions(if_add_summary=True, summary_char_threshold=600, doc_id=fixed_id),
                llm_factory=None,
            )
            self.assertEqual(payload["doc_id"], fixed_id)
            self.assertTrue(payload["summary"])
            self.assertIn("One", payload["summary"])
            self.assertTrue(payload["result"][0]["summary"])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_build_pageindex_tree_routes_markdown_input_and_auto_layout(self) -> None:
        """The unified build entrypoint should route Markdown files through the Markdown builder."""
        tmp_path = Path(self.id().replace(".", "_") + ".md")
        try:
            tmp_path.write_text(
                textwrap.dedent(
                    """\
                    <!-- page:1 -->
                    # RootTitle

                    intro

                    <!-- page:2 -->
                    ## PageTwo
                    body
                    """
                ),
                encoding="utf-8",
            )
            result = build_pageindex_tree(input_path=str(tmp_path), markdown_layout="auto")
            self.assertTrue(result["doc_id"])
            self.assertGreaterEqual(result["line_count"], 1)
            self.assertEqual(len(result["result"]), 1)
            self.assertEqual(result["result"][0]["title"], "RootTitle")
            self.assertEqual(len(result["result"][0]["nodes"]), 2)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_build_pageindex_tree_routes_pdf_input_to_pdf_builder(self) -> None:
        """The unified build entrypoint should continue to route PDF files to the PDF builder."""
        tmp_path = Path(self.id().replace(".", "_") + ".pdf")
        try:
            tmp_path.write_bytes(b"%PDF-1.4\n")
            expected = {"doc_id": "doc-1", "status": "completed", "retrieval_ready": False, "result": []}
            with patch("DemoIndex.pipeline._build_pdf_output", return_value=expected) as mock_pdf_builder:
                result = build_pageindex_tree(input_path=str(tmp_path))
            self.assertEqual(result, expected)
            mock_pdf_builder.assert_called_once()
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_build_pageindex_tree_rejects_multiple_input_arguments(self) -> None:
        """The unified build entrypoint should reject simultaneous input_path and pdf_path usage."""
        tmp_path = Path(self.id().replace(".", "_") + ".pdf")
        try:
            tmp_path.write_bytes(b"%PDF-1.4\n")
            with self.assertRaisesRegex(ValueError, "Only one of input_path or pdf_path"):
                build_pageindex_tree(input_path=str(tmp_path), pdf_path=str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_detect_toc_page_ignores_sparse_pages_without_toc_signals(self) -> None:
        """Sparse cover-like pages should not be misclassified as TOC pages without TOC markers."""
        pages = [
            self._make_page_artifact(1, plain_text="2024中国移动游戏广告营销报告", text_block_count=2),
            self._make_page_artifact(2, plain_text="", text_block_count=0),
            self._make_page_artifact(3, plain_text="产业现状 01", text_block_count=2),
        ]
        self.assertIsNone(detect_toc_page(pages))

    def test_build_seeded_outline_falls_back_to_heading_candidates_without_toc(self) -> None:
        """PDF builds should seed a tree from page heading candidates when TOC extraction is empty."""
        pages = [
            self._make_page_artifact(1, plain_text="封面", text_block_count=2),
            self._make_page_artifact(4, plain_text="移动游戏市场发展状况", text_block_count=20),
            self._make_page_artifact(5, plain_text="移动游戏用户存量状况", text_block_count=18),
            self._make_page_artifact(6, plain_text="移动游戏用户存量状况", text_block_count=18),
        ]

        def fake_heading_candidates(page: PageArtifact, limit: int = 5) -> list[dict]:
            del limit
            mapping = {
                4: [{"title": "移动游戏市场发展状况", "size": 18.0, "bold": False, "bbox": [0, 0, 1, 1]}],
                5: [{"title": "移动游戏用户存量状况", "size": 18.0, "bold": False, "bbox": [0, 0, 1, 1]}],
                6: [{"title": "移动游戏用户存量状况", "size": 18.0, "bold": False, "bbox": [0, 0, 1, 1]}],
            }
            return mapping.get(page.page_number, [])

        with patch("DemoIndex.pipeline.layout_heading_candidates", side_effect=fake_heading_candidates):
            seeded = _build_seeded_outline(
                page_artifacts=pages,
                outline_entries=[],
                toc_page_number=1,
            )

        self.assertEqual(
            seeded,
            [
                {"structure": "1", "title": "移动游戏市场发展状况", "physical_index": 4},
                {"structure": "2", "title": "移动游戏用户存量状况", "physical_index": 5},
            ],
        )

    def test_assess_outline_confidence_marks_real_toc_as_high(self) -> None:
        """TOC confidence should be high for multi-entry front-matter outlines with valid page mappings."""
        pages = [
            self._make_page_artifact(
                1,
                plain_text="目录 第一章......3 第二章......6 第三章......8",
                text_block_count=4,
                lines=[
                    ("第一章 ...... 3", (0.0, 0.0, 10.0, 10.0)),
                    ("1.1 市场概况 ...... 4", (20.0, 12.0, 10.0, 10.0)),
                    ("第二章 ...... 6", (0.0, 24.0, 10.0, 10.0)),
                    ("第三章 ...... 8", (0.0, 36.0, 10.0, 10.0)),
                    ("附录 ...... 10", (0.0, 48.0, 10.0, 10.0)),
                ],
            ),
            self._make_page_artifact(2, plain_text="封面", text_block_count=1),
            self._make_page_artifact(3, plain_text="第一章", text_block_count=8),
            self._make_page_artifact(4, plain_text="1.1 市场概况", text_block_count=8),
            self._make_page_artifact(5, plain_text="1.2 用户概况", text_block_count=8),
            self._make_page_artifact(6, plain_text="第二章", text_block_count=8),
            self._make_page_artifact(7, plain_text="2.1 竞争格局", text_block_count=8),
            self._make_page_artifact(8, plain_text="第三章", text_block_count=8),
        ]
        outline_entries = [
            OutlineEntry("第一章", 3, 3, 1, 1, (0.0, 0.0, 10.0, 10.0)),
            OutlineEntry("1.1 市场概况", 4, 4, 2, 1, (20.0, 12.0, 10.0, 10.0)),
            OutlineEntry("1.2 用户概况", 5, 5, 2, 1, (20.0, 18.0, 10.0, 10.0)),
            OutlineEntry("第二章", 6, 6, 1, 1, (0.0, 24.0, 10.0, 10.0)),
            OutlineEntry("第三章", 8, 8, 1, 1, (0.0, 36.0, 10.0, 10.0)),
        ]

        assessment = assess_outline_confidence(pages, 1, outline_entries)

        self.assertEqual(assessment["confidence"], "high")
        self.assertGreaterEqual(assessment["mapped_entry_ratio"], 0.6)
        self.assertGreaterEqual(assessment["outline_entry_count"], 5)

    def test_resolve_pdf_candidate_plan_prefers_pageindex_native_without_high_confidence_toc(self) -> None:
        """Auto strategy should prefer PageIndex-native parsing when TOC confidence is not high."""
        low_plan = _resolve_pdf_candidate_plan(
            pdf_strategy="auto",
            toc_assessment={"confidence": "low"},
            outline_entries=[object()],
        )
        none_plan = _resolve_pdf_candidate_plan(
            pdf_strategy="auto",
            toc_assessment={"confidence": "none"},
            outline_entries=[],
        )
        forced_plan = _resolve_pdf_candidate_plan(
            pdf_strategy="toc_seeded",
            toc_assessment={"confidence": "none"},
            outline_entries=[],
        )

        self.assertEqual(low_plan, ["pageindex_native", "toc_seeded", "layout_fallback"])
        self.assertEqual(none_plan, ["pageindex_native", "layout_fallback"])
        self.assertEqual(forced_plan, ["toc_seeded"])

    def test_normalize_output_tree_synthesizes_missing_numbered_parents(self) -> None:
        """Normalization should synthesize missing parents for dotted numbering prefixes."""
        normalized = _normalize_output_tree(
            [
                {"title": "2.1 中国游戏产业企业营销状况", "page_index": 12, "text": ""},
                {"title": "2.1.2 中国游戏企业营销策略状况", "page_index": 13, "text": ""},
            ],
            page_artifacts=[self._make_page_artifact(12), self._make_page_artifact(13)],
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["title"], "2")
        self.assertEqual(normalized[0]["nodes"][0]["title"], "2.1中国游戏产业企业营销状况")
        self.assertEqual(
            normalized[0]["nodes"][0]["nodes"][0]["title"],
            "2.1.2中国游戏企业营销策略状况",
        )

    def test_ensure_output_tree_integrity_backfills_missing_ids_and_summaries(self) -> None:
        """Integrity cleanup should backfill synthetic parent IDs and summaries deterministically."""
        normalized = _normalize_output_tree(
            [
                {"title": "2.1 中国游戏产业企业营销状况", "page_index": 12, "text": ""},
                {"title": "2.1.2 中国游戏企业营销策略状况", "page_index": 13, "text": "正文摘要来源"},
            ],
            page_artifacts=[self._make_page_artifact(12), self._make_page_artifact(13)],
        )

        prepared = _ensure_output_tree_integrity(normalized)

        root = prepared[0]
        child = root["nodes"][0]
        grandchild = child["nodes"][0]
        self.assertTrue(root["node_id"].startswith("synthetic:"))
        self.assertTrue(root["summary"])
        self.assertTrue(child["node_id"])
        self.assertTrue(child["summary"])
        self.assertTrue(grandchild["node_id"])
        self.assertEqual(grandchild["summary"], "正文摘要来源")

    def test_build_minimal_text_fallback_tree_uses_top_lines_when_heading_candidates_are_weak(self) -> None:
        """Minimal fallback should build text-backed nodes from page lines when headings are weak."""
        pages = [
            self._make_page_artifact(
                1,
                plain_text="封面信息",
                text_block_count=1,
                lines=[("封面信息", (0.0, 20.0, 200.0, 40.0))],
            ),
            self._make_page_artifact(
                3,
                plain_text="01 季节性机会",
                text_block_count=2,
                lines=[("01 季节性机会", (0.0, 20.0, 200.0, 40.0))],
            ),
            self._make_page_artifact(
                7,
                plain_text="02 季节性时刻 如何设置你的广告",
                text_block_count=1,
                lines=[("02 季节性时刻 如何设置你的广告", (0.0, 20.0, 400.0, 40.0))],
            ),
        ]

        with patch("DemoIndex.pipeline.layout_heading_candidates", return_value=[]):
            tree = _build_minimal_text_fallback_tree(
                page_artifacts=pages,
                include_summary=True,
            )

        self.assertEqual([node["title"] for node in tree], ["01季节性机会", "02季节性时刻如何设置你的广告"])
        self.assertTrue(all(node["summary"] for node in tree))

    def test_extract_outline_entries_skips_absurd_printed_page_numbers(self) -> None:
        """TOC extraction should ignore trailing year-like numbers that are not real page anchors."""
        pages = [
            self._make_page_artifact(
                1,
                plain_text="目录 7月01 - 8月31 2025 8月10-8月17 17 8月08-8月15 15",
                text_block_count=3,
                lines=[
                    ("目录", (0.0, 0.0, 10.0, 10.0)),
                    ("7月01 - 8月31 2025", (0.0, 20.0, 100.0, 30.0)),
                    ("8月10-8月17 17", (0.0, 40.0, 100.0, 50.0)),
                    ("8月08-8月15 15", (0.0, 60.0, 100.0, 70.0)),
                ],
            ),
        ] + [self._make_page_artifact(index, plain_text=f"第{index}页", text_block_count=1) for index in range(2, 27)]

        _toc_page, outline_entries = extract_outline_entries(pages)

        self.assertEqual(len(outline_entries), 2)
        self.assertEqual([entry.printed_page for entry in outline_entries], [17, 15])

    def test_sanitize_page_token_list_drops_surrogates_and_nul_bytes(self) -> None:
        """Page token sanitization should clean strings before LLM serialization."""
        sanitized = _sanitize_page_token_list([("Alpha\x00\udfd0Beta", 12)])

        self.assertEqual(sanitized, [("AlphaBeta", 12)])

    def test_score_output_tree_prefers_hierarchical_structure_over_flat_roots(self) -> None:
        """Scoring should reward deeper, less root-heavy trees over flat title lists."""
        hierarchical_tree = [
            {
                "title": "01",
                "page_index": 3,
                "text": "",
                "nodes": [
                    {
                        "title": "营销现状",
                        "page_index": 6,
                        "text": "",
                        "nodes": [
                            {"title": "游戏产业整体营销状况", "page_index": 7, "text": ""},
                        ],
                    }
                ],
            }
        ]
        flat_tree = [
            {"title": "营销现状", "page_index": 6, "text": ""},
            {"title": "游戏产业整体营销状况", "page_index": 7, "text": ""},
            {"title": "广告创意投放数量增长，买量更聚焦于少数产品", "page_index": 8, "text": ""},
        ]
        pages = [
            self._make_page_artifact(6, plain_text="营销现状", text_block_count=5),
            self._make_page_artifact(7, plain_text="游戏产业整体营销状况", text_block_count=5),
            self._make_page_artifact(8, plain_text="广告创意投放数量增长，买量更聚焦于少数产品", text_block_count=5),
        ]

        hierarchical_score, _ = _score_output_tree(
            _summarize_output_tree(hierarchical_tree, page_artifacts=pages)
        )
        flat_score, _ = _score_output_tree(
            _summarize_output_tree(flat_tree, page_artifacts=pages)
        )

        self.assertGreater(hierarchical_score, flat_score)

    def test_build_tree_with_pageindex_native_falls_back_to_process_no_toc(self) -> None:
        """Native PageIndex builds should fall back to `process_no_toc` when TOC detection errors out."""

        class _FakePageIndexModule:
            async def tree_parser(self, _page_list, _opt, logger=None):
                del logger
                raise KeyError("toc_detected")

            async def meta_processor(self, _page_list, mode=None, start_index=1, opt=None, logger=None):
                del mode, start_index, opt, logger
                return [{"title": "章节A", "physical_index": 1, "appear_start": "yes"}]

            @staticmethod
            def add_preface_if_needed(data):
                return data

            async def check_title_appearance_in_start_concurrent(self, structure, _page_list, model=None, logger=None):
                del model, logger
                return structure

            async def process_large_node_recursively(self, node, _page_list, opt=None, logger=None):
                del opt, logger
                return node

        class _FakeUtilsModule:
            @staticmethod
            def post_processing(_structure, _page_count):
                return [{"title": "章节A", "start_index": 1, "end_index": 1}]

        tree = asyncio.run(
            _build_tree_with_pageindex_native(
                page_list=[("page 1", 10)],
                pageindex_module=_FakePageIndexModule(),
                utils_module=_FakeUtilsModule(),
                opt=type("Opt", (), {"model": "demo-model"})(),
                debug_recorder=None,
            )
        )

        self.assertEqual(tree, [{"title": "章节A", "start_index": 1, "end_index": 1}])
