# The Relay — Research Assistant Pipeline & Production Load Benchmarking

A safety-first, four-step agentic research assistant and production load-benchmarking harness built in Python and FastAPI over three exact arXiv papers. Draft outputs are buffered in memory, verified against retrieved passage provenance and numerical consistency before release, and protected by a bounded FIFO admission gateway, local token-bucket rate limiter, jittered retry budget, circuit breaker, and a controllable FastAPI HTTP/SSE stand-in model service.

---

## Authoritative Paper Library

All answers are grounded strictly in these exact arXiv versions:

1. **Attention Is All You Need** (`arXiv:1706.03762v7`)
2. **Moshi: a speech-text foundation model for real-time dialogue** (`arXiv:2410.00037v1`)
3. **Dynamic Chunking for End-to-End Hierarchical Sequence Modeling (H-Net)** (`arXiv:2507.07955v2`)

If retrieved passages do not substantiate a claim (or fail numerical/citation verification), the pipeline refuses safely instead of guessing.

---

## Repository Structure

```text
The-Relay/
├── .env.example                        # Template for optional local provider credentials (never commit .env)
├── .gitignore                          # Excludes .env, caches, virtualenvs, IDE/OS artifacts
├── pyproject.toml                      # Src-layout packaging, dependencies, and pytest config
├── README.md                           # Setup, architecture, and reproduction guide
├── results.csv                         # Validated raw per-request benchmark telemetry (120 rows)
├── The_Relay_Assignment_Brief.pdf      # Original assignment brief
│
├── data/
│   ├── eval_questions.json             # 15 questions with explicit mandatory expected_facts
│   ├── observed_limits.json            # Live Groq probe status (currently unverified)
│   ├── benchmark_run_metadata.json     # Run timestamp, code hash, CSV hash, scenario counts
│   ├── archive/                        # Original results, 401 probe log, run logs, old/new tables
│   ├── raw_papers/                     # Cached exact-version arXiv PDFs
│   └── processed/                      # Deterministic chunk corpora (Settings 1, 2, 3)
│
├── docs/
│   ├── THE_RELAY_2_PAGE_REPORT.md      # Final 2-page technical report & corrected tables
│   ├── REVISION_4_IMPLEMENTATION_REPORT.md  # P0 remediation, evidence, commands, residual risks
│   ├── IMPLEMENTATION_PLAN.md          # Historical Revision 3 blueprint (superseded)
│   └── PHASE_2_2_IMPLEMENTATION_REPORT.md   # Historical Phase 2.2 record (superseded)
│
├── src/relay/
│   ├── config.py                       # Validated Pydantic settings and safety invariant
│   ├── corpus/                         # fetcher.py, parser.py, chunker.py
│   ├── evaluation/                     # grading.py (strict mandatory-fact & citation grading)
│   ├── retrieval/                      # bm25_index.py, reranker.py (Hybrid RRF), tool.py
│   ├── models/                         # base.py, groq_provider.py, standin_service.py (FastAPI SSE)
│   ├── pipeline/                       # router.py, retriever_step.py, generator_step.py, verifier_step.py, engine.py, state.py
│   ├── gateway/                        # gateway.py (FIFO + TTL), rate_limiter.py, retry_policy.py, circuit_breaker.py
│   └── telemetry/                      # recorder.py, validator.py
│
├── benchmarks/
│   ├── probe_groq.py                   # Minimal live provider header/limit probe
│   ├── run_benchmarks.py               # Runs Table 3 retrieval eval + 5 load scenarios -> results.csv
│   └── generate_report_tables.py       # Recomputes Tables 2a, 2b, 3 and request traces from results.csv
│
└── tests/                              # 109 automated unit, regression, safety, and parity tests
```

---

## System Highlights

