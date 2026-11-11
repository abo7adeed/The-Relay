"""Regression tests for benchmark reproducibility and telemetry integrity."""

import csv
import hashlib
from collections import Counter

import pytest

from relay.pipeline.state import LatencyTimers, PipelineResult
from relay.telemetry.recorder import CSV_COLUMNS, TelemetryRecorder
from relay.telemetry.validator import (
    validate_expected_scenarios,
    validate_results_csv,
)


def _result(request_id: str = "req-1") -> PipelineResult:
    timers = LatencyTimers(
        queue_wait_ms=5.0,
        working_ms=20.0,
        retry_wait_ms=2.0,
        user_first_token_ms=27.0,
        total_ms=27.0,
    )
    return PipelineResult(
        request_id=request_id,
        scenario="steady",
        source="stand-in",
        query="q",
        response_text="r",
        citations=[],
        status="ok",
        answer_grade="correct",
        verification_status="verified_grounded",
        retries=0,
        tokens_in=10,
        tokens_out=5,
        retrieval_setting="setting_3_hybrid_rerank",
        timers=timers,
    )


def test_recorder_rejects_duplicate_request_ids():
    recorder = TelemetryRecorder()
    recorder.add_result(_result("duplicate"))
    with pytest.raises(ValueError, match="Duplicate request_id"):
        recorder.add_result(_result("duplicate"))


def test_overwrite_does_not_delete_previous_file_at_init(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("previous evidence", encoding="utf-8")
    TelemetryRecorder(str(path), overwrite=True)
    assert path.read_text(encoding="utf-8") == "previous evidence"


def test_repeated_save_does_not_append_duplicate_rows(tmp_path):
    path = tmp_path / "results.csv"
    recorder = TelemetryRecorder(str(path), overwrite=True)
    recorder.add_result(_result("req-1"))
    recorder.add_result(_result("req-2"))

    recorder.save()
    first = path.read_text(encoding="utf-8")
    recorder.save()
    second = path.read_text(encoding="utf-8")

    assert first == second
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


def _write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _valid_row(request_id: str = "r1", scenario: str = "steady", setting: str = "setting_3_hybrid_rerank"):
    return {
        "request_id": request_id,
        "scenario": scenario,
        "source": "stand-in",
        "start_time": "2026-11-11T00:00:00+00:00",
        "first_token_ms": "10",
        "total_ms": "27",
        "status": "ok",
        "retries": "0",
        "tokens_in": "10",
        "tokens_out": "5",
        "retrieval_setting": setting,
        "answer_grade": "correct",
        "citation_grade": "citation_not_applicable",
        "queue_wait_ms": "5",
        "working_ms": "20",
        "retry_wait_ms": "2",
        "model_first_token_ms": "10",
        "verification_status": "verified_grounded",
        "peak_queue_length": "1",
    }


def test_validator_detects_duplicate_request_ids(tmp_path):
    path = tmp_path / "results.csv"
    _write_rows(path, [_valid_row("dup"), _valid_row("dup")])
    ok, errors = validate_results_csv(str(path))
    assert ok is False
    assert any("Duplicate request_id" in error for error in errors)


def test_validator_requires_citation_grade_column(tmp_path):
    path = tmp_path / "results.csv"
    row = _valid_row()
    row.pop("citation_grade")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[c for c in CSV_COLUMNS if c != "citation_grade"])
        writer.writeheader()
        writer.writerows([row])
    ok, errors = validate_results_csv(str(path))
    assert ok is False
    assert any("citation_grade" in error for error in errors)


def test_completeness_validator_accepts_exact_120_row_matrix():
    rows = []
    for scenario, count in {
        "retrieval_eval": 45,
        "steady": 4,
        "burst": 20,
        "overload": 24,
        "limit_halved": 12,
        "budget_exhausted": 15,
    }.items():
        for i in range(count):
            setting = (
                ["setting_1_bm25_base", "setting_2_bm25_granular", "setting_3_hybrid_rerank"][i // 15 % 3]
                if scenario == "retrieval_eval"
                else "setting_3_hybrid_rerank"
            )
            rows.append({"scenario": scenario, "retrieval_setting": setting})
    ok, errors = validate_expected_scenarios(rows)
    assert ok is True, errors
    assert len(rows) == 120


def test_completeness_validator_rejects_missing_scenario():
    rows = [{"scenario": "steady", "retrieval_setting": "setting_3_hybrid_rerank"}] * 4
    ok, errors = validate_expected_scenarios(rows)
    assert ok is False
    assert any("retrieval_eval" in error for error in errors)
    assert any("Total rows" in error for error in errors)


def test_archive_preserves_original_results(tmp_path):
    from benchmarks.run_benchmarks import archive_existing_results

    results = tmp_path / "results.csv"
    results.write_text("original", encoding="utf-8")
    archive_dir = tmp_path / "archive"
    archived_path, digest = archive_existing_results(str(results), str(archive_dir))

    assert archived_path is not None
    assert digest == hashlib.sha256(b"original").hexdigest()
    assert open(archived_path, "r", encoding="utf-8").read() == "original"
    assert results.read_text(encoding="utf-8") == "original"
