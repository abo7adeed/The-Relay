# Final Submission Audit & Reconciliation — The Relay (Revision 4+)

**Date:** 2026-11-11  
**Baseline Commit:** `862318a` (`main`) — *"Add Revision 4 grading/CI, live Groq verification (qwen/qwen3.8-27b), and verifier fixes"*  
**Auditor Role:** Senior AI/ML Infrastructure Engineer & Independent Release Auditor  
**Verdict:** **PASS — Ready for Push Authorization**

---

## 1. Executive Summary & Release Verdict

Post-push reconciliation on Revision 4 (`862318a`) resolved the provider-routing contradiction between the codebase (`respan_gateway` vs. `direct_groq`), verified live Direct Groq connectivity using the updated local `.env` `GROQ_API_KEY`, re-validated the Revision 4 mandatory-fact grading suite and 120-row Stand-in telemetry (`results.csv`), confirmed green GitHub Actions CI, and synchronized all technical documentation without altering historical archives or exposing credentials.

- **Live Provider Status:** **`VERIFIED_ONLINE`** on Direct Groq (`https://api.groq.com/openai/v1`, model `qwen/qwen3.8-27b`). `8,000` TPM and `1,000` RPD were observed directly in HTTP response headers; `rpm` and `tpd` are not emitted in headers and remain explicitly `null` (unverified).
- **Controlled Live Pipeline Check:** `5/5` SSE requests (`Q01`, `Q02`, `Q04`, `Q08`, `Q14`) succeeded over HTTPS against `direct_groq` (`446.2–1,392.8 ms` user TTFT, `6,370` input / `465` output tokens, `4/4` target-cited on answerable questions, `3` `correct`, `1` `refused_correctly`, and `1` documented `refused_wrongly` heuristic interaction on `Q02`).
- **Automated Tests:** **`109 passed in 103.07s`** (`python -m pytest tests/ -q`), zero failures, zero skips.
- **Stand-in Benchmark Separation:** All 120 rows in `results.csv` (`sha256: 441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`) remain pure local Stand-in data (`llama-3.1-8b-instant` 30 RPM / 6,000 TPM simulation) and were **not** mixed with live probe runs.
- **GitHub Actions CI:** Passing on pushed commit `862318a` (Run `43091684869`, job `test`, `ubuntu-latest`, Python `3.11`).

---

## 2. Repository Baseline, CI Status, and Security Audit

### 2.1 Git Baseline
- **Branch:** `main`
- **Inspected HEAD before reconciliation:** `862318a` (preceded by `cfb9e0f`)
- **Initial working tree:** clean (`git status --short` returned empty before edits)

