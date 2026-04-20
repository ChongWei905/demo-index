"""Unit tests for PostgreSQL-facing tree and chunk sanitization helpers."""

from __future__ import annotations

import unittest

from DemoIndex.global_index import build_global_chunk_records
from DemoIndex.postgres_store import flatten_tree_sections


class _FakeEmbeddingClient:
    """Return deterministic embeddings for chunk-record unit tests."""

    dimensions = 4

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed-size vector for each input text."""
        return [[float(index + 1)] * self.dimensions for index, _text in enumerate(texts)]


class PostgresStoreTests(unittest.TestCase):
    """Cover text sanitization before PostgreSQL persistence."""

    def test_flatten_tree_sections_strips_nul_bytes_from_titles_summaries_and_text(self) -> None:
        """Flattening should normalize text fields so PostgreSQL never sees NUL bytes."""
        flattened = flatten_tree_sections(
            {
                "doc_id": "doc\x00-1",
                "result": [
                    {
                        "node_id": "n\x001",
                        "title": "章节\x00A",
                        "summary": "概述\x00文本",
                        "page_index": 3,
                        "text": "正文\x00内容",
                    }
                ],
            }
        )

        self.assertEqual(flattened[0].doc_id, "doc-1")
        self.assertEqual(flattened[0].node_id, "n1")
        self.assertEqual(flattened[0].title, "章节A")
        self.assertEqual(flattened[0].summary, "概述文本")
        self.assertEqual(flattened[0].text, "正文内容")
        self.assertEqual(flattened[0].title_path, "章节A")

    def test_build_global_chunk_records_strips_nul_bytes_from_chunk_payloads(self) -> None:
        """Chunk construction should keep stored text and search text free of NUL bytes."""
        records, report = build_global_chunk_records(
            {
                "doc_id": "doc-1",
                "result": [
                    {
                        "node_id": "n-1",
                        "title": "章节\x00A",
                        "summary": "概述",
                        "page_index": 3,
                        "text": "章节\x00A\n\n正文\x00内容",
                    }
                ],
            },
            count_tokens=lambda text, model=None: max(1, len(str(text))),
            embedding_client=_FakeEmbeddingClient(),
            embedding_model="fake-embedding-model",
        )

        self.assertEqual(report["chunk_count"], 1)
        self.assertNotIn("\x00", records[0].title)
        self.assertNotIn("\x00", records[0].title_path)
        self.assertNotIn("\x00", records[0].chunk_text)
        self.assertNotIn("\x00", records[0].search_text)


if __name__ == "__main__":
    unittest.main()
