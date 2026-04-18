"""Unit tests for DemoIndex env resolution and provider-aware clients."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import DemoIndex.pipeline  # noqa: F401
from DemoIndex import build_pageindex_tree, retrieve_candidates
from DemoIndex import env as demo_env
from DemoIndex.llm import DashScopeEmbeddingClient, QwenChatClient
from DemoIndex.postgres_store import resolve_database_url


def _load_dotenv_for_test(path: str | os.PathLike[str], override: bool = False) -> bool:
    """Populate `os.environ` from one test `.env` file without relying on python-dotenv internals."""
    env_path = Path(path)
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if override or key not in os.environ:
            os.environ[key] = value
    return True


class _FakeEmbeddingsAPI:
    """Capture embedding create payloads and return deterministic vectors."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        """Record the request payload and return one fake embedding response."""
        self.calls.append(kwargs)
        dimension = int(kwargs.get("dimensions") or 1536)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index + 1)] * dimension)
                for index, _text in enumerate(kwargs["input"])
            ],
            model=kwargs["model"],
            usage=None,
        )


class _FakeOpenAI:
    """Minimal fake OpenAI client for embedding tests."""

    instances: list["_FakeOpenAI"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.embeddings = _FakeEmbeddingsAPI()
        _FakeOpenAI.instances.append(self)


class _FakeChatCompletionsAPI:
    """Capture sync chat completion payloads and return one deterministic response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        """Record the request payload and return one fake chat completion response."""
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"answer":"yes"}'),
                    finish_reason="stop",
                )
            ],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4, total_tokens=16),
        )


