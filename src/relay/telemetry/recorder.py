"""Thread-safe/async-safe CSV telemetry recorder writing results.csv."""

import csv
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from src.relay.pipeline.state import PipelineResult

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
    "queue_wait_ms",
    "working_ms",
    "retry_wait_ms",
    "model_first_token_ms",
    "verification_status",
    "peak_queue_length",
]


class TelemetryRecorder:
    """Records per-request execution telemetry to results.csv."""

    def __init__(self, output_path: str = "results.csv", overwrite: bool = False):
        self.output_path = output_path
        self.rows: List[Dict[str, Any]] = []
        if overwrite and os.path.exists(self.output_path):
            os.remove(self.output_path)

    def add_result(
        self,
        res: PipelineResult,
        start_time_iso: Optional[str] = None,
        answer_grade_override: Optional[str] = None,
        peak_queue_length: int = 0,
    ) -> Dict[str, Any]:
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
            "queue_wait_ms": round(res.timers.queue_wait_ms, 3),
            "working_ms": round(res.timers.working_ms, 3),
            "retry_wait_ms": round(res.timers.retry_wait_ms, 3),
            "model_first_token_ms": round(res.timers.model_first_token_ms, 3),
            "verification_status": res.verification_status,
            "peak_queue_length": peak_queue_length,
        }
        self.rows.append(row)
        return row

    def save(self) -> str:
        parent = os.path.dirname(self.output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)
        return self.output_path
