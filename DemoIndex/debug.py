"""Structured debug logging for DemoIndex runs."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterator


class DebugRecorder:
    """Write structured debug events and a run summary to disk."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.base_dir / "debug.log.jsonl"
        self.summary_path = self.base_dir / "run_summary.json"
        self._lock = Lock()
        self._run_metadata: dict[str, Any] = {}
        self._stage_records: list[dict[str, Any]] = []
        self._llm_usage_totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._llm_call_counts = {
            "chat": 0,
            "embedding": 0,
            "success": 0,
            "error": 0,
        }

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def set_run_metadata(self, **metadata: Any) -> None:
        """Store top-level run metadata and emit it to the debug log."""
        with self._lock:
            self._run_metadata.update(metadata)
        self.log_event("run_metadata", **metadata)

    def log_event(self, event_type: str, **payload: Any) -> None:
        """Append one structured event to the debug log."""
        record = {
            "timestamp": self._utc_now_iso(),
            "event_type": event_type,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def log_llm_call(
        self,
        *,
        api_kind: str,
        status: str,
        requested_model: str | None,
        actual_model: str | None,
        duration_ms: int,
        attempt: int,
        usage: dict[str, int] | None = None,
        prompt_char_count: int | None = None,
        response_char_count: int | None = None,
        finish_reason: str | None = None,
        input_count: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record one chat or embedding API call."""
        self.log_event(
            "llm_api_call",
            api_kind=api_kind,
            status=status,
            requested_model=requested_model,
            actual_model=actual_model,
            duration_ms=duration_ms,
            attempt=attempt,
            usage=usage,
            prompt_char_count=prompt_char_count,
            response_char_count=response_char_count,
            finish_reason=finish_reason,
            input_count=input_count,
            error_type=error_type,
            error_message=error_message,
        )
        with self._lock:
            self._llm_call_counts[api_kind] = self._llm_call_counts.get(api_kind, 0) + 1
            self._llm_call_counts[status] = self._llm_call_counts.get(status, 0) + 1
            if usage:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    self._llm_usage_totals[key] += int(usage.get(key) or 0)

    @contextmanager
    def stage(self, stage_name: str, **metadata: Any) -> Iterator[None]:
        """Measure one pipeline stage and emit start/end events."""
        started_at = self._utc_now_iso()
        start = time.perf_counter()
        self.log_event("stage_start", stage=stage_name, started_at=started_at, **metadata)
        try:
            yield
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            record = {
                "stage": stage_name,
                "status": "error",
                "started_at": started_at,
                "duration_ms": duration_ms,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                **metadata,
            }
            with self._lock:
                self._stage_records.append(record)
            self.log_event("stage_end", **record)
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        record = {
            "stage": stage_name,
            "status": "success",
            "started_at": started_at,
            "duration_ms": duration_ms,
            **metadata,
        }
        with self._lock:
            self._stage_records.append(record)
        self.log_event("stage_end", **record)

    def write_summary(self, **extra: Any) -> None:
        """Write an aggregated run summary alongside the debug log."""
        with self._lock:
            payload = {
                "run_metadata": dict(self._run_metadata),
                "stage_records": list(self._stage_records),
                "llm_usage_totals": dict(self._llm_usage_totals),
                "llm_call_counts": dict(self._llm_call_counts),
                **extra,
            }
        self.summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
