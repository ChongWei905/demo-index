"""Qwen-compatible chat helpers for PageIndex-style prompts."""

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
        self._semaphore = asyncio.Semaphore(max_concurrency)

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
                try:
                    response = self._client.chat.completions.create(
                        model=candidate,
                        messages=messages,
                        temperature=0,
                    )
                    content = response.choices[0].message.content or ""
                    finish_reason = self._normalize_finish_reason(response.choices[0].finish_reason)
                    if return_finish_reason:
                        return content, finish_reason
                    return content
                except Exception as exc:  # noqa: PERF203
                    last_error = exc
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
        async with self._semaphore:
            for candidate in model_candidates:
                for attempt in range(1, self.max_retries + 1):
                    try:
                        response = await self._async_client.chat.completions.create(
                            model=candidate,
                            messages=messages,
                            temperature=0,
                        )
                        return response.choices[0].message.content or ""
                    except Exception as exc:  # noqa: PERF203
                        last_error = exc
                        print(f"[qwen-async-retry] model={candidate} attempt={attempt} error={exc}", flush=True)
                        if attempt < self.max_retries:
                            await asyncio.sleep(min(8.0, 1.5 * attempt))
                            continue
                        break
        if last_error is None:
            raise RuntimeError("Asynchronous completion failed without a captured exception.")
        raise last_error