- **4-Step Buffered Pipeline:** `QueryRouter` → `RetrievalTool` (`retrieve_passages`) → buffered `GeneratorStep` (records `model_first_token_ms`) → `VerifierStep` (enforces exact page equality, numeric claim consistency, and max-1 re-retrieval loop before releasing output at `user_first_token_ms`).
- **Admission & Failure Isolation:** Bounded `asyncio.Queue` FIFO admission control, client TTL abandonment tracking, 85% TPM quota-pressure context shedding (`top_k` reduction without skipping verification), 15% global retry budget with full jitter (`retry_wait_ms` isolated from `working_ms`), and fail-fast `CircuitBreaker` on daily quota exhaustion (`0` retries, `0` tokens).
- **Controlled Retrieval Experiments (`Table 3`, Revision 4 strict grading):**
  - **Setting 1** (`350w / 50o, k=4`, BM25): `53.3%` fully correct facts (8/15; 72.7% of 11 answerable), `33.3%` correct + exact target citation, `13.3%` wrong, `26.7%` correctly refused, `6.7%` wrongly refused, `1,900.3` avg tokens/answer.
  - **Setting 2** (`175w / 25o, k=6`, BM25): `46.7%` fully correct facts (7/15), `33.3%` correct + cited, `13.3%` wrong, `26.7%` correctly refused, `13.3%` wrongly refused, `1,789.4` avg tokens/answer.
  - **Setting 3** (`350w / 50o, k=8->3`, Hybrid BM25 + subword TF-IDF RRF): `53.3%` fully correct facts (8/15), `33.3%` correct + cited, `13.3%` wrong, `26.7%` correctly refused, `6.7%` wrongly refused, **`1,373.1` avg tokens/answer** (-27.7% token cost at parity with Setting 1).
  - **Grading semantics:** an answer is `correct` only when **every** configured mandatory fact is present. Partial answers are `wrong` and are never counted as correct. Citation grading separately checks exact target-citation presence; it is **not** a per-response evidence-quotation or entailment check.

---

## Quickstart & Verification

### 1. Setup Environment
```bash
pip install -e ".[dev]"
```

*(Optional — only needed if probing the live Groq Cloud API; **never commit `.env`**)*:
```bash
cp .env.example .env
# Edit .env and set GROQ_API_KEY=<your-free-tier-key>
```
*(In PowerShell on Windows: `Copy-Item .env.example .env`)*

### 2. Run the Full Test Suite
```bash
python -m pytest tests/ -v
```
All **109 tests** verify PDF parsing/provenance, hyphenation repair, verbatim quote alignment, bounded FIFO queueing, TTL expiry, retry latency isolation, Stand-in SSE/429 parity, Hybrid RRF reranking, strict verifier invariants, strict mandatory-fact grading, and benchmark CSV completeness/duplicate/latency integrity.

### 3. Run Benchmarks & Reproduce Report Tables
```bash
# 1. Optional live provider probe (updates data/observed_limits.json; never required for stand-in benchmarks)
python benchmarks/probe_groq.py

# 2. Run Retrieval Evaluation (Settings 1-3) + Load Scenarios (steady, burst, overload, limit_halved, budget_exhausted)
python benchmarks/run_benchmarks.py

# 3. Validate latency conservation and print Tables 2a, 2b, 3 + request traces from results.csv
python benchmarks/generate_report_tables.py

# 4. Validate CSV schema, duplicate IDs, scenario completeness, and latency conservation only
python -c "from relay.telemetry.validator import validate_results_csv; ok, errs = validate_results_csv('results.csv'); print('valid=', ok); print(errs); raise SystemExit(0 if ok else 1)"
```

**Live-provider status:** committed `data/observed_limits.json` remains `NOT_VERIFIED_MISSING_CREDENTIALS` because no successful authenticated probe was verified by this remediation. All reported latency/outcome metrics are **measured on the local stand-in**; real Groq metrics are **not measured**. A provisional `.env` key returned HTTP 401; that failed attempt is preserved under `data/archive/` and is not treated as verification.

---

## Key Documentation
- **[2-Page Technical Report](docs/THE_RELAY_2_PAGE_REPORT.md):** Complete engineering write-up, corrected tables, latency decomposition, failure case studies, and Vaani feedback.
- **[Revision 4 Implementation Report](docs/REVISION_4_IMPLEMENTATION_REPORT.md):** P0 grading defect, before/after evidence, file-by-file changes, exact commands, residual risks, and final status.
- **[Implementation Plan (Revision 3, historical)](docs/IMPLEMENTATION_PLAN.md):** Original evidence-driven blueprint; superseded by Revision 4.
- **[Raw Benchmark Telemetry](results.csv):** Per-request metrics (`120` rows) validated with `|total_ms - (queue_wait_ms + working_ms + retry_wait_ms)| < 15 ms`.
