# Revision 4 Implementation Report — Benchmark Integrity Remediation

**Date:** 2026-11-11  
**Repository:** `E:\Ai-projects\vaaniresearch-assignment`  
**Scope:** P0 benchmark-grading defect, benchmark integrity/reproducibility, focused regression tests, table regeneration, documentation/CI updates.  
**Final status (point-in-time at Revision 4 commit `862318a`):** **PASS** (local Stand-in evidence; see **Section 9: Post-Push Final Submission Reconciliation** for live Direct Groq verification and GitHub Actions CI run `43091684869` completed afterward).

---

## 1. Executive summary

Revision 3 contained a P0 grading defect in `benchmarks/run_benchmarks.py`:

```python
if all(k in resp_l for k in keys):
    return "correct"
if any(k in resp_l for k in keys):
    return "correct"
```

The second branch made **any single expected fact sufficient** for a `correct` grade, and matching was raw substring matching. As a result, partial answers were reported as correct and the Table 3 accuracy figures were inflated. Revision 4 removes the `any()` acceptance path, moves grading into a tested module, requires **all** mandatory facts, rejects embedded numeric substrings, fails loudly on missing/empty fact configuration, preserves refusal grading, and regenerates the entire benchmark and report tables from the corrected code.

**Verified result:** `109 passed` tests; a fresh 120-row benchmark run; corrected Table 3 with `53.3% / 46.7% / 53.3%` fully correct facts and `33.3%` correct + target-cited for Settings 1/2/3, with `13.3%` wrong in every setting instead of the previously reported `0.0%`.

---

## 2. The defect, before and after

### 2.1 Before (Revision 3, committed `HEAD`)

- Expected facts lived in a hard-coded dictionary inside `run_benchmarks.py`, not in the audited eval data.
- A response was `correct` if `all(...)` **or** `any(...)` expected fact appeared as a lowercase substring.
- Consequences:
  - `Q06` (needs `160` and `80`) was graded correct from a response containing only `160`.
  - `Q09` (needs `4x` and `3.6x`) was graded correct from a response containing only `4x`.
  - `Q05` (needs `1.1` and `12.5`) could be graded correct from a partial answer.
  - Numeric substring collisions were possible (`1.1` inside `11.1` or `1.10`; `4000` inside `14000`).
  - An empty or missing fact list silently accepted everything (the `all([])` result).

### 2.2 After (Revision 4)

`src/relay/evaluation/grading.py` now:

1. Reads explicit `expected_facts` arrays from `data/eval_questions.json`.
2. Requires **every** mandatory fact for an answerable question to be present.
3. Uses numeric-aware boundaries:
   - `(?<![\d.])fact(?![\d.])` for numeric facts;
   - alphanumeric word boundaries for plain text facts.
4. Normalizes case, PDF decimal spacing (`0 . 1` → `0.1`), and multiplication notation (`4 ×` → `4x`).
5. Raises `ValueError` when an answerable question has missing, empty, or non-string expected facts. It never treats an empty set as “all facts present.”
6. Preserves trap semantics: false-friend/unanswerable questions are `refused_correctly` only when refused; a non-refusal is `wrong`.
7. Preserves answerable-question refusal semantics: refusal or non-grounded status is `refused_wrongly`.
8. Produces a **separate** `citation_grade` that checks exact target-citation presence. This is explicitly **not** per-response evidence-quotation or entailment verification.

### 2.3 Focused regression coverage

`tests/test_benchmark_grading.py` covers:

- all facts present → correct;
- only one of multiple facts → wrong;
- no facts present → wrong;
- empty and missing `expected_facts` configuration → loud `ValueError`;
- correct trap refusal → `refused_correctly`;
- non-refusal on a trap → `wrong`;
- refusal of an answerable question → `refused_wrongly`;
- embedded numeric/word collisions (`11.1`, `1.10`, `14000`, `DNase`);
- real `Q09` partial vs. complete answer;
- real `Q05` single-fact vs. complete answer;
- exact target citation, wrong-page citation, missing citation, and trap N/A.

---

## 3. Evidence preservation

The original evidence was preserved before any overwrite:

