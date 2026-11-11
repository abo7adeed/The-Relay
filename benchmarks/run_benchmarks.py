"""Executable benchmark harness running Retrieval Settings (Table 3) and Load Scenarios (Tables 2a/2b)."""

import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))

import httpx
import uvicorn

from relay.evaluation.grading import grade_citation, grade_eval_answer
from relay.gateway.circuit_breaker import CircuitBreaker
from relay.gateway.gateway import AdmissionGateway
from relay.gateway.rate_limiter import LocalRateLimiter
from relay.gateway.retry_policy import RetryPolicy
from relay.models.groq_provider import GroqProvider
from relay.models.standin_service import app as standin_app
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool
from relay.telemetry.recorder import TelemetryRecorder
from relay.telemetry.validator import (
    EXPECTED_SCENARIO_COUNTS,
    validate_expected_scenarios,
    validate_results_csv,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

STANDIN_HOST = "127.0.0.1"
STANDIN_PORT = 8765
STANDIN_BASE_URL = f"http://{STANDIN_HOST}:{STANDIN_PORT}"

RESULTS_PATH = "results.csv"
PENDING_RESULTS_PATH = "results.pending.csv"
ARCHIVE_DIR = "data/archive"
METADATA_PATH = "data/benchmark_run_metadata.json"
EVAL_QUESTIONS_PATH = "data/eval_questions.json"


def start_standin_server() -> uvicorn.Server:
    """Launch StandInService on a background daemon thread using real HTTP/SSE over TCP."""
    config = uvicorn.Config(standin_app, host=STANDIN_HOST, port=STANDIN_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for health check
    for _ in range(50):
        try:
            r = httpx.get(f"{STANDIN_BASE_URL}/health", timeout=0.5)
            if r.status_code == 200:
                return server
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Failed to start local StandInService uvicorn server.")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_existing_results(
    results_path: str = RESULTS_PATH,
    archive_dir: str = ARCHIVE_DIR,
) -> Tuple[str | None, str | None]:
    """Preserve the previous results.csv before a new run replaces it."""
    if not os.path.exists(results_path):
        return None, None

    os.makedirs(archive_dir, exist_ok=True)
    original_hash = _sha256(results_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = os.path.join(archive_dir, f"results_{stamp}.csv")
    suffix = 1
    while os.path.exists(destination):
        destination = os.path.join(archive_dir, f"results_{stamp}_{suffix}.csv")
        suffix += 1
    shutil.copy2(results_path, destination)
    return destination, original_hash


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def write_run_metadata(
    results_path: str,
    rows: List[Dict[str, Any]],
    archived_path: str | None,
    archived_sha256: str | None,
    started_at: str,
) -> str:
    """Record reproducibility metadata next to the validated CSV."""
    scenario_counts = Counter(r["scenario"] for r in rows)
    grade_counts = Counter(r["answer_grade"] for r in rows)
    citation_counts = Counter(r["citation_grade"] for r in rows)

    groq_status = None
    if os.path.exists("data/observed_limits.json"):
        with open("data/observed_limits.json", "r", encoding="utf-8") as f:
            groq_status = json.load(f).get("status")

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_started_at": started_at,
        "git_commit": _git_commit(),
        "python_version": platform.python_version(),
        "benchmark_script": {
            "path": "benchmarks/run_benchmarks.py",
            "sha256": _sha256("benchmarks/run_benchmarks.py"),
        },
        "eval_questions": {
            "path": EVAL_QUESTIONS_PATH,
            "sha256": _sha256(EVAL_QUESTIONS_PATH),
        },
        "results_csv": {
            "path": results_path,
            "sha256": _sha256(results_path),
            "rows": len(rows),
        },
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "answer_grade_counts": dict(sorted(grade_counts.items())),
        "citation_grade_counts": dict(sorted(citation_counts.items())),
        "archived_previous_results": archived_path,
        "archived_previous_sha256": archived_sha256,
        "groq_status": groq_status,
        "execution_mode": "stand-in HTTP/SSE; real Groq not measured",
    }
    tmp_path = f"{METADATA_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    os.replace(tmp_path, METADATA_PATH)
    return METADATA_PATH


async def run_retrieval_evaluations(recorder: TelemetryRecorder) -> None:
    """Run Settings 1, 2, and 3 across all 15 audited questions over HTTP SSE streaming."""
    with open(EVAL_QUESTIONS_PATH, "r", encoding="utf-8") as f:
        eval_questions = json.load(f)

    idx_350 = BM25Index.from_json_file("data/processed/chunks_setting_1.json")
    idx_175 = BM25Index.from_json_file("data/processed/chunks_setting_2.json")

    settings = [
        ("setting_1_bm25_base", RetrievalTool(idx_350, use_reranker=False, default_top_k=4)),
        ("setting_2_bm25_granular", RetrievalTool(idx_175, use_reranker=False, default_top_k=6)),
        ("setting_3_hybrid_rerank", RetrievalTool(idx_350, use_reranker=True, rerank_fetch_k=8, default_top_k=3)),
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Configure high TPM temporarily so isolated retrieval quality evaluation measures pure retrieval accuracy
        await client.post(f"{STANDIN_BASE_URL}/admin/reset")
        await client.post(
            f"{STANDIN_BASE_URL}/admin/configure",
            params={"rpm_override": 500, "tpm_override": 500000, "ttft_mu": 5.3, "ttft_sigma": 0.25},
        )
        provider = GroqProvider(
            api_key="standin-key",
            base_url=f"{STANDIN_BASE_URL}/v1",
            timeout_seconds=15.0,
            client=client,
        )

        for setting_id, tool in settings:
            engine = PipelineEngine(tool, provider, max_loops=1, use_streaming=True)
            print(f"[Retrieval Eval] Running {setting_id} across {len(eval_questions)} questions...")
            for q in eval_questions:
                start_iso = datetime.now(timezone.utc).isoformat()
                ctx = RequestContext(
                    query=q["question"],
                    scenario="retrieval_eval",
                    source="stand-in",
                    retrieval_setting=setting_id,
                    ttl_seconds=15.0,
                )
                res = await engine.process_request(ctx)
                grade = grade_eval_answer(q, res.response_text, res.verification_status)
                citation_grade = grade_citation(q, res.citations)
                recorder.add_result(
                    res,
                    start_time_iso=start_iso,
                    answer_grade_override=grade,
                    citation_grade_override=citation_grade,
                    peak_queue_length=0,
                )


async def run_load_scenario(
    recorder: TelemetryRecorder,
    scenario: str,
    queries: List[str],
    interval_s: float,
    ttl_s: float,
    concurrency_limit: int,
    queue_max_size: int,
    rpm_override: int | None = None,
    tpm_override: int | None = None,
    force_daily: bool = False,
    local_tpm_limit: int = 6000,
    local_rpm_limit: int = 30,
) -> None:
    """Execute one controlled load scenario through AdmissionGateway against StandInService."""
    idx_s3 = BM25Index.from_json_file("data/processed/chunks_setting_3.json")
    tool = RetrievalTool(idx_s3, use_reranker=True, rerank_fetch_k=8, default_top_k=3)

    async with httpx.AsyncClient(timeout=20.0) as client:
        await client.post(f"{STANDIN_BASE_URL}/admin/reset")
        params: Dict[str, Any] = {"ttft_mu": 5.45, "ttft_sigma": 0.35, "capacity_limit": 5}
        if rpm_override is not None:
            params["rpm_override"] = rpm_override
        if tpm_override is not None:
            params["tpm_override"] = tpm_override
        if force_daily:
            params["force_daily_exhaustion"] = True
        await client.post(f"{STANDIN_BASE_URL}/admin/configure", params=params)

        provider = GroqProvider(
            api_key="standin-key",
            base_url=f"{STANDIN_BASE_URL}/v1",
            timeout_seconds=10.0,
            client=client,
        )
        breaker = CircuitBreaker()
        limiter = LocalRateLimiter(rpm_limit=local_rpm_limit, tpm_limit=local_tpm_limit, degrade_threshold_pct=85.0)
        retry_pol = RetryPolicy(
            max_attempts=2,
            retry_budget_pct=15.0,
            backoff_base_s=0.6,
            backoff_max_s=3.0,
            circuit_breaker=breaker,
        )
        engine = PipelineEngine(tool, provider, max_loops=1, retry_policy=retry_pol, use_streaming=True)
        gateway = AdmissionGateway(
            engine=engine,
            queue_max_size=queue_max_size,
            concurrency_limit=concurrency_limit,
            rate_limiter=limiter,
            circuit_breaker=breaker,
        )
        await gateway.start()

        print(f"[Load Scenario] Running '{scenario}': N={len(queries)}, interval={interval_s}s, ttl={ttl_s}s, C={concurrency_limit}...")
        tasks: List[Tuple[str, asyncio.Task]] = []
        try:
            for i, q_text in enumerate(queries):
                start_iso = datetime.now(timezone.utc).isoformat()
                ctx = RequestContext(
                    query=q_text,
                    scenario=scenario,
                    source="stand-in",
                    retrieval_setting="setting_3_hybrid_rerank",
                    ttl_seconds=ttl_s,
                )
                tasks.append((start_iso, asyncio.create_task(gateway.submit(ctx))))
                if interval_s > 0 and i < len(queries) - 1:
                    await asyncio.sleep(interval_s)

            results = await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)
            failures = [r for r in results if isinstance(r, BaseException)]
            if failures:
                raise RuntimeError(
                    f"Scenario {scenario!r} had {len(failures)} failed request task(s); "
                    f"first failure: {failures[0]!r}"
                )
            for (start_iso, _), res in zip(tasks, results):
                recorder.add_result(
                    res,
                    start_time_iso=start_iso,
                    citation_grade_override="citation_not_applicable",
                    peak_queue_length=gateway.peak_queue_length,
                )
        finally:
            await gateway.stop()


async def main() -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    server = start_standin_server()
    try:
        with open(EVAL_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            eval_questions = json.load(f)
        # Focus load mix on answerable questions to realistically stress token throughput
        sample_queries = [q["question"] for q in eval_questions[:11]]

        # Pending output is validated first; results.csv is only replaced afterwards.
        recorder = TelemetryRecorder(output_path=PENDING_RESULTS_PATH, overwrite=True)

        # 1. Retrieval Settings Evaluation (Table 3: Settings 1, 2, 3)
        await run_retrieval_evaluations(recorder)

        # 2. Steady Load: 4 requests spaced 16.0s apart (~3.75 QPM, within 6k TPM ceiling -> 100% success)
        steady_qs = [sample_queries[i % len(sample_queries)] for i in range(4)]
        await run_load_scenario(
            recorder, scenario="steady", queries=steady_qs,
            interval_s=16.0, ttl_s=10.0, concurrency_limit=3, queue_max_size=50,
            local_tpm_limit=6000, local_rpm_limit=30,
        )

        # 3. Sudden Burst: 20 concurrent requests launched in < 0.5s behind C=2 workers with TTL=2.2s
        # Demonstrates peak FIFO queueing, retry backoff holding workers, 429 rate limits, and TTL expirations
        burst_qs = [sample_queries[i % len(sample_queries)] for i in range(20)]
        await run_load_scenario(
            recorder, scenario="burst", queries=burst_qs,
            interval_s=0.02, ttl_s=2.2, concurrency_limit=2, queue_max_size=18,
            local_tpm_limit=6000, local_rpm_limit=30,
        )

        # 4. Sustained Overload: 24 requests pushed at ~400 QPM (0.15s interval) behind C=1, q=10, TTL=1.2s
        # Demonstrates FIFO queue saturation, queue-full shedding, TPM 429s, and real TTL abandonment timeouts
        overload_qs = [sample_queries[i % len(sample_queries)] for i in range(24)]
        await run_load_scenario(
            recorder, scenario="overload", queries=overload_qs,
            interval_s=0.15, ttl_s=1.2, concurrency_limit=1, queue_max_size=10,
            local_tpm_limit=6000, local_rpm_limit=30,
        )

        # 5. Per-Minute Limit Halved: 12 requests at 30 QPM (2.0s interval) under halved 15 RPM / 3000 TPM cap
        halved_qs = [sample_queries[i % len(sample_queries)] for i in range(12)]
        await run_load_scenario(
            recorder, scenario="limit_halved", queries=halved_qs,
            interval_s=2.0, ttl_s=4.0, concurrency_limit=2, queue_max_size=20,
            rpm_override=15, tpm_override=3000, local_tpm_limit=3000, local_rpm_limit=15,
        )

        # 6. Daily Budget Exhausted (3:00 PM Scenario): 15 requests hitting daily quota -> Circuit Breaker OPEN
        exhausted_qs = [sample_queries[i % len(sample_queries)] for i in range(15)]
        await run_load_scenario(
            recorder, scenario="budget_exhausted", queries=exhausted_qs,
            interval_s=0.05, ttl_s=10.0, concurrency_limit=3, queue_max_size=50,
            force_daily=True,
        )

        complete, completeness_errors = validate_expected_scenarios(recorder.rows)
        if not complete:
            recorder.save(PENDING_RESULTS_PATH)
            print(f"\nBenchmark completeness validation FAILED. Partial rows saved to {PENDING_RESULTS_PATH}.")
            for e in completeness_errors[:10]:
                print("  COMPLETENESS ERROR:", e)
            sys.exit(1)

        recorder.save(PENDING_RESULTS_PATH)
        ok, errs = validate_results_csv(PENDING_RESULTS_PATH)
        if not ok:
            print(f"\nPending results failed CSV validation; {RESULTS_PATH} was not replaced.")
            for e in errs[:10]:
                print("  VALIDATION ERROR:", e)
            sys.exit(1)

        archived_path, archived_hash = archive_existing_results(RESULTS_PATH)
        os.replace(PENDING_RESULTS_PATH, RESULTS_PATH)
        metadata_path = write_run_metadata(
            RESULTS_PATH, recorder.rows, archived_path, archived_hash, started_at
        )

        scenario_counts = Counter(r["scenario"] for r in recorder.rows)
        print(f"\nSaved {len(recorder.rows)} rows to {RESULTS_PATH}. Validation passed: True")
        print("Scenario counts:", dict(sorted(scenario_counts.items())))
        print("Expected total:", sum(EXPECTED_SCENARIO_COUNTS.values()))
        if archived_path:
            print(f"Previous results preserved at {archived_path} (sha256={archived_hash})")
        print(f"Run metadata written to {metadata_path}")
    finally:
        server.should_exit = True


if __name__ == "__main__":
    asyncio.run(main())