class _FakeAsyncChatCompletionsAPI:
    """Capture async chat completion payloads and return one deterministic response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        """Record the async request payload and return one fake chat completion response."""
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"answer":"yes"}'),
                    finish_reason="stop",
                )
            ],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4, total_tokens=16),
        )


class _FakeChatOpenAI:
    """Minimal fake sync OpenAI client for chat tests."""

    instances: list["_FakeChatOpenAI"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=_FakeChatCompletionsAPI())
        _FakeChatOpenAI.instances.append(self)


class _FakeAsyncOpenAI:
    """Minimal fake async OpenAI client for chat tests."""

    instances: list["_FakeAsyncOpenAI"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=_FakeAsyncChatCompletionsAPI())
        _FakeAsyncOpenAI.instances.append(self)


class EnvAndLLMTests(unittest.TestCase):
    """Cover env loading, precedence, and provider-specific embedding payloads."""

    def test_get_demoindex_config_loads_demoindex_dotenv(self) -> None:
        """Config loading should read DemoIndex/.env from the configured path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DEMOINDEX_DATABASE_URL=postgresql://env-only",
                        "DEMOINDEX_LLM_API_KEY=env-llm-key",
                        "DEMOINDEX_EMBEDDING_API_KEY=env-embedding-key",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", env_path),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict("os.environ", {}, clear=True),
            ):
                config = demo_env.get_demoindex_config()
        self.assertEqual(config.database_url, "postgresql://env-only")
        self.assertEqual(config.llm.api_key, "env-llm-key")
        self.assertEqual(config.embedding.api_key, "env-embedding-key")

    def test_stage3_relation_priors_json_is_parsed_from_env(self) -> None:
        """JSON env values should become normalized float mappings."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                'DEMOINDEX_STAGE3_RELATION_PRIORS_JSON={"anchor":5,"sibling":1.75}',
                encoding="utf-8",
            )
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", env_path),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict("os.environ", {}, clear=True),
            ):
                config = demo_env.get_demoindex_config()
        self.assertEqual(config.retrieval.stage3_relation_priors["anchor"], 5.0)
        self.assertEqual(config.retrieval.stage3_relation_priors["sibling"], 1.75)

    def test_retrieve_candidates_uses_env_defaults_and_explicit_args_win(self) -> None:
        """Retrieval entrypoints should resolve env defaults only when args are omitted."""
        sentinel = object()
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DEMOINDEX_RETRIEVAL_TOP_K_DENSE=7",
                        "DEMOINDEX_RETRIEVAL_PARSE_MODEL=env-parse-model",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", env_path),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict("os.environ", {}, clear=True),
                patch("DemoIndex.retrieval._retrieve_candidates_internal", return_value=sentinel) as mock_internal,
            ):
                result = retrieve_candidates("query")
                self.assertIs(result, sentinel)
                self.assertEqual(mock_internal.call_args.kwargs["top_k_dense"], 7)
                self.assertEqual(mock_internal.call_args.kwargs["parse_model"], "env-parse-model")

                retrieve_candidates("query", top_k_dense=11, parse_model="explicit-parse-model")
                self.assertEqual(mock_internal.call_args.kwargs["top_k_dense"], 11)
                self.assertEqual(
                    mock_internal.call_args.kwargs["parse_model"],
                    "explicit-parse-model",
                )

    def test_build_pageindex_tree_uses_env_defaults_and_explicit_args_win(self) -> None:
        """Build entrypoints should resolve env defaults only when args are omitted."""
        expected_payload = {"doc_id": "doc-1", "status": "completed", "retrieval_ready": False, "result": []}
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            markdown_path = Path(tmp_dir) / "demo.md"
            markdown_path.write_text("# Demo\n", encoding="utf-8")
            env_path.write_text(
                "\n".join(
                    [
                        "DEMOINDEX_BUILD_MODEL=env-build-model",
                        "DEMOINDEX_BUILD_MARKDOWN_LAYOUT=page_per_page",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", env_path),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict("os.environ", {}, clear=True),
                patch("DemoIndex.pipeline._build_markdown_output", return_value=expected_payload) as mock_builder,
            ):
                build_pageindex_tree(input_path=str(markdown_path))
                self.assertEqual(mock_builder.call_args.kwargs["model"], "env-build-model")
                self.assertEqual(mock_builder.call_args.kwargs["markdown_layout"], "page_per_page")

                build_pageindex_tree(
                    input_path=str(markdown_path),
                    model="explicit-build-model",
                    markdown_layout="h1_forest",
                )
                self.assertEqual(mock_builder.call_args.kwargs["model"], "explicit-build-model")
                self.assertEqual(mock_builder.call_args.kwargs["markdown_layout"], "h1_forest")

    def test_resolve_database_url_reports_new_env_name(self) -> None:
        """Missing database config should mention the new env variable name."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", Path(tmp_dir) / ".env"),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict("os.environ", {}, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "DEMOINDEX_DATABASE_URL"):
                    resolve_database_url()

    def test_dashscope_embedding_requests_include_text_type_and_dimensions(self) -> None:
        """DashScope embedding requests should include text_type and default dimensions."""
        _FakeOpenAI.instances.clear()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", Path(tmp_dir) / ".env"),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict(
                    "os.environ",
                    {
                        "DEMOINDEX_EMBEDDING_API_PROVIDER": "dashscope",
                        "DEMOINDEX_EMBEDDING_API_KEY": "dashscope-key",
                    },
                    clear=True,
                ),
                patch("DemoIndex.llm.OpenAI", _FakeOpenAI),
            ):
                client = DashScopeEmbeddingClient(model_name="dashscope/text-embedding-v4")
                vectors = client.embed_queries(["hello"])
        request = _FakeOpenAI.instances[-1].embeddings.calls[-1]
        self.assertEqual(len(vectors), 1)
        self.assertEqual(request["extra_body"], {"text_type": "query"})
        self.assertEqual(request["dimensions"], 1024)

    def test_openai_embedding_requests_skip_dashscope_only_fields(self) -> None:
        """OpenAI embedding requests should omit DashScope-only fields when dimensions are unset."""
        _FakeOpenAI.instances.clear()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", Path(tmp_dir) / ".env"),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict(
                    "os.environ",
                    {
                        "DEMOINDEX_EMBEDDING_API_PROVIDER": "openai",
                        "DEMOINDEX_EMBEDDING_API_KEY": "openai-key",
                    },
                    clear=True,
                ),
                patch("DemoIndex.llm.OpenAI", _FakeOpenAI),
            ):
                client = DashScopeEmbeddingClient(model_name="text-embedding-3-large")
                vectors = client.embed_documents(["hello"])
        request = _FakeOpenAI.instances[-1].embeddings.calls[-1]
        self.assertEqual(len(vectors), 1)
        self.assertNotIn("extra_body", request)
        self.assertNotIn("dimensions", request)

    def test_dashscope_chat_can_disable_thinking_and_strip_prompt_field(self) -> None:
        """DashScope chat requests should disable thinking mode and remove prompt thinking fields."""
        _FakeChatOpenAI.instances.clear()
        _FakeAsyncOpenAI.instances.clear()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", Path(tmp_dir) / ".env"),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict(
                    "os.environ",
                    {
                        "DEMOINDEX_LLM_API_PROVIDER": "dashscope",
                        "DEMOINDEX_LLM_API_KEY": "dashscope-key",
                    },
                    clear=True,
                ),
                patch("DemoIndex.llm.OpenAI", _FakeChatOpenAI),
                patch("DemoIndex.llm.AsyncOpenAI", _FakeAsyncOpenAI),
            ):
                client = QwenChatClient(enable_thinking=False, strip_thinking_field=True)
                response = client.completion(
                    model="dashscope/qwen3.6-plus",
                    prompt='Reply format:\n{\n    "thinking": <why>\n    "answer": "yes"\n}',
                )
        request = _FakeChatOpenAI.instances[-1].chat.completions.calls[-1]
        self.assertEqual(response, '{"answer":"yes"}')
        self.assertEqual(request["extra_body"], {"enable_thinking": False})
        self.assertNotIn('"thinking"', request["messages"][-1]["content"])
        self.assertIn("Do not include any thinking field", request["messages"][-1]["content"])

    def test_dashscope_async_chat_can_disable_thinking_and_strip_prompt_field(self) -> None:
        """Async DashScope chat requests should use the same non-thinking request shaping."""
        _FakeChatOpenAI.instances.clear()
        _FakeAsyncOpenAI.instances.clear()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(demo_env, "DEMOINDEX_ENV_PATH", Path(tmp_dir) / ".env"),
                patch.object(demo_env, "load_dotenv", side_effect=_load_dotenv_for_test),
                patch.dict(
                    "os.environ",
                    {
                        "DEMOINDEX_LLM_API_PROVIDER": "dashscope",
                        "DEMOINDEX_LLM_API_KEY": "dashscope-key",
                    },
                    clear=True,
                ),
                patch("DemoIndex.llm.OpenAI", _FakeChatOpenAI),
                patch("DemoIndex.llm.AsyncOpenAI", _FakeAsyncOpenAI),
            ):
                client = QwenChatClient(enable_thinking=False, strip_thinking_field=True)
                response = asyncio.run(
                    client.acompletion(
                        model="dashscope/qwen3.6-plus",
                        prompt='Reply format:\n{\n    "thinking": <why>\n    "answer": "yes"\n}',
                    )
                )
        request = _FakeAsyncOpenAI.instances[-1].chat.completions.calls[-1]
        self.assertEqual(response, '{"answer":"yes"}')
        self.assertEqual(request["extra_body"], {"enable_thinking": False})
        self.assertNotIn('"thinking"', request["messages"][-1]["content"])
        self.assertIn("Do not include any thinking field", request["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