| Artifact | Preserved at | SHA-256 / note |
| :--- | :--- | :--- |
| Original Revision 3 results | `data/archive/results_rev3_original.csv` | `B340188A024FFB96BCCE0B43C569F57AFA14F376A70B6C8BEEDFDA82595D902D` |
| Copy made by the final benchmark run | `data/archive/results_20261111T191253Z.csv` | `b2f0a1c0b772e478974b81e25dba88fba3e5075aec561ea9feaac1cd283a29f5` |
| Original Table 3 output | `data/archive/report_tables_rev3_original.txt` | old `66.7% / 60.0%` table |
| Failed HTTP 401 probe | `data/archive/groq_probe_rev4_401.txt`, `data/archive/observed_limits_probe_failed_20261111T191057Z.json` | provisional `.env` key rejected; **not** verification |
| Final benchmark log | `data/archive/benchmark_run_rev4_final.txt` | 120 rows, validation passed |
| Final test log | `data/archive/pytest_rev4_final.txt` | `109 passed` |
| Final tables/traces | `data/archive/report_tables_rev4_final.txt` | regenerated from final CSV |

The run metadata is in `data/benchmark_run_metadata.json` and records the code commit, Python version, benchmark/eval/CSV hashes, scenario counts, grade counts, and the live-provider status.

**Externally appearing artifacts were not adopted as evidence:** `data/real_groq_probe_results.json`, `benchmarks/probe_live_pipeline.py`, and the uncommitted Respan gateway integration (`src/relay/models/respan_gateway.py`) were left in place, untouched, and are not the source of any number or claim in the report. No successful live probe was performed by this remediation.

---

## 4. File-by-file changes

### Grading and eval data

- `src/relay/evaluation/grading.py` (new): strict all-facts grading, numeric/word-boundary matching, loud config validation, refusal semantics, exact-citation grading.
- `src/relay/evaluation/__init__.py` (new): package export.
- `data/eval_questions.json`: every answerable question now has an explicit `expected_facts` array; traps have an intentional empty array. `Q05`, `Q06`, and `Q09` are no longer implicitly gradable from one token.
- `benchmarks/run_benchmarks.py`: imports the shared grader; no inline `any()` path; records `answer_grade` and `citation_grade`; validates exactly 120 rows and the scenario matrix before replacing `results.csv`.
- `benchmarks/generate_report_tables.py`: Table 3 now reports fully correct facts, exact target-citation rate, correct + target-cited, wrong, correctly refused, and wrongly refused. It validates schema/completeness before computing and uses the final CSV’s traces.

### Benchmark reproducibility and telemetry

- `src/relay/telemetry/recorder.py`: rejects duplicate `request_id`s; writes a pending CSV first and replaces atomically; repeated `save()` does not append duplicates.
- `src/relay/telemetry/validator.py`: schema checks, duplicate-ID checks, exact scenario/row completeness checks, and latency-conservation checks.
- `benchmarks/run_benchmarks.py`: archives previous results with a timestamp before replacement; writes run metadata with hashes and counts.

### Correctness fixes found during the audit

- `src/relay/gateway/retry_policy.py`: off-by-one corrected so `max_attempts=2` means two total calls (one retry), not three.
- `src/relay/retrieval/reranker.py`: corpus-signature cache invalidation and empty-query handling.
- `src/relay/models/groq_provider.py`: timeout propagation, HTTP error mapping, truncated-stream detection.
- `src/relay/models/standin_service.py`: dataclass config, in-place reset, isolation between runs.
- `src/relay/config.py`: validated settings, positive bounds, percentage bounds, and an invariant that verification cannot be disabled.
- Import standardization from `src.relay.*` to `relay.*`; `pyproject.toml` now has a proper setuptools src-layout and pytest configuration; `tests/conftest.py` makes the src layout importable.

### Tests

New files:

- `tests/test_benchmark_grading.py` — 16 collected grading regressions (12 test functions, 4 parametrized).
- `tests/test_benchmark_integrity.py` — recorder/validator/archive/completeness regressions.
- `tests/test_config.py` — settings validation and safety invariant.
- `tests/test_retrieval_edge_cases.py` — reranker/cache/empty-query edge cases.

Modified tests cover retry accounting, TTL behavior, verifier invariants, Stand-in parity, provider errors, pipeline imports, and eval-set provenance.

