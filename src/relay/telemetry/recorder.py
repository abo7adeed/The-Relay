"""Thread-safe/async-safe CSV telemetry recorder writing results.csv atomically."""

import csv
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from relay.pipeline.state import PipelineResult

CSV_COLUMNS: List[str] = [
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
    "citation_grade",
    "queue_wait_ms",
    "working_ms",
    "retry_wait_ms",
    "model_first_token_ms",
    "verification_status",
    "peak_queue_length",
]


class TelemetryRecorder:
    """Records per-request execution telemetry and writes results.csv atomically.

    ``overwrite`` is retained for API compatibility, but the previous output is
    no longer deleted in ``__init__``.  The file is replaced only when ``save()``
    succeeds, so a crashed run cannot destroy the last valid evidence file.
    """

    def __init__(self, output_path: str = "results.csv", overwrite: bool = False):
        self.output_path = output_path
        self.overwrite = overwrite
        self.rows: List[Dict[str, Any]] = []
        self._request_ids: set[str] = set()

    def add_result(
        self,
        res: PipelineResult,
        start_time_iso: Optional[str] = None,
        answer_grade_override: Optional[str] = None,
        citation_grade_override: Optional[str] = None,
        peak_queue_length: int = 0,
    ) -> Dict[str, Any]:
        if res.request_id in self._request_ids:
            raise ValueError(
                f"Duplicate request_id {res.request_id!r}; refusing to append a "
                "second telemetry record for the same request."
            )
        self._request_ids.add(res.request_id)

        ts_iso = start_time_iso or datetime.now(timezone.utc).isoformat()
        first_tok = res.timers.user_first_token_ms if res.timers.user_first_token_ms > 0 else res.timers.total_ms
        row = {
            "request_id": res.request_id,
            "scenario": res.scenario,
            "source": res.source,
            "start_time": ts_iso,
            "first_token_ms": round(first_tok, 3),
            "total_ms": round(res.timers.total_ms, 3),
            "status": res.status,
            "retries": res.retries,
            "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out,
            "retrieval_setting": res.retrieval_setting,
            "answer_grade": answer_grade_override or res.answer_grade,
            "citation_grade": citation_grade_override or "citation_not_applicable",
            "queue_wait_ms": round(res.timers.queue_wait_ms, 3),
            "working_ms": round(res.timers.working_ms, 3),
            "retry_wait_ms": round(res.timers.retry_wait_ms, 3),
            "model_first_token_ms": round(res.timers.model_first_token_ms, 3),
            "verification_status": res.verification_status,
            "peak_queue_length": peak_queue_length,
        }
        self.rows.append(row)
        return row

    def save(self, path: Optional[str] = None) -> str:
        """Write rows to a temporary file, then atomically replace the target."""
        target = path or self.output_path
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{target}.tmp"
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)
        os.replace(tmp_path, target)
        return target
