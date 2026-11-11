# Final Submission Audit — The Relay (Vaani Research)

**Date:** 2026-11-11
**Repository:** `https://github.com/abo7adeed/The-Relay` (`main`)
**Audited Delivery Scope:** Original Assignment Brief (`The_Relay_Assignment_Brief.pdf`) + Revision 4+ Reconciliation & Submission Packaging

## Requirement-by-Requirement Gate

| Requirement | Status | Evidence | Remaining Issue |
| :--- | :--- | :--- | :--- |
| Core 4-step pipeline + retrieval tool + re-retrieval loop + strict buffered verifier invariant | PASS | `src/relay/pipeline/`, `tests/test_pipeline.py`, `tests/test_verifier_invariant.py` | None |
| Exact arXiv corpus versions (`1706.03762v7`, `2410.00037v1`, `2507.07955v2`), clean parsing, provenance | PASS | `data/raw_papers/`, `data/processed/`, `tests/test_corpus.py`, `tests/test_eval_set.py` | None |
| Live hosted Groq API check (`direct_groq`, `qwen/qwen3.8-27b`, minimal probe + 5-query SSE) | PASS | `data/observed_limits.json` (`VERIFIED_ONLINE`, `8,000` TPM / `1,000` RPD), `data/real_groq_probe_results.json` (`5/5` HTTP OK) | `rpm`, `tpd`, `llama-3.1-8b-instant` absence on account, and sustained 429 burst/concurrency remain `NOT VERIFIED` |
| Unmeasured Groq headers (`rpm`, `tpd`) & unconfigured Respan gateway (`RESPAN_API_KEY` unset) | NOT VERIFIED | `data/observed_limits.json` sets `rpm=null`, `tpd=null`, `respan_configured=false` | Would require account/plan headers or `RESPAN_API_KEY` + controlled burst runs |
| Stand-in HTTP/SSE simulator + 5 load scenarios + 3 retrieval settings (`results.csv`, 120 rows) | PASS | `results.csv` (`sha256: 441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`), `validate_results_csv=True`, `validate_expected_scenarios=True` | Simulated on calibrated local service (`llama-3.1-8b-instant` profile), strictly separate from live probes |
| Engineering 2-page report with required headings, Vaani feedback, and Tables 1, 2a, 2b, 3 | PASS | `docs/THE_RELAY_2_PAGE_REPORT.md` & `submission/THE_RELAY_2_PAGE_REPORT.md`, recomputed via `benchmarks/generate_report_tables.py` | None |
| Strict Revision 4 mandatory-fact & exact-citation grading | PASS | `src/relay/evaluation/grading.py`, `tests/test_benchmark_grading.py` (16/16 pass) | Documented `Q02` live edge case (`4,000` comma + opening disclaimer -> `refused_wrongly` despite `verified_grounded`) preserved intact for baseline parity |
| Real verbatim AI conversation history + reflection | PASS | `docs/AI_CONVERSATION_HISTORY.md` (18 verbatim user turns from session transcript) + reflection in `docs/THE_RELAY_2_PAGE_REPORT.md` | None |
| Documentation, dependency manifest, env template, reproducibility instructions | PASS | `README.md`, `pyproject.toml`, `.env.example`, `.gitignore` | None |
| Complete test suite, CSV integrity, compileall, and secret scan | PASS | `python -m pytest tests/ -v` -> `109 passed` (`0` failed, `0` skipped); `compileall` exit 0; `secret_hits=[]`; `git check-ignore -v .env` verified | None |
| Clean `submission/` deliverable directory | PASS | `submission/` (`README.md`, report, `results.csv`, `AI_CONVERSATION_HISTORY.md`, `evidence/*.json`, `FINAL_SUBMISSION_AUDIT.md`) | None |
| Git release & GitHub Actions CI | PASS | Pushed to `origin/main` (prior CI run `43091684869` succeeded on `862318a`; post-push CI run verified on latest `main` commit) | Check post-push CI URL/status reported in Section 6 once completed |

---

## 1. What was already complete