### Documentation and CI

- `README.md`: corrected root layout, 109-test count, strict Table 3 semantics/numbers, Revision 4 report link, and live-status qualifier.
- `docs/THE_RELAY_2_PAGE_REPORT.md`: new Table 2a/2b/3 numbers, new trace IDs, strict grading explanation, retry off-by-one note, and honest citation limitation.
- `docs/IMPLEMENTATION_PLAN.md` and `docs/PHASE_2_2_IMPLEMENTATION_REPORT.md`: historical/superseded banners with links to current evidence.
- `.github/workflows/ci.yml` (new): install, full test suite, CSV schema/completeness/latency validation, and table recomputation. It does **not** run the live probe or the slow benchmark.

---

## 5. Exact verification commands and outputs

### 5.1 Test suite

```bash
python -m pytest tests/ -q
```

Observed:

```text
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 79.97s (0:01:19)
```

### 5.2 Full benchmark rerun

```bash
python benchmarks/run_benchmarks.py
```

Observed:

```text
[Retrieval Eval] Running setting_1_bm25_base across 15 questions...
[Retrieval Eval] Running setting_2_bm25_granular across 15 questions...
[Retrieval Eval] Running setting_3_hybrid_rerank across 15 questions...
[Load Scenario] Running 'steady': N=4, interval=16.0s, ttl=10.0s, C=3...
[Load Scenario] Running 'burst': N=20, interval=0.02s, ttl=2.2s, C=2...
[Load Scenario] Running 'overload': N=24, interval=0.15s, ttl=1.2s, C=1...
[Load Scenario] Running 'limit_halved': N=12, interval=2.0s, ttl=4.0s, C=2...
[Load Scenario] Running 'budget_exhausted': N=15, interval=0.05s, ttl=10.0s, C=3...

Saved 120 rows to results.csv. Validation passed: True
Scenario counts: {'budget_exhausted': 15, 'burst': 20, 'limit_halved': 12, 'overload': 24, 'retrieval_eval': 45, 'steady': 4}
Expected total: 120
Previous results preserved at data/archive\results_20261111T191253Z.csv (sha256=b2f0a1c0b772e478974b81e25dba88fba3e5075aec561ea9feaac1cd283a29f5)
Run metadata written to data/benchmark_run_metadata.json
```

Final `results.csv` SHA-256: `441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`  
Maximum observed latency-conservation error: `0.195 ms` (threshold `15 ms`).

### 5.3 CSV validation

```bash
python -c "import csv; from relay.telemetry.validator import validate_results_csv, validate_expected_scenarios; ok, errs = validate_results_csv('results.csv'); rows = list(csv.DictReader(open('results.csv', encoding='utf-8', newline=''))); complete, cerrs = validate_expected_scenarios(rows); print('schema_ok=', ok); print('completeness_ok=', complete); print('rows=', len(rows)); print('errors=', errs[:5], cerrs[:5])"
```

Observed:

```text
schema_ok= True
completeness_ok= True
rows= 120
errors= [] []
```

### 5.4 Table regeneration

```bash
python benchmarks/generate_report_tables.py
```

Final Table 3:

| Setting | N | Fully correct facts | Correct + target-cited | Wrong | Correctly refused | Wrongly refused | Tokens/answer |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1: 350w/50o, k=4, no rerank | 15 | 53.3% | 33.3% | 13.3% | 26.7% | 6.7% | 1900.3 |
| 2: 175w/25o, k=6, no rerank | 15 | 46.7% | 33.3% | 13.3% | 26.7% | 13.3% | 1789.4 |
| 3: 350w/50o, k=8→3, rerank | 15 | 53.3% | 33.3% | 13.3% | 26.7% | 6.7% | 1373.1 |

Per-question outcome changes relative to Revision 3:

- `Q06` is now `wrong` in all three settings (missing `80`).
- `Q09` is now `wrong` in all three settings (missing `3.6x`).
- `Q05` is correct in Settings 1 and 3 but wrongly refused in Setting 2 (chunk-boundary context loss).
- `Q10` is wrongly refused in all three settings.
- Traps `Q12–Q15` remain correctly refused in all settings.

---

## 6. Live Groq status

