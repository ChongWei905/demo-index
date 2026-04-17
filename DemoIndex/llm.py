"""DashScope OpenAI-compatible helpers for DemoIndex."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from openai import AsyncOpenAI, OpenAI


class QwenChatClient:
    """A small DashScope OpenAI-compatible wrapper for PageIndex prompt calls."""

    def __init__(
        self,
        api_key: str | None = None,
        primary_model: str | None = None,
        fallback_model: str | None = None,
        timeout_seconds: float = 180.0,
        max_retries: int = 4,
        max_concurrency: int = 4,
        debug_recorder: Any | None = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise RuntimeError("Missing DashScope API key.")
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout_seconds,
        )
        self._async_client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout_seconds,
        )
        self.primary_model = self._normalize_model_name(primary_model)
        self.fallback_model = self._normalize_model_name(fallback_model)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_concurrency = max_concurrency
        self.debug_recorder = debug_recorder
        self._semaphores: dict[int, asyncio.Semaphore] = {}

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """Normalize provider-qualified model names for DashScope's API."""
        if not model_name:
            return model_name
        return model_name.split("/", 1)[-1]

    @staticmethod
    def _normalize_finish_reason(finish_reason: str | None) -> str:
        """Normalize finish reasons to the format PageIndex expects."""
        if finish_reason == "length":
            return "max_output_reached"
        if finish_reason in {"stop", None}:
            return "finished"
        return str(finish_reason)

    @staticmethod
    def _build_messages(prompt: str, chat_history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Build the chat completion message list."""
        if chat_history:
            return [*chat_history, {"role": "user", "content": prompt}]
        return [{"role": "user", "content": prompt}]

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int] | None:
        """Extract token usage from one OpenAI-compatible response when present."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    @staticmethod
    def _message_char_count(messages: list[dict[str, Any]]) -> int:
        """Return the total character count across completion messages."""
        total = 0
        for message in messages:
            total += len(str(message.get("content") or ""))
        return total

    def _log_chat_call(
        self,
        *,
        status: str,
        requested_model: str | None,
        response: Any | None,
        duration_ms: int,
        attempt: int,
        messages: list[dict[str, Any]],
        response_text: str | None = None,
        finish_reason: str | None = None,
        error: Exception | None = None,
    ) -> None:
        """Emit one structured debug record for a chat completion call."""
        if self.debug_recorder is None:
            return
        self.debug_recorder.log_llm_call(
            api_kind="chat",
            status=status,
            requested_model=requested_model,
            actual_model=getattr(response, "model", None) if response is not None else requested_model,
            duration_ms=duration_ms,
            attempt=attempt,
            usage=self._extract_usage(response) if response is not None else None,
            prompt_char_count=self._message_char_count(messages),
            response_char_count=len(response_text or ""),
            finish_reason=finish_reason,
            error_type=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Return an async semaphore scoped to the current event loop."""
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(id(loop))
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.max_concurrency)
            self._semaphores[id(loop)] = semaphore
        return semaphore

    def completion(
        self,
        model: str | None,
        prompt: str,
        chat_history: list[dict[str, Any]] | None = None,
        return_finish_reason: bool = False,
    ) -> str | tuple[str, str]:
        """Run one synchronous chat completion with retries."""
        requested_model = self._normalize_model_name(model) or self.primary_model
        model_candidates = [requested_model]
        if self.fallback_model and self.fallback_model not in model_candidates:
            model_candidates.append(self.fallback_model)
        messages = self._build_messages(prompt, chat_history=chat_history)
        last_error: Exception | None = None
        for candidate in model_candidates:
            for attempt in range(1, self.max_retries + 1):
                start_time = time.time()
                try:
                    response = self._client.chat.completions.create(
                        model=candidate,
                        messages=messages,
                        temperature=0,
                    )
                    content = response.choices[0].message.content or ""
                    finish_reason = self._normalize_finish_reason(response.choices[0].finish_reason)
                    self._log_chat_call(
                        status="success",
                        requested_model=candidate,
                        response=response,
                        duration_ms=int((time.time() - start_time) * 1000),
                        attempt=attempt,
                        messages=messages,
                        response_text=content,
                        finish_reason=finish_reason,
                    )
                    if return_finish_reason:
                        return content, finish_reason
                    return content
                except Exception as exc:  # noqa: PERF203
                    last_error = exc
                    self._log_chat_call(
                        status="error",
                        requested_model=candidate,
                        response=None,
                        duration_ms=int((time.time() - start_time) * 1000),
                        attempt=attempt,
                        messages=messages,
                        error=exc,
                    )
                    print(f"[qwen-sync-retry] model={candidate} attempt={attempt} error={exc}", flush=True)
                    if attempt < self.max_retries:
                        time.sleep(min(8.0, 1.5 * attempt))
                        continue
                    break
        if last_error is None:
            raise RuntimeError("Synchronous completion failed without a captured exception.")
        raise last_error

    async def acompletion(self, model: str | None, prompt: str) -> str:
        """Run one asynchronous chat completion with retries."""
        requested_model = self._normalize_model_name(model) or self.primary_model
        model_candidates = [requested_model]
        if self.fallback_model and self.fallback_model not in model_candidates:
            model_candidates.append(self.fallback_model)
        messages = self._build_messages(prompt)
        last_error: Exception | None = None
        async with self._get_semaphore():
            for candidate in model_candidates:
                for attempt in range(1, self.max_retries + 1):
                    start_time = time.time()
                    try:
                        response = await self._async_client.chat.completions.create(
                            model=candidate,
                            messages=messages,
                            temperature=0,
                        )
                        content = response.choices[0].message.content or ""
                        finish_reason = self._normalize_finish_reason(response.choices[0].finish_reason)
                        self._log_chat_call(
                            status="success",
                            requested_model=candidate,
                            response=response,
                            duration_ms=int((time.time() - start_time) * 1000),
                            attempt=attempt,
                            messages=messages,
                            response_text=content,
                            finish_reason=finish_reason,
                        )
                        return content
                    except Exception as exc:  # noqa: PERF203
                        last_error = exc
                        self._log_chat_call(
                            status="error",
                            requested_model=candidate,
                            response=None,
                            duration_ms=int((time.time() - start_time) * 1000),
                            attempt=attempt,
                            messages=messages,
                            error=exc,
                        )
                        print(f"[qwen-async-retry] model={candidate} attempt={attempt} error={exc}", flush=True)
                        if attempt < self.max_retries:
                            await asyncio.sleep(min(8.0, 1.5 * attempt))
                            continue
                        break
        if last_error is None:
            raise RuntimeError("Asynchronous completion failed without a captured exception.")
        raise last_error


class DashScopeEmbeddingClient:
    """A small DashScope OpenAI-compatible wrapper for embedding calls."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "text-embedding-v4",
        dimensions: int = 1024,
        timeout_seconds: float = 180.0,
        max_retries: int = 4,
        max_batch_size: int = 10,
        debug_recorder: Any | None = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise RuntimeError("Missing DashScope API key.")
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout_seconds,
        )
        self.model_name = self._normalize_model_name(model_name) or "text-embedding-v4"
        self.dimensions = int(dimensions)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_batch_size = min(10, max(1, int(max_batch_size)))
        self.debug_recorder = debug_recorder

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """Normalize provider-qualified model names for DashScope's API."""
        if not model_name:
            return model_name
        return model_name.split("/", 1)[-1]

    @staticmethod
    def _sort_embedding_rows(rows: list[Any]) -> list[Any]:
        """Return embedding rows ordered by their original input index."""
        return sorted(rows, key=lambda row: int(getattr(row, "index", 0)))

    def _log_embedding_call(
        self,
        *,
        status: str,
        duration_ms: int,
        attempt: int,
        texts: list[str],
        response: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        """Emit one structured debug record for an embedding API call."""
        if self.debug_recorder is None:
            return
        usage = QwenChatClient._extract_usage(response) if response is not None else None
        self.debug_recorder.log_llm_call(
            api_kind="embedding",
            status=status,
            requested_model=self.model_name,
            actual_model=getattr(response, "model", None) if response is not None else self.model_name,
            duration_ms=duration_ms,
            attempt=attempt,
            usage=usage,
            prompt_char_count=sum(len(text) for text in texts),
            input_count=len(texts),
            error_type=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document-side texts in batches and validate vector dimensions."""
        return self._embed_texts(texts, text_type="document")

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Embed query-side texts in batches and validate vector dimensions."""
        return self._embed_texts(texts, text_type="query")

    def _embed_texts(self, texts: list[str], *, text_type: str) -> list[list[float]]:
        """Embed texts in batches for the requested text role."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.max_batch_size):
            batch = texts[start : start + self.max_batch_size]
            vectors.extend(self._embed_batch(batch, text_type=text_type))
        return vectors

    def _embed_batch(self, texts: list[str], *, text_type: str) -> list[list[float]]:
        """Embed one batch with retries."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            start_time = time.time()
            try:
                response = self._client.embeddings.create(
                    model=self.model_name,
                    input=texts,
                    dimensions=self.dimensions,
                    encoding_format="float",
                    extra_body={"text_type": text_type},
                )
                vectors: list[list[float]] = []
                for item in self._sort_embedding_rows(list(response.data)):
                    vector = list(item.embedding or [])
                    if len(vector) != self.dimensions:
                        raise RuntimeError(
                            f"Embedding dimension mismatch for {self.model_name}: "
                            f"expected {self.dimensions}, got {len(vector)}."
                        )
                    vectors.append(vector)
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"Embedding batch size mismatch: expected {len(texts)}, got {len(vectors)}."
                    )
                self._log_embedding_call(
                    status="success",
                    duration_ms=int((time.time() - start_time) * 1000),
                    attempt=attempt,
                    texts=texts,
                    response=response,
                )
                return vectors
            except Exception as exc:  # noqa: PERF203
                last_error = exc
                self._log_embedding_call(
                    status="error",
                    duration_ms=int((time.time() - start_time) * 1000),
                    attempt=attempt,
                    texts=texts,
                    error=exc,
                )
                print(
                    f"[dashscope-embedding-retry] model={self.model_name} "
                    f"attempt={attempt} error={exc}",
                    flush=True,
                )
                if attempt < self.max_retries:
                    time.sleep(min(8.0, 1.5 * attempt))
                    continue
                break
        if last_error is None:
            raise RuntimeError("Embedding batch failed without a captured exception.")
        raise last_error
