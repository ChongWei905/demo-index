"""LLM helpers for DemoIndex."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .models import OutlineEntry, PageArtifact, PageTranscription


class DashScopeVisionClient:
    """A small OpenAI-compatible client wrapper for DashScope multimodal calls."""

    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_model: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout_seconds,
        )
        self.model = self._normalize_model_name(model)
        self.fallback_model = self._normalize_model_name(fallback_model) if fallback_model else None
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @staticmethod
    def _encode_image(image_path: Path) -> str:
        """Return a base64 data URL for one image."""
        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """Normalize provider-qualified model names for DashScope's API."""
        if not model_name:
            return model_name
        return model_name.split("/", 1)[-1]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Parse a JSON object from a model response."""
        candidate = text.strip()
        if "```json" in candidate:
            start = candidate.find("```json") + len("```json")
            end = candidate.rfind("```")
            candidate = candidate[start:end].strip()
        elif "```" in candidate:
            start = candidate.find("```") + len("```")
            end = candidate.rfind("```")
            candidate = candidate[start:end].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            for left in ("{", "["):
                start = candidate.find(left)
                if start == -1:
                    continue
                for right in ("}", "]"):
                    end = candidate.rfind(right)
                    if end == -1 or end <= start:
                        continue
                    snippet = candidate[start : end + 1]
                    try:
                        return json.loads(snippet)
                    except json.JSONDecodeError:
                        continue
            raise

    @staticmethod
    def _build_prompt(
        *,
        page: PageArtifact,
        toc_page_number: int | None,
        outline_entries: list[OutlineEntry],
        layout_candidates: list[dict],
    ) -> str:
        """Build the page transcription prompt."""
        page_role = "content"
        if toc_page_number and page.page_number < toc_page_number:
            page_role = "cover"
        elif toc_page_number and page.page_number == toc_page_number:
            page_role = "toc"

        prompt_payload = {
            "page_number": page.page_number,
            "page_role_hint": page_role,
            "visual_placeholders": [region.placeholder_name for region in page.visual_regions],
            "layout_heading_candidates": layout_candidates,
            "toc_context": [
                {
                    "title": entry.title,
                    "printed_page": entry.printed_page,
                    "physical_page": entry.physical_page,
                    "level_hint": entry.level_hint,
                }
                for entry in outline_entries
            ],
            "ocr_text": page.plain_text,
        }
        instructions = """
You are converting one PDF page into canonical Markdown for a document-tree pipeline.

Return strict JSON with this schema:
{
  "page_markdown": "<markdown>",
  "headings": [
    {
      "title": "<heading text>",
      "level_hint": <1-4>,
      "role": "cover|toc|section|subsection|subsubsection|body"
    }
  ]
}

Rules:
- Keep only semantic headings that should become document-tree nodes.
- Do not turn running headers, page numbers, captions, decorative part numbers, or inline bold labels into headings.
- If this is a cover page and a TOC appears later, emit a single document root heading using `#`.
- If this is the TOC page, emit `## 目录` and preserve the TOC content as readable markdown, but do not invent extra tree headings from every TOC line.
- Preserve tabular layouts as Markdown tables when practical.
- Insert visual placeholders on their own paragraphs using only the provided placeholder names and in top-to-bottom order.
- Keep the original language and wording; only fix obvious OCR spacing or punctuation noise.
- Keep reading order natural and concise; avoid hallucinating content that is not visible on the page.
"""
        return instructions + "\n\nContext JSON:\n" + json.dumps(
            prompt_payload, ensure_ascii=False, indent=2
        )

    def transcribe_page(
        self,
        *,
        page: PageArtifact,
        toc_page_number: int | None,
        outline_entries: list[OutlineEntry],
        layout_candidates: list[dict],
    ) -> PageTranscription:
        """Transcribe one page into markdown with fallback model support."""
        prompt = self._build_prompt(
            page=page,
            toc_page_number=toc_page_number,
            outline_entries=outline_entries,
            layout_candidates=layout_candidates,
        )
        data_url = self._encode_image(page.page_image_path)
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)

        last_error: Exception | None = None
        for model_name in models:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = self._client.chat.completions.create(
                        model=model_name,
                        temperature=0,
                        response_format={"type": "json_object"},
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            }
                        ],
                    )
                    content = response.choices[0].message.content or "{}"
                    payload = self._extract_json(content)
                    page_markdown = str(payload.get("page_markdown") or "").strip()
                    headings = payload.get("headings") or []
                    if not page_markdown:
                        raise ValueError("Model returned an empty page_markdown.")
                    if not isinstance(headings, list):
                        raise ValueError("Model returned invalid headings.")
                    return PageTranscription(
                        page_number=page.page_number,
                        page_markdown=page_markdown,
                        headings=headings,
                        model=model_name,
                    )
                except Exception as exc:  # noqa: PERF203
                    last_error = exc
                    if attempt < self.max_retries:
                        time.sleep(1.5 * attempt)
                        continue
                    break
        if last_error is None:
            raise RuntimeError("Page transcription failed without a captured exception.")
        raise RuntimeError(
            f"Failed to transcribe page {page.page_number} with models {models}: {last_error}"
        ) from last_error