`data/observed_limits.json` is deliberately restored to:

```json
{
  "status": "NOT_VERIFIED_MISSING_CREDENTIALS",
  "probe_successful": false,
  "observed_limits": {"rpm": null, "tpm": null, "rpd": null, "tpd": null, "recovery_window_seconds": null}
}
```

No successful authenticated probe was completed or independently verified by this remediation. A provisional local `.env` key was attempted and returned **HTTP 401 Invalid API Key**; that failed attempt is archived and is not counted as verification. A separate externally appearing artifact (`data/real_groq_probe_results.json`) was not produced or validated here and is not used as evidence. Therefore:

- Published/documented limits are reported as **documented**.
- Live limits are reported as **not measured**.
- All latency/outcome tables are **measured on the local stand-in**, not on Groq.
- No `VERIFIED_ONLINE` claim is made anywhere in the committed evidence.

The external `data/real_groq_probe_results.json` artifact is not used in the report; it was not produced by a verified run in this remediation.

---

## 7. Remaining risks and limitations

1. **Citation grading is not entailment verification.** `citation_grade` only checks exact target-citation identity. It does not prove that each sentence is supported by the quoted passage. Corpus quotation provenance is tested separately, and the runtime verifier checks fetched-passage provenance and numeric consistency, but those are not equivalent to full per-response entailment.
2. **No real-Groq calibration.** Queue saturation, provider `retry-after` behavior, and real multi-tenant noise remain unmeasured. The stand-in is a calibrated simulation, not Groq.
3. **Benchmark nondeterminism.** Latency percentiles vary between runs. Accuracy/citation outcomes were stable across the two Revision 4 runs, but the tables are a single final run; the raw CSV and run metadata are the source of truth.
4. **`refused_wrongly` in aggregate metadata includes load-scenario non-answers.** Table 3 uses only the 45 `retrieval_eval` rows. The 120-row grade counts in `benchmark_run_metadata.json` are aggregate and should not be read as retrieval accuracy.
5. **CI workflow is new and has not executed on GitHub.** Local commands were executed successfully; remote runner execution is pending the next push.

---

## 8. Final verdict

**PASS** for the Revision 4 remediation scope, with the explicit limitations above.

The P0 grading defect is fixed and regression-tested; partial answers can no longer be graded correct; empty configuration fails loudly; refusal grading is preserved; the original evidence is archived; a complete 120-row benchmark was rerun and validated; tables and the 2-page report were regenerated with corrected semantics; documentation/CI were updated; and live Groq remained unverified at commit `862318a` until the post-push reconciliation below.

---

## 9. Post-push final submission reconciliation

After Revision 4 (`862318a`) was pushed:

1. **GitHub Actions CI verified:** Workflow run `43091684869` (`ubuntu-latest`, Python 3.11) passed all steps on `862318a` (`pytest tests/ -q`, `results.csv` validation, `generate_report_tables.py`).
2. **Pre-reconciliation probe snapshots preserved:** `data/observed_limits.json` and `data/real_groq_probe_results.json` from Revision 4 were copied unchanged to `data/archive/observed_limits_rev4_pre_reconcile.json` and `data/archive/real_groq_probe_results_rev4_pre_reconcile.json`.
3. **Provider routing & live verification reconciled:** `benchmarks/probe_groq.py` and `benchmarks/probe_live_pipeline.py` were updated to select `direct_groq` (`https://api.groq.com/openai/v1`, `GROQ_API_KEY`) when `RESPAN_API_KEY` is absent, while preserving optional `respan_gateway` (`https://gateway.respan.ai/v1`). Using the updated local `.env` `GROQ_API_KEY`, live probing verified `qwen/qwen3.8-27b` (`VERIFIED_ONLINE`, `8,000` TPM / `1,000` RPD observed; `rpm`/`tpd` unverified `null`; `5/5` SSE pipeline requests OK).
4. **Stand-in vs. live separation preserved:** `results.csv` (120 rows) and `data/benchmark_run_metadata.json` remain pure local Stand-in evidence (`llama-3.1-8b-instant` profile). See [`FINAL_SUBMISSION_AUDIT.md`](FINAL_SUBMISSION_AUDIT.md) for the complete post-Revision-4 release audit.
