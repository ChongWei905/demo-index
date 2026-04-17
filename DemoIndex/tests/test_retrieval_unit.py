"""Unit tests for DemoIndex Stage 1 + Stage 2 retrieval logic."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from DemoIndex.retrieval import (
    QueryUnderstanding,
    RetrievalChunkHit,
    _aggregate_candidates,
    _fuse_chunk_hits,
    parse_query,
)


class RetrievalUnitTests(unittest.TestCase):
    """Cover rule parsing, LLM fallback, fusion, and aggregation helpers."""

    def test_parse_query_rule_based_mixed_language(self) -> None:
        """Rule parsing should extract common mixed Chinese and English fields."""
        result = parse_query("2024 全球手游 CPI 和 retention 趋势", use_llm=False)
        self.assertEqual(result.language, "mixed")
        self.assertEqual(result.intent, "trend")
        self.assertIn("CPI", result.metrics)
        self.assertIn("Retention", result.metrics)
        self.assertIn("Global", result.regions)
        self.assertIn(2024, result.time_scope["years"])
        self.assertFalse(result.llm_enriched)

    def test_parse_query_llm_failure_falls_back(self) -> None:
        """LLM enrichment errors should not break the rule-based parse."""
        with patch("DemoIndex.retrieval._enrich_query_with_llm", side_effect=RuntimeError("boom")):
            result = parse_query("欧美 RPG ARPU 对比", use_llm=True)
        self.assertIn("ARPU", result.metrics)
        self.assertIn("RPG", result.genres)
        self.assertEqual(result.intent, "comparison")
        self.assertFalse(result.llm_enriched)

    def test_rrf_fusion_merges_dense_and_lexical_hits(self) -> None:
        """RRF fusion should preserve both branches and reward overlap."""
        dense_hits = [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "section_id": "s1",
                "node_id": "n1",
                "title": "T1",
                "title_path": "A > T1",
                "page_index": 1,
                "chunk_index": 0,
                "chunk_text": "alpha",
                "dense_rank": 1,
                "dense_score": 0.99,
            },
            {
                "chunk_id": "c2",
                "doc_id": "d2",
                "section_id": "s2",
                "node_id": "n2",
                "title": "T2",
                "title_path": "B > T2",
                "page_index": 2,
                "chunk_index": 0,
                "chunk_text": "beta",
                "dense_rank": 2,
                "dense_score": 0.85,
            },
        ]
        lexical_hits = [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "section_id": "s1",
                "node_id": "n1",
                "title": "T1",
                "title_path": "A > T1",
                "page_index": 1,
                "chunk_index": 0,
                "chunk_text": "alpha",
                "lexical_rank": 1,
                "lexical_score": 0.77,
            },
            {
                "chunk_id": "c3",
                "doc_id": "d3",
                "section_id": "s3",
                "node_id": "n3",
                "title": "T3",
                "title_path": "C > T3",
                "page_index": 3,
                "chunk_index": 0,
                "chunk_text": "gamma",
                "lexical_rank": 2,
                "lexical_score": 0.55,
            },
        ]
        fused = _fuse_chunk_hits(dense_hits=dense_hits, lexical_hits=lexical_hits, top_k_fused_chunks=10)
        self.assertEqual(fused[0].chunk_id, "c1")
        self.assertIsNotNone(fused[0].dense_rank)
        self.assertIsNotNone(fused[0].lexical_rank)
        self.assertEqual({item.chunk_id for item in fused}, {"c1", "c2", "c3"})

    def test_doc_and_section_aggregation_limits(self) -> None:
        """Aggregation should limit sections and supporting chunks per doc."""
        fused_hits = [
            RetrievalChunkHit("c1", "d1", "s1", "n1", "T1", "P > T1", 1, 0, "a", 1, 0.9, 1, 0.8, 0.03),
            RetrievalChunkHit("c2", "d1", "s1", "n1", "T1", "P > T1", 1, 1, "b", 2, 0.8, None, None, 0.02),
            RetrievalChunkHit("c3", "d1", "s2", "n2", "T2", "P > T2", 2, 0, "c", None, None, 2, 0.7, 0.015),
            RetrievalChunkHit("c4", "d2", "s3", "n3", "T3", "Q > T3", 3, 0, "d", 3, 0.7, 3, 0.6, 0.014),
        ]
        metadata = {
            "s1": {"section_id": "s1", "node_id": "n1", "title": "T1", "depth": 1, "summary": "S1"},
            "s2": {"section_id": "s2", "node_id": "n2", "title": "T2", "depth": 2, "summary": "S2"},
            "s3": {"section_id": "s3", "node_id": "n3", "title": "T3", "depth": 1, "summary": "S3"},
        }
        with patch("DemoIndex.retrieval._load_section_metadata", return_value=metadata):
            docs, sections = _aggregate_candidates(
                fused_hits=fused_hits,
                database_url="postgresql://unused",
                top_k_docs=1,
                top_k_sections_per_doc=1,
                top_k_chunks_per_section=1,
            )
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].doc_id, "d1")
        self.assertEqual(len(docs[0].section_candidates), 1)
        self.assertEqual(len(docs[0].section_candidates[0].supporting_chunks), 1)
        self.assertEqual(len(sections), 1)


if __name__ == "__main__":
    unittest.main()