### 2.2 GitHub Actions CI Verification
- **Run ID:** `43091684869` ([GitHub Actions Run `43091684869`](https://github.com/abo7adeed/The-Relay/actions/runs/43091684869))
- **Target SHA:** `862318a` (`event=push`, `status=completed`, `conclusion=success`)
- **Job `test` (`ubuntu-latest`, Python 3.11) steps all `success`:**
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5`
  3. `Install package and test dependencies` (`pip install -e ".[dev]"`)
  4. `Run test suite` (`python -m pytest tests/ -q`)
  5. `Validate committed benchmark CSV` (`validate_results_csv` + `validate_expected_scenarios` = 120 rows)
  6. `Recompute report tables` (`python benchmarks/generate_report_tables.py`)
- **CI Scope Note:** `.github/workflows/ci.yml` executes deterministic offline checks and tests only; it does not read `.env` or contact external LLM endpoints.

### 2.3 Secret & Hygiene Controls
- `.gitignore` explicitly excludes `.env`, `.env.local`, `.env.*.local`, Python caches, virtual environments, and temp logs (`*.log`, `*.tmp`, `*.bak`).
- Non-sensitive configuration inspection (`PipelineConfig()`) verified:
  - `groq_api_key`: configured (`True`, prefix `gsk_`, length `56`)
  - `respan_api_key`: unset (`False`, length `0`)
- Full workspace secret scan for live `gsk_[A-Za-z0-9]{25,}` patterns outside `.env` returned **`secret_pattern_hits= 0 []`** (`tests/test_models.py` contains only the short unit-test dummy string `"gsk_dummy_test_key_12345"`).

---

## 3. Provider Identity & Contradiction Resolution

Revision 4 commit `862318a` introduced a contradiction across scripts, data files, and docs:

1. `benchmarks/probe_groq.py` and `benchmarks/probe_live_pipeline.py` were switched to require `RESPAN_API_KEY` via `build_gateway_provider()` (`https://gateway.respan.ai/v1`), whereas the local environment holds a working `GROQ_API_KEY` (`direct_groq`) and no `RESPAN_API_KEY`.
2. `data/observed_limits.json` had been reset to `NOT_VERIFIED_MISSING_CREDENTIALS` while `data/real_groq_probe_results.json` held an earlier unattributed 5-call Direct Groq run.

**Reconciliation Implemented:**
- **`benchmarks/probe_groq.py` & `benchmarks/probe_live_pipeline.py`:** Now select `respan_gateway` (`https://gateway.respan.ai/v1`) when `RESPAN_API_KEY` is configured and select `direct_groq` (`https://api.groq.com/openai/v1`) via `GroqProvider` when `GROQ_API_KEY` is configured. Both scripts fail cleanly if neither credential exists, and record explicit `provider_type`, `endpoint_url`, `auth_mode`, and `model_configured` metadata on every output.
- **Stand-in remains distinct:** `src/relay/models/standin_service.py` (`http://127.0.0.1:8000/v1`) continues to serve local load/retrieval simulations (`llama-3.1-8b-instant` defaults) for `results.csv`.

---

## 4. Live Provider Probe & Observed Limits (`data/observed_limits.json`)

| Field | Measured Value | Source / Basis |
| :--- | :--- | :--- |
| `status` | `VERIFIED_ONLINE` | `benchmarks/probe_groq.py` (`2026-11-11T20:16:10Z`) |
| `provider_type` | `direct_groq` | Explicit credential resolution (`GROQ_API_KEY`) |
| `endpoint_url` | `https://api.groq.com/openai/v1` | `GroqProvider` HTTP client |
| `model_configured` / `model_returned` | `qwen/qwen3.8-27b` / `qwen/qwen3.8-27b` | `POST /chat/completions` JSON response |
| Single minimal probe latency | `368.9 ms` (`20` in / `2` out tokens, output `"READY"`) | End-to-end HTTP call |
| `tpm` (observed) | `8,000` (`x-ratelimit-limit-tokens: "8000"`, `reset=269ms`) | HTTP response headers |
| `rpd` (observed) | `1,000` (`x-ratelimit-limit-requests: "1000"`, `reset=1m26.4s`) | HTTP response headers (`86,400s / 1,000 = 86.4s`) |
| `rpm` (observed) | `null` (**unverified**) | No per-minute request limit header returned |
| `tpd` (observed) | `null` (**unverified**) | No daily token limit header returned |

**Account Model Inventory (`GET /openai/v1/models` -> HTTP 200, 11 models):**  
`allam-2-7b`, `canopylabs/orpheus-arabic-saudi`, `canopylabs/orpheus-v1-english`, `meta-llama/llama-prompt-guard-2-22m`, `meta-llama/llama-prompt-guard-2-86m`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `whisper-large-v3`, `whisper-large-v3-turbo`.  
Note: `llama-3.1-8b-instant` (`PipelineConfig.default_model`, simulated by the Stand-in) is **not** exposed on this Groq account.

*Header Parsing Note:* `GroqProvider._parse_rate_limit_headers()` populates `limits.rpm_observed` from `x-ratelimit-limit-requests` for unit-test compatibility (`tests/test_models.py`), whereas `benchmarks/probe_groq.py` classifies `x-ratelimit-limit-requests=1000` with `1m26.4s` reset accurately into `rpd=1000` and `rpm=null` in `data/observed_limits.json`.

---

## 5. Controlled 5-Query Live SSE Pipeline Verification (`data/real_groq_probe_results.json`)

Executed `python benchmarks/probe_live_pipeline.py` against `direct_groq` (`https://api.groq.com/openai/v1`, `qwen/qwen3.8-27b`, Setting 3 `350w/50o k=8->3`, `use_streaming=True`, `11.0s` pacing delay) at `2026-11-11T20:16:24Z–20:17:12Z`:

| QID | Type | Request ID | Status | Verify Status | Answer Grade | Citation Grade | Model TTFT (ms) | User TTFT / Total (ms) | Tokens (in/out) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: |
| `Q01` | `specific_fact` | `f44cf130-d008-45d1-b7f8-f48f56327119` | `ok` | `verified_grounded` | `correct` | `citation_correct` | 633.0 | 804.4 | 1417 / 39 |
| `Q02` | `specific_fact` | `95aa0152-9457-4c72-91f8-0206b3d0030e` | `ok` | `verified_grounded` | `refused_wrongly` | `citation_correct` | 458.8 | 862.5 | 1211 / 220 |
| `Q04` | `formula_values` | `81a5a095-87dd-48c4-9caa-485313e8b53f` | `ok` | `verified_grounded` | `correct` | `citation_correct` | 1234.7 | 1392.8 | 725 / 85 |
| `Q08` | `numeric_claim` | `fce6afa2-8faf-4177-8452-4ec4fdb35382` | `ok` | `verified_grounded` | `correct` | `citation_correct` | 481.9 | 712.8 | 1348 / 107 |
| `Q14` | `unanswerable` | `f7d9803d-4da1-47ed-8aa3-7a8375b4dcc2` | `ok` | `verified_refusal` | `refused_correctly` | `citation_not_applicable` | 420.1 | 446.2 | 1669 / 14 |

**Aggregate Live Probe Totals (`N=5`):** `5/5` HTTP `ok` (`0` retries), `6,370` input tokens, `465` output tokens, `4/4` `citation_correct` on answerable questions, `3` `correct`, `1` `refused_correctly`, `1` `refused_wrongly` (`Q02`).

**Honest `Q02` Observation:** `qwen/qwen3.8-27b` grounded and cited `[Attention Is All You Need, 5.3 Optimizer, Page 7]` (`verification_status="verified_grounded"`, `citation_grade="citation_correct"`), but began with *"The provided papers do not state a single universal number of warmup steps..."* followed by `"**4,000** warmup steps"`. Under unmodified Revision 4 `grade_eval_answer`, both the opening refusal phrase and comma-formatted `"4,000"` vs `"4000"` yield `refused_wrongly`. Revision 4 `src/relay/evaluation/grading.py` was intentionally left untouched to preserve strict benchmark comparability and regression parity.

---

## 6. Revision 4 Grading & Automated Regression Suite

- **Grader module (`src/relay/evaluation/grading.py`):** Verified strict all-facts conjunction, boundary-aware numeric regex `(?<![\d.])fact(?![\d.])`, decimal/multiplication normalization (`0 . 1` -> `0.1`, `4 ×` -> `4x`), loud `ValueError` on empty/missing non-trap `expected_facts`, refusal semantics, and separate exact-target `grade_citation`.
- **Full Test Execution:**
  - Command: `python -m pytest tests/ -q`
  - Result: **`109 passed in 103.07s (0:01:43)`** across `test_benchmark_grading.py`, `test_benchmark_integrity.py`, `test_config.py`, `test_corpus.py`, `test_eval_set.py`, `test_gateway_and_retries.py`, `test_models.py`, `test_pipeline.py`, `test_reranker.py`, `test_retrieval_edge_cases.py`, `test_retry_accounting.py`, `test_standin_parity.py`, `test_ttl_behavior.py`, and `test_verifier_invariant.py`.

---

## 7. Stand-in Benchmark Matrix & Separation of Evidence (`results.csv`)

- **Validator Verification:** `validate_results_csv('results.csv')` + `validate_expected_scenarios(rows)` returned **`schema_ok=True`, `completeness_ok=True`, `rows=120`, `errors=[]`**.
- **CSV Identity:** `results.csv` SHA-256 = `441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd` (`retrieval_eval=45`, `steady=4`, `burst=20`, `overload=24`, `limit_halved=12`, `budget_exhausted=15`).
- **Recomputed Tables (`python benchmarks/generate_report_tables.py`):**
  - **Table 3 (`retrieval_eval`, `N=15` per setting, Stand-in):**
    - Setting 1 (`350w/50o, k=4, rerank=No`): `facts=53.3%`, `cite_ok=63.6%`, `facts+cite=33.3%`, `wrong=13.3%`, `ref_ok=26.7%`, `ref_wrong=6.7%`, `1900.3` tok/ans.
    - Setting 2 (`175w/25o, k=6, rerank=No`): `facts=46.7%`, `cite_ok=63.6%`, `facts+cite=33.3%`, `wrong=13.3%`, `ref_ok=26.7%`, `ref_wrong=13.3%`, `1789.4` tok/ans.
    - Setting 3 (`350w/50o, k=8->3, rerank=Yes`): `facts=53.3%`, `cite_ok=54.5%`, `facts+cite=33.3%`, `wrong=13.3%`, `ref_ok=26.7%`, `ref_wrong=6.7%`, `1373.1` tok/ans.
  - **Latency conservation:** Verified across all 120 rows within `< 15 ms` threshold (slow trace `7afa5482-51c8-46b4-8e71-eed4ecf95673` `1914.041 ms`, retry trace `3803d90f-e702-4925-9eb5-07205437281a` `3027.711 ms`).
- **Historical Metadata Preservation:** `data/benchmark_run_metadata.json` remains unchanged as the point-in-time provenance snapshot of the `2026-11-11T19:12:53Z` Stand-in CSV generation.

---

## 8. Historical Archive Integrity

All original historical artifacts in `data/archive/` were left untouched, and pre-reconciliation Revision 4 snapshots were preserved prior to running new live probes:
- Untouched originals/Rev4 logs: `results_rev3_original.csv`, `results_20261111T190703Z.csv`, `results_20261111T191253Z.csv`, `report_tables_rev3_original.txt`, `report_tables_rev4.txt`, `report_tables_rev4_final.txt`, `groq_probe_rev4_401.log`, `groq_probe_rev4_401.txt`, `observed_limits_probe_failed_20261111T191057Z.json`, `live_probe_artifact_20261111T2057Z/`, `benchmark_run_rev4.log`, `benchmark_run_rev4_final.log`, `benchmark_run_rev4_final.txt`, `pytest_rev4_final.log`, `pytest_rev4_final.txt`.
- Newly archived pre-reconciliation snapshots:
  - `data/archive/observed_limits_rev4_pre_reconcile.json`
  - `data/archive/real_groq_probe_results_rev4_pre_reconcile.json`

---

## 9. Documentation & Script Changes Summary

| File | Reconciliation Update |
| :--- | :--- |
| `benchmarks/probe_groq.py` | Supports `respan_gateway` (`RESPAN_API_KEY`) and `direct_groq` (`GROQ_API_KEY`); inspects `/models`; classifies RPD vs. TPM without fabricating `rpm` or `tpd`. |
| `benchmarks/probe_live_pipeline.py` | Supports `respan_gateway` and `direct_groq`; grades 5 paced SSE requests via `grade_eval_answer` & `grade_citation`; emits explicit provider/endpoint provenance. |
| `data/observed_limits.json` | Updated to `VERIFIED_ONLINE` (`direct_groq`, `qwen/qwen3.8-27b`, `8,000` TPM / `1,000` RPD, `rpm`/`tpd` `null`). |
| `data/real_groq_probe_results.json` | Updated with reconciled 5-query `direct_groq` SSE pipeline traces, citations, and grades. |
| `README.md` | Updated file tree and **Live-provider status** paragraph separating live Direct Groq evidence from 120-row Stand-in `results.csv`. |
| `docs/THE_RELAY_2_PAGE_REPORT.md` | Reconciled Banner, Sections 2, 3, 8, and **Table 1** with measured `qwen/qwen3.8-27b` headers alongside unverified fields and Stand-in `llama-3.1-8b-instant` references. |
| `docs/REVISION_4_IMPLEMENTATION_REPORT.md` | Kept historical Revision 4 body accurate to `862318a` and added **Section 9: Post-push final submission reconciliation**. |
| `docs/FINAL_SUBMISSION_AUDIT.md` | Added this complete 11-section release audit. |

---

## 10. Remaining Limitations & Unverified Items

1. **`rpm` and `tpd` remain unverified on live Groq (`null`):** Direct Groq HTTP headers emitted `x-ratelimit-limit-requests=1000` (`reset=1m26.4s`, daily request quota) and `x-ratelimit-limit-tokens=8000` (`reset=269ms`, TPM), not per-minute RPM or daily TPD.
2. **Respan Gateway (`respan_gateway`) not exercised live:** `RESPAN_API_KEY` is unset; gateway BYOK path remains tested structurally at unit level only.
3. **`llama-3.1-8b-instant` not available on this Groq account:** `/openai/v1/models` returns 11 models including `qwen/qwen3.8-27b`, without `llama-3.1-8b-instant`; Stand-in latency/load calibration remains simulated against published `30 RPM / 6,000 TPM` values.
4. **Multi-tenant concurrency & sustained live 429 backoff untested:** The 5-query live probe was intentionally paced (11s spacing, 1 request in flight) to respect the 8,000 TPM limit without triggering HTTP 429 burst exhaustion.
5. **Comma-formatted numbers + disclaimer prefaces in `grade_eval_answer`:** Observed on live `Q02` (`"The provided papers do not... 4,000"` graded `refused_wrongly` despite `verified_grounded` and `citation_correct`). Left unchanged to preserve Revision 4 `results.csv` comparability.

---

## 11. Final Pre-Push Verification & Authorization Gate

- [x] Live Direct Groq minimal probe (`VERIFIED_ONLINE`, `qwen/qwen3.8-27b`) completed & saved without secrets.
- [x] Live 5-query SSE pipeline test completed (`5/5` HTTP `ok`) & saved without secrets.
- [x] Full test suite passed after final edits (`109 passed in 103.07s`).
- [x] `results.csv` schema, duplicate, completeness (120 rows), and latency-conservation checks passed.
- [x] `generate_report_tables.py` recomputed Tables 2a, 2b, and 3 cleanly from `results.csv`.
- [x] Zero live credentials or `.env` leaks (`secret_pattern_hits= 0`).
- [ ] **Git push held pending explicit user authorization.**

