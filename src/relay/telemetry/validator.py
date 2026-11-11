"""Validator auditing results.csv schema, enum integrity, and latency conservation."""

import csv
from typing import List, Dict, Any, Tuple
from src.relay.telemetry.recorder import CSV_COLUMNS


VALID_STATUSES = {"ok", "rate_limited", "timeout", "error"}
VALID_SOURCES = {"real", "stand-in", "standin"}
VALID_GRADES = {"correct", "wrong", "refused_correctly", "refused_wrongly"}


def validate_results_csv(csv_path: str = "results.csv", max_epsilon_ms: float = 15.0) -> Tuple[bool, List[str]]:
    """Audit results.csv for missing columns, invalid values, and latency accounting violations."""
    errors: List[str] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return False, ["CSV file is empty or missing header."]
        missing_cols = [c for c in CSV_COLUMNS if c not in reader.fieldnames]
        if missing_cols:
            errors.append(f"Missing columns: {missing_cols}")

        rows = list(reader)
        if not rows:
            errors.append("results.csv contains 0 rows.")

        for i, row in enumerate(rows, start=1):
            if row.get("status") not in VALID_STATUSES:
                errors.append(f"Row {i} invalid status: {row.get('status')}")
            if row.get("source") not in VALID_SOURCES:
                errors.append(f"Row {i} invalid source: {row.get('source')}")
            if row.get("answer_grade") not in VALID_GRADES:
                errors.append(f"Row {i} invalid answer_grade: {row.get('answer_grade')}")

            try:
                total_ms = float(row["total_ms"])
                q_ms = float(row["queue_wait_ms"])
                w_ms = float(row["working_ms"])
                r_ms = float(row["retry_wait_ms"])
                ft_ms = float(row["first_token_ms"])
                if min(total_ms, q_ms, w_ms, r_ms, ft_ms) < 0:
                    errors.append(f"Row {i} has negative timing value.")
                eps = abs(total_ms - (q_ms + w_ms + r_ms))
                if eps > max_epsilon_ms:
                    errors.append(f"Row {i} latency accounting discrepancy epsilon={eps:.2f}ms > {max_epsilon_ms}ms")
            except (ValueError, KeyError) as e:
                errors.append(f"Row {i} numeric parse error: {e}")

    return len(errors) == 0, errors