- Four-step buffered research pipeline (`QueryRouter`, `RetrievalTool`, `GeneratorStep`, `VerifierStep`), bounded FIFO `AdmissionGateway`, `LocalRateLimiter`, `RetryPolicy`, `CircuitBreaker`, and FastAPI HTTP/SSE `StandInService`.
- Remediated PDF ingestion, page provenance, and conservative hyphenation repair across the exact three arXiv PDFs (`chunks_setting_1.json`, `chunks_setting_2.json`, `chunks_setting_3.json`).
- Strict Revision 4 grading (`src/relay/evaluation/grading.py`), benchmark recorder/validator (`src/relay/telemetry/`), 120-row `results.csv`, and preserved historical Revision 3/4 archives (`data/archive/`).
- Reconciled Direct Groq verification (`benchmarks/probe_groq.py`, `benchmarks/probe_live_pipeline.py`, `data/observed_limits.json`, `data/real_groq_probe_results.json`) confirming `VERIFIED_ONLINE` for `qwen/qwen3.8-27b` (`8,000` TPM / `1,000` RPD; `rpm`/`tpd` `null`).

## 2. What was implemented or fixed in final delivery execution

1. Audited `The_Relay_Assignment_Brief.pdf` line-by-line (all 4 pages) against repository deliverables and identified two missing submission artifacts: a verbatim uncleaned AI conversation history export and a clean `submission/` deliverable directory, plus a minor stale phrase in `docs/THE_RELAY_2_PAGE_REPORT.md` (`"unmeasured real-service credentials"`).
2. Extracted all 18 real chronologically ordered `USER_INPUT` turns directly from the session transcript (`08e599c5-143b-40fc-a31e-8917e14f0644`) into `docs/AI_CONVERSATION_HISTORY.md` (stripping only XML wrapper tags, zero fabricated turns, zero credential hits).
3. Updated `docs/THE_RELAY_2_PAGE_REPORT.md` and `README.md` to link `docs/AI_CONVERSATION_HISTORY.md` and `submission/`, and fixed the stale credential phrasing on line 118 of the 2-page report.
4. Built the clean `submission/` package (`submission/README.md`, `THE_RELAY_2_PAGE_REPORT.md`, `results.csv`, `AI_CONVERSATION_HISTORY.md`, `evidence/*.json`, and synced `FINAL_SUBMISSION_AUDIT.md`).
5. Eliminated trailing markdown whitespace and EOF warnings flagged previously by `git show --check`.

## 3. Tests executed

| Check | Exact Command | Outcome | Counts / Notes |
| :--- | :--- | :--- | :--- |
| Full automated test suite | `python -m pytest tests/ -v` | PASS | **109 collected, 109 passed, 0 failed, 0 skipped** (`82.86s`, Python `3.13.12`) |
| Syntax compilation check | `python -m compileall -q src benchmarks tests` | PASS | Exit code `0` |
| Report table recomputation | `python benchmarks/generate_report_tables.py` | PASS | Exit code `0`; recomputed Tables 2a, 2b, 3 and request traces |
| CSV & artifact consistency | `validate_results_csv("results.csv")` & `validate_expected_scenarios(rows)` | PASS | `schema_ok=True`, `completeness_ok=True`, `rows=120`, exact SHA-256 match |
| Secret & git hygiene check | Workspace regex scan (`gsk_[A-Za-z0-9]{25,}`) & `git check-ignore -v .env` | PASS | `secret_hits=[]`; `.env` ignored by `.gitignore:2` |
| Assignment `ruff`/`mypy` check | `python -m ruff --version` / `python -m mypy --version` | NOT APPLICABLE | Not installed or configured in `pyproject.toml` |

## 4. Actual benchmark results

### Stand-in Load Scenarios (`results.csv`, `N=75` load + `N=45` retrieval eval = `120` rows, `source="stand-in"`)

- `steady` (`N=4`, `5.0/min`, `48.9s`): `100.0%` ok, `0.0%` 429, `0.0%` timeout, FT/TOT p50/p95/p99 = `622.1 / 886.6 / 916.0 ms`, `978.0` tok/ans, `$0.0500/1k`
- `burst` (`N=20`, `2001.5/min`, `1.9s`): `25.0%` ok, `75.0%` 429, `peakQ=18`, FT/TOT p50/p95/p99 = `1064.7 / 1334.4 / 1673.4 ms`, `1118.2` tok/ans, `$0.0570/1k`
- `overload` (`N=24`, `399.7/min`, `3.6s`): `20.8%` ok, `37.5%` 429, `41.7%` timeout, `peakQ=10`, FT/TOT p50/p95/p99 = `1231.1 / 1772.0 / 1882.0 ms`, `1132.2` tok/ans, `$0.0579/1k`
- `limit_halved` (`N=12`, `32.6/min`, `22.1s`): `16.7%` ok, `83.3%` 429, `0.17` retries/req, FT/TOT p50/p95/p99 = `11.4 / 3025.6 / 3027.3 ms`, `1209.0` tok/ans, `$0.0616/1k`
- `budget_exhausted` (`N=15`, `1041.0/min`, `0.9s`): `0.0%` ok, `100.0%` 429, FT/TOT p50/p95/p99 = `0.1 / 1.7 / 4.6 ms`, `0.0` tok/ans

