"""Validator auditing results.csv schema, enums, duplicates, completeness, and latency conservation."""

import csv
from collections import Counter
from typing import List, Dict, Any, Tuple
from relay.telemetry.recorder import CSV_COLUMNS


VALID_STATUSES = {"ok", "rate_limited", "timeout", "error"}
VALID_SOURCES = {"real", "stand-in", "standin"}
VALID_GRADES = {"correct", "wrong", "refused_correctly", "refused_wrongly"}
VALID_CITATION_GRADES = {"citation_correct", "citation_wrong", "citation_not_applicable"}
VALID_RETRIEVAL_SETTINGS = {
    "setting_1_bm25_base",
    "setting_2_bm25_granular",
    "setting_3_hybrid_rerank",
}

# Revision 4 benchmark matrix: 3 retrieval settings x 15 questions + 75 load requests.
EXPECTED_SCENARIO_COUNTS = {
    "retrieval_eval": 45,
    "steady": 4,
    "burst": 20,
    "overload": 24,
    "limit_halved": 12,
    "budget_exhausted": 15,
}
EXPECTED_TOTAL_ROWS = sum(EXPECTED_SCENARIO_COUNTS.values())


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

        request_ids = [row.get("request_id", "") for row in rows]
        duplicate_ids = sorted({rid for rid, count in Counter(request_ids).items() if count > 1})
        if duplicate_ids:
            errors.append(f"Duplicate request_id values: {duplicate_ids[:5]}")

        for i, row in enumerate(rows, start=1):
            if row.get("status") not in VALID_STATUSES:
                errors.append(f"Row {i} invalid status: {row.get('status')}")
            if row.get("source") not in VALID_SOURCES:
                errors.append(f"Row {i} invalid source: {row.get('source')}")
            if row.get("answer_grade") not in VALID_GRADES:
                errors.append(f"Row {i} invalid answer_grade: {row.get('answer_grade')}")
            if row.get("citation_grade") not in VALID_CITATION_GRADES:
                errors.append(f"Row {i} invalid citation_grade: {row.get('citation_grade')}")
            if row.get("retrieval_setting") not in VALID_RETRIEVAL_SETTINGS:
                errors.append(f"Row {i} invalid retrieval_setting: {row.get('retrieval_setting')}")

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


def validate_expected_scenarios(
    rows: List[Dict[str, Any]],
    expected_counts: Dict[str, int] | None = None,
) -> Tuple[bool, List[str]]:
    """Check that every scenario and the full 120-record matrix is present exactly once per request."""
    expected = expected_counts or EXPECTED_SCENARIO_COUNTS
    errors: List[str] = []
    actual = Counter(row.get("scenario", "") for row in rows)

    for scenario, expected_count in expected.items():
        if actual.get(scenario, 0) != expected_count:
            errors.append(
                f"Scenario {scenario!r} produced {actual.get(scenario, 0)} rows; "
                f"expected {expected_count}."
            )
    unexpected = sorted(set(actual) - set(expected))
    if unexpected:
        errors.append(f"Unexpected scenarios present: {unexpected}")

    expected_total = sum(expected.values())
    if len(rows) != expected_total:
        errors.append(f"Total rows {len(rows)} != expected {expected_total}.")

    retrieval_rows = [r for r in rows if r.get("scenario") == "retrieval_eval"]
    per_setting = Counter(r.get("retrieval_setting", "") for r in retrieval_rows)
    for setting in VALID_RETRIEVAL_SETTINGS:
        if per_setting.get(setting, 0) != 15:
            errors.append(
                f"Retrieval setting {setting!r} produced {per_setting.get(setting, 0)} rows; expected 15."
            )

    return len(errors) == 0, errors