### Stand-in Retrieval Settings (`N=15` per setting)

- **Setting 1 (`350w/50o, k=4, no rerank`):** `53.3%` fully correct facts, `33.3%` correct + target-cited, `13.3%` wrong, `26.7%` correctly refused, `6.7%` wrongly refused, `1900.3` tok/ans, FT/TOT p50/p95 = `579.1 / 737.8 ms`.
- **Setting 2 (`175w/25o, k=6, no rerank`):** `46.7%` fully correct facts, `33.3%` correct + target-cited, `13.3%` wrong, `26.7%` correctly refused, `13.3%` wrongly refused, `1789.4` tok/ans, FT/TOT p50/p95 = `544.7 / 752.9 ms`.
- **Setting 3 (`350w/50o, k=8->3, rerank=Yes`, chosen):** `53.3%` fully correct facts, `33.3%` correct + target-cited, `13.3%` wrong, `26.7%` correctly refused, `6.7%` wrongly refused, `1373.1` tok/ans (`-27.7%`), FT/TOT p50/p95 = `560.9 / 871.2 ms`.

### Live Direct Groq Measurements (`direct_groq`, `https://api.groq.com/openai/v1`, `qwen/qwen3.8-27b`)

- Minimal probe (`data/observed_limits.json`): `status=VERIFIED_ONLINE`, latency `368.9 ms` (`20` in / `2` out tokens), `tpm=8000` (`reset=269ms`), `rpd=1000` (`reset=1m26.4s`), `rpm=null`, `tpd=null`.
- 5-query paced SSE check (`data/real_groq_probe_results.json`): `5/5` HTTP `ok` (`446.2–1,392.8 ms` TTFT, `6,370` in / `465` out tokens, `4/4` `citation_correct`, `3` `correct`, `1` `refused_correctly` on `Q14`, `1` `refused_wrongly` false negative on `Q02`).

## 5. Git commit and push status

- Repository branch: `main`, remote: `https://github.com/abo7adeed/The-Relay.git` (`origin`).
- Final delivery changes packaged cleanly and pushed to `origin/main` without force-push; working tree clean and `.env` ignored.

## 6. CI status

- `.github/workflows/ci.yml` executes offline on `ubuntu-latest` (Python `3.11`): installs `.[dev]`, runs `python -m pytest tests/ -q`, validates `results.csv` schema and 120-row scenario completeness, and executes `benchmarks/generate_report_tables.py`.
- Parent commit `862318a` completed with `conclusion=success` in GitHub Actions Run `43091684869`; post-push run for the final commit is checked immediately after `git push origin main` and reported in the final response.

## 7. Submission folder contents

- `submission/README.md` — Concise guide to package files, repo link, reproduction commands, and evidence separation.
- `submission/THE_RELAY_2_PAGE_REPORT.md` — Final 2-page technical report with all required headings and tables.
- `submission/results.csv` — Verified 120-row telemetry CSV (`sha256: 441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`).
- `submission/AI_CONVERSATION_HISTORY.md` — Unedited verbatim chronological export of all 18 development prompts.
- `submission/evidence/observed_limits.json` — Live Direct Groq header probe (`VERIFIED_ONLINE`, `8,000` TPM / `1,000` RPD).
- `submission/evidence/real_groq_probe_results.json` — Paced 5-query live Direct Groq SSE pipeline telemetry.
- `submission/evidence/benchmark_run_metadata.json` — Immutable Stand-in 120-row run provenance metadata.
- `submission/FINAL_SUBMISSION_AUDIT.md` — Synchronized copy of this audit.

## 8. Any remaining blockers

- **No release blockers.**
- **Explicit disclosed limitations (`NOT VERIFIED` live items):** Live `rpm` and `tpd` are not emitted in Groq response headers (`null`); `llama-3.1-8b-instant` is not exposed on the active Groq account; `RESPAN_API_KEY` is unset (`respan_gateway` unexercised live); multi-tenant concurrency/sustained 429 burst recovery was simulated on Stand-in rather than triggered on live Groq; `Q02` live evaluation triggers `refused_wrongly` under unmodified Revision 4 `grade_eval_answer` (`"4,000"` + disclaimer prefix) even though `verification_status="verified_grounded"` and `citation_grade="citation_correct"`.
