# The Relay: ML Systems Engineering Implementation Plan & Technical Blueprint

> **HISTORICAL / SUPERSEDED (2026-11-11).** This is the Revision 3 blueprint retained for provenance. It is not the current status or evidence record. Revision 4 fixed the P0 benchmark-grading defect, regenerated the 120-row benchmark, corrected Table 3 semantics, and documented residual risks. See [`REVISION_4_IMPLEMENTATION_REPORT.md`](REVISION_4_IMPLEMENTATION_REPORT.md) and the current [`THE_RELAY_2_PAGE_REPORT.md`](THE_RELAY_2_PAGE_REPORT.md). Numbers below may describe pre-remediation hypotheses or the old `any()` grading.

**Document Version:** Revision 3 — Evidence-Driven Implementation Plan  
**Role:** Senior Backend / AI Systems Engineer  
**Assignment:** The Relay — Research Assistant Pipeline & Production Load Benchmarking  
**Status:** Planning Phase Complete — Awaiting Approval Before Implementation  
**Target File:** `docs/IMPLEMENTATION_PLAN.md`  

---

## 1. Revision 3 Summary of Changes

This revision transforms the plan into an evidence-driven, testable, and scope-disciplined blueprint following a rigorous engineering review:

1. **Groundedness & Generation Safety:** Removed unverified streaming to users. The pipeline now generates and buffers the answer internally, executes mandatory citation verification against retrieved passages, and releases the response to the user only after verification succeeds. If verification fails or evidence is insufficient, the system safely falls back to a structured refusal.
2. **Timing & Telemetry Disambiguation:** 
   - Introduced `model_first_token_ms` (time to first internal model token) vs. `user_first_token_ms` (time until the client receives output).
   - Redefined `total_ms` as an independently measured monotonic wall-clock timer (`time.perf_counter()`), rather than a tautological sum.
   - Explicitly isolated `queue_wait_ms`, `working_ms` (including failed attempt runtimes), and `retry_wait_ms` (pure backoff sleep).
3. **Strict Limit Categorization:** Separated all provider limits into **Documented**, **Observed** (measured via live probes), and **Simulated** (stand-in engine). Avoided treating unmeasured assumptions as facts.
4. **Realistic Token Budgeting:** Formulated token consumption per question across pipeline stages (Router, Draft Generator, Verifier) as an **Estimated Hypothesis** ($\sim 1,500\text{--}3,000$ tokens/question) rather than an assertion. Benchmarks are now dimensioned in **questions per minute** and guarded against blowing Groq free-tier quotas.
5. **Conservative Routing:** Eliminated brittle keyword-based out-of-distribution (OOD) dropping. The router acts conservatively; negative and cross-paper traps are adjudicated by the evidence-based verifier.
6. **Evaluation Dataset Evidence Audit:** Audited benchmark questions Q01–Q15 against paper claims, flagged claims requiring exact PDF confirmation (e.g., Q06 Moshi acoustic delay and Q09 DNA data efficiency), and marked unverified citations honestly as `[Pending PDF Verification]`.
7. **Scope Discipline:** Retained simple, robust in-process async architecture (FastAPI, bounded FIFO queue, BM25 retrieval). Deferred Cross-Encoder re-ranking, distributed queues, and priority scheduling until empirically justified.
8. **Essential Acceptance Tests:** Codified the three non-negotiable acceptance tests: `test_retry_accounting`, `test_ttl_behavior`, and `test_verifier_invariant`.

---

## 2. System Architecture & Request Lifecycle

### 2.1 Safe Request Lifecycle
To prevent any possibility of emitting unverified hallucinations, draft text is strictly buffered inside the service. The user receives output only after the Grounding Verifier certifies the citations.

```
                                  [ Client / Benchmark Harness ]
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                             ADMISSION & TRAFFIC CONTROL GATEWAY                             │
│                                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Bounded FIFO Queue: asyncio.Queue(maxsize=K)                                          │  │
│  │ - Preserves strict First-In, First-Out arrival order (avoids starvation)              │  │
│  │ - Rejects traffic with HTTP 429/503 when queue is full                                │  │
│  └───────────────────────────────────────────┬───────────────────────────────────────────┘  │
│                                              │                                              │
│  ┌───────────────────────────────────────────▼───────────────────────────────────────────┐  │
│  │ Request Deadline / TTL Monitor (Hypothesis: client_ttl_s = 10.0s)                     │  │
│  │ - Assigns deadline = t0 + TTL; aborts expired requests before model dispatch          │  │
│  └───────────────────────────────────────────┬───────────────────────────────────────────┘  │
│                                              │                                              │
│  ┌───────────────────────────────────────────▼───────────────────────────────────────────┐  │
│  │ Concurrency Gate: asyncio.Semaphore(C_max)                                           │  │
│  │ - Restricts active model calls to upstream concurrency limit                          │  │
│  │ - Token-Bucket Local Rate Limiter (tracks local RPM / TPM consumption)                │  │
│  └───────────────────────────────────────────┬───────────────────────────────────────────┘  │
└──────────────────────────────────────────────┼──────────────────────────────────────────────┘
                                               │ Admitted Request
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                            AGENTIC RESEARCH PIPELINE (4 STEPS)                              │
│                                                                                             │
│  [Step 1: Conservative Query Router] ────────────────────────────────────────────────────┐  │
│  - Extracts query terms and candidate target paper(s)                                    │  │
│  - Does NOT hard-drop queries via heuristic keywords; passes ambiguous queries down      │  │
│                                                                                          │  │
│  [Step 2: Passage Retrieval Tool (retrieve_passages)] ───────────────────────────────────┤  │
│  - Controlled lexical retrieval (BM25 baseline) over parsed paper chunks                 │  │
│  - Returns top-k passages with provenance: [Paper, Section, Page, Chunk ID]              │  │
│  - Under high token pressure: dynamically reduces k (e.g. 4 -> 2) to shed context tokens │  │
│                                                                                          │  │
│  [Step 3: Internal Draft Generation (Buffered)] ─────────────────────────────────────────┤  │
│  - Model call (Groq Cloud or Stand-in)                                                   │  │
│  - Generates draft response citing [Paper, Section, Page]                                │  │
│  - Output is BUFFERED IN MEMORY (never streamed to user at this step)                    │  │
│  - Records model_first_token_ms                                                          │  │
│                                                                                          │  │
│  [Step 4: Citation Verification & Safety Guard (MANDATORY INVARIANT)] ───────────────────┘  │
│  - Evaluates draft against retrieved passages:                                              │
│    Case A: Citations verified & assertions supported -> Release response to user.           │
│    Case B: Unsubstantiated claims / missing citation -> Re-retrieve once (max 1 loop).       │
│    Case C: Re-retrieve fails / unanswerable / missing evidence -> Emit safe refusal.        │
│    Case D: Verifier timeout / provider error -> FAIL SAFE (emit structured refusal).         │
└──────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                               │ Verified Output or Safe Refusal
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 CLIENT DELIVERY & TELEMETRY                                 │
│  - Emits final answer or refusal to user (records user_first_token_ms)                      │
│  - Telemetry logs all timing metrics, token counts, and outcomes to results.csv             │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Python Project Structure
```
vaani-the-relay/
├── pyproject.toml                     # Python dependencies (fastapi, uvicorn, httpx, rank-bm25, pymupdf)
├── README.md                          # Quickstart, setup, and replication guide
├── .env.example                       # GROQ_API_KEY template
├── results.csv                        # Raw experimental log file (MANDATORY ARTIFACT)
│
├── data/
│   ├── raw_papers/                    # Authoritative downloaded arXiv PDFs
│   │   ├── 1706.03762v7.pdf           # Attention Is All You Need (v7)
│   │   ├── 2410.00037v1.pdf           # Moshi: Real-Time Dialogue (v1)
│   │   └── 2507.07955v2.pdf           # Dynamic Chunking / H-Net (v2)
│   ├── processed/                     # Parsed section JSONs for Settings 1, 2, and 3
│   ├── observed_limits.json           # Raw headers and measurements from minimal Groq probe
│   └── eval_questions.json            # The 15 curated benchmark questions
│
├── docs/
│   ├── IMPLEMENTATION_PLAN.md         # This technical planning blueprint
│   └── THE_RELAY_2_PAGE_REPORT.md     # Final 2-page report under required headings
│
├── src/
│   └── relay/
│       ├── __init__.py
│       ├── config.py                  # Pydantic Settings (Hypotheses, limits, timeouts)
│       │
│       ├── corpus/                    # Paper acquisition, parsing, and chunking
│       │   ├── __init__.py
│       │   ├── fetcher.py             # Deterministic downloader for exact arXiv versions
│       │   ├── parser.py              # PyMuPDF extractor with section header detection
│       │   └── chunker.py             # Configurable chunk slicer with metadata
│       │
│       ├── retrieval/                 # Controlled retrieval experiments
│       │   ├── __init__.py
│       │   ├── bm25_index.py          # BM25 implementation for Settings 1 & 2
│       │   ├── reranker.py            # Section-boost lexical reranker for Setting 3
│       │   └── tool.py                # retrieve_passages() tool call with context shedding
│       │
│       ├── models/                    # Provider abstraction & stand-in
│       │   ├── __init__.py
│       │   ├── base.py                # Abstract LLMProvider & StreamChunk
│       │   ├── groq_provider.py       # Real Groq Cloud client with header parser
│       │   └── standin_service.py     # FastAPI standalone emulator (SSE, jitter, limits)
│       │
│       ├── pipeline/                  # The 4-step pipeline engine
│       │   ├── __init__.py
│       │   ├── state.py               # RequestContext & LatencyTimers
│       │   ├── router.py              # Step 1: Query router & intent classifier
│       │   ├── retriever_step.py      # Step 2: Tool execution
│       │   ├── generator_step.py      # Step 3: Buffered draft generator
│       │   ├── verifier_step.py       # Step 4: Verification loop (NEVER BYPASSED)
│       │   └── engine.py              # End-to-end async orchestrator
│       │
│       ├── gateway/                   # Admission control, FIFO queue & limits
│       │   ├── __init__.py
│       │   ├── fifo_queue.py          # Bounded FIFO queue with TTL cancellation
│       │   ├── concurrency.py         # Semaphore concurrency gate
│       │   ├── rate_limiter.py        # Token bucket RPM/TPM limiter
│       │   └── circuit_breaker.py     # Trips on daily exhaustion or severe 5xx
│       │
│       └── telemetry/                 # Telemetry & results accounting
│           ├── __init__.py
│           ├── timer.py               # Monotonic stage timer (queue, work, retry, total)
│           ├── recorder.py            # Thread-safe results.csv writer
│           └── validator.py           # Mathematical consistency audit script
│
├── benchmarks/                        # Executable benchmarking scripts
│   ├── probe_groq.py                  # Step 1: Minimal real service probing (Table 1)
│   ├── run_standin_server.py          # Runs local stand-in FastAPI process
│   ├── run_retrieval_eval.py          # Evaluates Settings 1-3 on 15 questions (Table 3)
│   ├── run_load_scenarios.py          # Runs 5 load scenarios (Tables 2a & 2b)
│   └── generate_report_tables.py      # Direct mathematical compilation to Markdown tables
│
└── tests/                             # Comprehensive test suite
    ├── test_pdf_parsing.py            # Asserts text and section fidelity
    ├── test_retry_accounting.py       # Acceptance Test 1: Work vs. Sleep timing isolation
    ├── test_ttl_behavior.py           # Acceptance Test 2: TTL drops expired requests
    ├── test_verifier_invariant.py     # Acceptance Test 3: Verifier NEVER bypassed
    └── test_standin_parity.py         # Asserts stand-in reproduces 429 and SSE formats
```

---

## 3. Mandatory Verification Invariant

The assignment brief specifies:
> *"The company wants every reply fast, every reply good, the bill small, and nobody turned away... It also never wants to send a wrong or embarrassing answer just to stay fast."*

To uphold this requirement, **verification is a hard system invariant, never an optional optimization**.

### 3.1 Verification Decision Matrix
| Verification Outcome | Root Cause | System Action | User-Facing Output | `answer_grade` |
| :--- | :--- | :--- | :--- | :--- |
| **Success** | All claims substantiated by retrieved passages; citations valid. | Release buffered response. | Full cited technical answer. | `correct` (or `wrong` if evaluated false by benchmark) |
| **Grounding Defect (Loop 1)** | Hallucinated detail or passage gap on first attempt. | Reformulate query; invoke `retrieve_passages` once more; re-draft. | Intermediate buffer; nothing sent to user. | N/A (in-flight) |
| **Grounding Defect (Exhausted)** | Secondary draft still contains ungrounded claims. | Suppress draft; emit refusal. | `"The provided papers do not state sufficient evidence to substantiate..."` | `refused_correctly` (if unanswerable) or `refused_wrongly` |
| **Missing Evidence / OOD** | Library lacks facts to answer question. | Immediate clean refusal. | `"The paper library does not contain information answering this question."` | `refused_correctly` |
| **Verifier Timeout** | Verification call exceeds timeout budget. | Suppress draft; fail safe. | `"Unable to verify answer within latency limits. Refusing response to maintain correctness."` | `refused_wrongly` / `error` |
| **Verifier Provider Error** | Upstream 5xx or rate limit during verification call. | Check retry budget; if exhausted, fail safe. | Structured refusal stating verification failed. | `error` |

> [!CAUTION]
> Under no circumstances may an unverified draft be released to the user. Fast failure to a safe refusal is strictly preferred over fast emission of an unverified response.

---

## 4. Provider Limits: Documented vs. Observed vs. Simulated

To preserve experimental integrity and prevent fabrication, limits are partitioned into three non-overlapping tiers:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                               PROVIDER LIMIT CATEGORIZATION                                      │
├──────────────────────────┬─────────────────────────────────────┬─────────────────────────────────┤
│   1. Documented Limits   │         2. Observed Limits          │       3. Simulated Limits       │
│  (Published Groq Docs)   │      (Measured via Live Probe)      │     (Local Stand-in Engine)     │
├──────────────────────────┼─────────────────────────────────────┼─────────────────────────────────┤
│ Model: llama-3.1-8b-inst │ Model Status: [Pending Live Probe]  │ Model: llama-3.1-8b-instant     │
│ RPM: 30                  │ Measured RPM: [Pending Live Probe]  │ Configurable RPM (e.g. 30, 15)  │
│ TPM: 6,000               │ Measured TPM: [Pending Live Probe]  │ Configurable TPM (e.g. 6,000)   │
│ RPD: 14,400              │ Measured RPD: [Pending Live Probe]  │ Configurable RPD                │
│ TPD: 500,000             │ Measured TPD: [Pending Live Probe]  │ Configurable TPD (e.g. 500,000) │
│ In-flight Concurrency:   │ Observed Concurrency: [Pending]     │ In-flight Semaphore: C_max = 5  │
│ Not formally documented  │ 429 Payload & Headers: [Pending]    │ Returns exact Groq 429 schema   │
└──────────────────────────┴─────────────────────────────────────┴─────────────────────────────────┘
```

### 4.1 Status Classification
- **Model Selection:** `llama-3.1-8b-instant` is selected based on documented free-tier suitability (low latency, 30 RPM, 6k TPM). Its active availability on the user's account is classified as **[Pending Live Probe]**.
- **Documented Limits:** Groq published documentation states 30 RPM, 6,000 TPM, 14,400 RPD, 500,000 TPD for `llama-3.1-8b-instant`.
- **Observed Limits:** Must be derived strictly from the minimal live probe script (`benchmarks/probe_groq.py`), which logs raw HTTP response headers (`x-ratelimit-limit-requests`, `x-ratelimit-remaining-tokens`, `retry-after`) to `data/observed_limits.json`. If unmeasured, marked `"not measured"`.
- **Simulated Limits:** Configured parameters in `StandInService` used exclusively during load testing (steady, burst, sustained overload, halved limit, daily budget exhausted).

---

## 5. Token Budget Calculations & Throughput Realities

### 5.1 Stage-by-Stage Token Consumption Model
A question passing through the pipeline incurs token consumption across multiple model interactions:

| Pipeline Stage | Interaction Type | Estimated Input Tokens | Estimated Output Tokens | Total Stage Tokens | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Step 1: Router** | Semantic / Model routing (if model-based) | $\sim 250$ | $\sim 50$ | $\sim 300$ | [Estimated Hypothesis] |
| **Step 2: Retrieval** | Tool execution | $0$ (local execution) | $0$ | $0$ | [Confirmed] |
| **Step 3: Draft Gen** | Model call with context ($k=4$ chunks) | $\sim 1,200\text{--}1,800$ | $\sim 200\text{--}350$ | $\sim 1,400\text{--}2,150$ | [Estimated Hypothesis] |
| **Step 4: Verifier** | Citation audit model call | $\sim 1,000\text{--}1,500$ | $\sim 50\text{--}100$ | $\sim 1,050\text{--}1,600$ | [Estimated Hypothesis] |
| **Total (Happy Path)**| **Single-pass pipeline** | $\sim 2,450\text{--}3,550$ | $\sim 300\text{--}500$ | $\mathbf{\sim 2,750\text{--}4,050}$ | **[Estimated Hypothesis]** |
| **Total (With Loop)** | **Re-retrieval & re-draft loop** | $+ \sim 1,500$ | $+ \sim 250$ | $\mathbf{\sim 4,500\text{--}5,800}$ | **[Estimated Hypothesis]** |

> [!IMPORTANT]
> **Throughput Implications of the 6,000 TPM Limit:**
> - If an average question consumes $\sim 3,000$ tokens across draft and verification, the documented **6,000 TPM limit permits only $\approx 2$ completed questions per minute** on the real Groq free tier.
> - Attempting to run 15–30 questions/minute against the live Groq free tier will trigger immediate, persistent 429 rate limits within 30 seconds.
> - Therefore, real Groq benchmarking must run at **$\le 1.5$ questions/minute** (or use isolated step probes). All high-throughput load tests (15–60 questions/minute) **must run against the Stand-in Service**.

---

## 6. Telemetry & Timing Accounting

To satisfy Heading 4 (*"Where the time goes"*) and prevent tautological metric definitions, all timing values are derived from independent monotonic clock samples (`time.perf_counter()`).

### 6.1 Metric Definitions & Data Types
| Metric Column | Measurement Method | Mathematical Definition / Meaning |
| :--- | :--- | :--- |
| `request_id` | UUID4 string | Unique identifier for the client request. |
| `scenario` | String category | `steady`, `burst`, `overload`, `limit_halved`, `budget_exhausted`, `retrieval_eval`. |
| `source` | Enum | `real` (Groq Cloud API) or `standin` (Local stand-in service). |
| `start_time` | ISO 8601 string | Gateway receipt timestamp ($t_0$). |
| `total_ms` | Monotonic clock | Independently measured end-to-end duration: $(t_{\text{final}} - t_0) \times 1000$. |
| `queue_wait_ms` | Monotonic clock | Time spent resident in admission FIFO queue before worker dequeue: $(t_{\text{dequeue}} - t_0) \times 1000$. |
| `working_ms` | Monotonic clock | Cumulative active processing time across router, retrieval, generation attempts, and verifier: $\sum \Delta t_{\text{exec}}$. |
| `retry_wait_ms` | Monotonic clock | Cumulative intentional backoff sleep duration between retry attempts: $\sum \Delta t_{\text{sleep}}$. |
| `model_first_token_ms` | Monotonic clock | Wall-clock time from $t_0$ until the draft model produces its first token internally: $(t_{\text{model\_first\_tok}} - t_0) \times 1000$. |
| `user_first_token_ms` | Monotonic clock | Wall-clock time from $t_0$ until the verified response begins streaming/releasing to client: $(t_{\text{user\_release}} - t_0) \times 1000$. |
| `retries` | Integer count | Number of retry attempts executed (0 for first-attempt success). |
| `tokens_in` | Integer count | Total prompt tokens consumed across all model calls for this request. |
| `tokens_out` | Integer count | Total completion tokens generated across all model calls for this request. |
| `retrieval_setting`| String ID | `setting_1_bm25_base`, `setting_2_bm25_granular`, `setting_3_hybrid_rerank`. |
| `status` | Enum | `ok`, `rate_limited`, `timeout`, `error`. |
| `answer_grade` | Enum | `correct`, `wrong`, `refused_correctly`, `refused_wrongly`. |
| `verification_status`| Enum | `verified_grounded`, `verified_refusal`, `failed_verification`, `verifier_error`, `verifier_timeout`. |

### 6.2 Latency Accounting Invariant & Timing Overhead
`total_ms` is measured directly and independently:
$$t_{\text{total\_ms}} = (t_{\text{final}} - t_0) \times 1000$$

The sum of constituent stages accounts for the total duration plus unclassified scheduling overhead ($\epsilon$):
$$t_{\text{total\_ms}} = t_{\text{queue\_wait\_ms}} + t_{\text{working\_ms}} + t_{\text{retry\_wait\_ms}} + \epsilon$$

Where:
- $\epsilon$ represents Python asyncio event loop dispatch latency and OS thread scheduling jitter.
- **Auditing Acceptance Criteria:** In automated testing, $\epsilon$ must satisfy $\epsilon \ge 0$ and $\epsilon < 15\text{ ms}$ under normal load.
- `working_ms` explicitly includes runtime of failed attempts, while `retry_wait_ms` contains only sleeping backoff time. No interval is counted twice.

---

## 7. Retry, TTL, and Failure Taxonomy

The four failure modes identified in Revision 2 are operationalized with strict handling policies:

```
                                  [ Downstream Failure Event ]
                                                │
       ┌────────────────────────┬───────────────┴───────────────┬────────────────────────┐
       ▼                        ▼                               ▼                        ▼
[ 1. Per-Minute 429 ]   [ 2. Daily Quota 429 ]        [ 3. Client Deadline ]    [ 4. Provider 5xx / Net ]
- retry-after <= 60s    - retry-after > 3600s         - t_elapsed > client_ttl  - 500, 503, socket drop
- Transient spike       - Terminal daily cap          - User abandoned queue    - Transient outage
       │                        │                               │                        │
       ▼                        ▼                               ▼                        ▼
Check Retry Budget      Trip Circuit Breaker to OPEN   Cancel Async Task         Check Retry Budget
& Request Deadline      Cancel all pending retries     Do not call model         Exponential Backoff
       │                        │                               │                        │
  Within Budget?                ▼                               ▼                  Within Budget?
  ├── YES: Sleep backoff   Emit 0-token Refusal          Log status: timeout       ├── YES: Retry attempt
  │   with Full Jitter     status: rate_limited          user receives 504         └── NO: Log status: error
  └── NO: Fail fast        answer_grade: refused_correctly                         trip breaker if burst
```

### 7.1 Detailed Failure Handling Rules
1. **Per-Minute Rate Limiting (RPM/TPM):**
   - *Retryable:* Yes, provided the request deadline has not elapsed and retry budget is unexhausted.
   - *Backoff:* Exponential backoff with full jitter: $t_{\text{sleep}} = \min(t_{\text{backoff\_max}}, \text{random}(0, t_{\text{backoff\_base}} \cdot 2^{\text{attempt}}))$. If `retry-after` header is supplied, $t_{\text{sleep}} = \max(t_{\text{sleep}}, t_{\text{retry\_after}} + \text{jitter})$.
2. **Daily Quota Exhaustion (RPD/TPD):**
   - *Retryable:* **NO. Never retry.**
   - *Action:* The circuit breaker transitions to `OPEN`. All queued and incoming requests immediately return a zero-token refusal.
   - *Integrity Rule:* Provider quota rejection must be logged with `status: rate_limited`. It is **not** an answer-level grounded refusal (`answer_grade` reflects system capacity, not textual unanswerability).
3. **Client Deadline Expiration (TTL):**
   - *Trigger:* Evaluated before dequeue and before each external network call: `if monotonic() > deadline: cancel()`.
   - *Action:* Abort request immediately; emit HTTP 504 / timeout response; log `status: timeout`.
4. **Temporary Provider Failures (500, 503, Timeout):**
   - *Retryable:* Yes (up to `retry_max_attempts = 2`).
   - *Budget Protection:* Retries are drawn from the global `retry_budget_pct = 15%`. If total retries exceed 15% of historical window requests, retries are denied and the error is returned immediately.

---

## 8. Evaluation Dataset Audit & Evidence Matrix

The 15 evaluation questions have been audited against paper contents. Questions requiring numerical or formula accuracy are verified, and unverified citations are explicitly identified.

### 8.1 Dataset Evidence Table
| Question ID | Question Type | Source Document | Stated Section / Page | Expected Evidence / Fact | Verification Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Q01** | Specific Fact | Attention Is All You Need | Section 5.4, Page 7 | Residual dropout rate $P_{\text{drop}} = 0.1$ applied to sub-layers. | [Pending PDF Verification] |
| **Q02** | Specific Fact | Attention Is All You Need | Section 5.3, Page 7 | Learning rate warmup steps = 4000 in Adam optimizer schedule. | [Pending PDF Verification] |
| **Q03** | Specific Fact | Attention Is All You Need | Section 5.4, Page 7 | Label smoothing parameter $\epsilon_{\text{ls}} = 0.1$. | [Pending PDF Verification] |
| **Q04** | Formula / Values | Attention Is All You Need | Section 3.2.2, Page 4 | Attention dimensions $d_k = d_v = d_{\text{model}} / h = 64$ for $h=8$. | [Pending PDF Verification] |
| **Q05** | Table / Numeric | Moshi | Section 2.1 / Table 1 | Mimi codec bitrate is 1.1 kbps at 12.5 Hz frame rate (80 ms step). | [Pending PDF Verification] |
| **Q06** | Timing / Mechanism | Moshi | Section 3.2, Page 4 | Acoustic delay relationship: 200 ms offset vs 80 ms audio frame step. | [Pending PDF Verification: Verify exact token/frame offset] |
| **Q07** | Architecture | Moshi | Section 2.1, Page 3 | Number of Residual Vector Quantization (RVQ) codebooks in Mimi ($8$ codebooks). | [Pending PDF Verification] |
| **Q08** | Numeric Claim | Moshi | Abstract / Section 1 | Overall conversational latency: 160 ms theoretical, $\sim 200\text{ ms}$ practical. | [Pending PDF Verification] |
| **Q09** | Benchmark Claim | Dynamic Chunking | Section 4.3, Page 8 | H-Net DNA sequence efficiency claim (verify exact multiplier in Table/Text). | [Pending PDF Verification: Check if 4x is exact wording or rounded] |
| **Q10** | Mechanism | Dynamic Chunking | Section 3.1, Page 4 | Learned dynamic chunk boundary scoring mechanism without BPE. | [Pending PDF Verification] |
| **Q11** | Modalities | Dynamic Chunking | Section 4.3, Page 8 | Evaluation across Chinese text, computer source code, and DNA sequences. | [Pending PDF Verification] |
| **Q12** | False Friend Trap | Cross-Corpus Trap | N/A (Moshi vs H-Net) | Asking for "streaming speech tokens in H-Net". System must refuse. | [Confirmed Valid Trap] |
| **Q13** | False Friend Trap | Cross-Corpus Trap | N/A (Vaswani vs H-Net)| Asking for "dynamic chunk boundaries in Transformer 2017". System must refuse. | [Confirmed Valid Trap] |
| **Q14** | Negative / OOD | Out of Library | N/A (LLaMA 3) | Pretraining batch size of LLaMA 3. System must refuse. | [Confirmed Valid Negative] |
| **Q15** | Negative / OOD | Out of Library | N/A (Whisper) | LibriSpeech test-clean WER of Whisper. System must refuse. | [Confirmed Valid Negative] |

> [!NOTE]
> Since arXiv PDFs have not yet been downloaded to `data/raw_papers/`, the exact text citations above are classified as **[Pending PDF Verification]**. Milestone M1 will inspect the downloaded PDFs with PyMuPDF to extract exact page, section, and line matches before running Table 3 evaluations.

---

## 9. Controlled Retrieval Experiments (Settings 1, 2, 3)

We test three controlled, reproducible settings to determine the Pareto frontier between context size, token cost, and citation precision:

```
Setting 1: BM25 Baseline ────────► 350-word chunks, k=4 (Direct BM25 top-k, no reranking)
                                   [Low working latency, simple, zero extra dependencies]

Setting 2: Granular BM25 ────────► 175-word chunks, k=6 (Finer chunks, smaller context)
                                   [Evaluates whether smaller snippets improve grounding]

Setting 3: Hybrid / Rerank ──────► 350-word chunks, k=8 fetched -> Top-3 selected via
                                   lexical-section score reranker
                                   [Evaluates if candidate pruning reduces tokens & false friends]
```

### 9.1 Experimental Protocol for Table 3
- Each setting is evaluated across all 15 audited questions.
- Telemetry records: `first_token_ms` (model & user), `total_ms`, `tokens_in`, `tokens_out`, `answer_grade`.
- **Criteria for Retaining Setting 3:** A re-ranking stage is justified *only* if it demonstrates a statistically meaningful reduction in false-friend errors (Q12, Q13) without inflating `user_first_token_ms` beyond acceptable thresholds. Otherwise, the simpler BM25 baseline is retained.

---

## 10. Benchmarking Scenarios & Load Generation

### 10.1 Scenario Matrix & Capacity Guardrails
To prevent crashing real accounts, load tests are strictly partitioned by target backend:

| Scenario | Target Backend | Offered Load (QPM) | Concurrency ($C$) | Total Questions | Duration | Token Consumption Intent |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Groq Live Probe** | Real Groq API | $\le 1.5$ QPM | 1 in-flight | 10–15 probes | $\sim 10$ mins | Probe headers & single-step latency safely ($<15\text{k}$ total tokens). |
| **1. Steady Load** | Stand-in Engine | 15 QPM | 2–3 in-flight | 45 questions | 3 mins | Baseline queue wait $\approx 0$; steady throughput. |
| **2. Sudden Burst** | Stand-in Engine | 30 questions in 2s | 30 initial burst | 30 questions | 1 min | Measure queue build-up, p95/p99 TTFT spike, TTL drops. |
| **3. Sustained Overload**| Stand-in Engine | 60 QPM | Unbounded queue | 120 questions | 2 mins | System saturation, steady-state queue shedding, TTL timeouts. |
| **4. Limit Halved** | Stand-in Engine | 20 QPM (RPM=15) | 5 in-flight | 40 questions | 2 mins | Throttle backpressure under constrained rate ceiling. |
| **5. Budget Exhausted** | Stand-in Engine | 15 QPM (Quota=0)| 1 in-flight | 15 questions | 1 min | 3:00 PM scenario: Immediate 429, circuit breaker trip, 0 tokens. |

*(Note: QPM = Questions Per Minute. Each question involves router, draft generation, and verification calls).*

---

## 11. Stand-in Model Service Specification

The Stand-in Service is an independent, controllable HTTP server built with FastAPI, replicating the OpenAI/Groq `/v1/chat/completions` REST and SSE streaming interfaces.

### 11.1 Behavioral Specifications
1. **Calibrated Latency:** Emulates Time-To-First-Token using Log-Normal distribution fitted to real probe measurements: $\text{TTFT} \sim \text{LogNormal}(\mu_{\text{probe}}, \sigma_{\text{probe}})$.
2. **Streaming Emulation:** Yields Server-Sent Events (SSE) data chunks at a configurable inter-token rate (e.g. 15 ms/token).
3. **Queue Saturation Modeling:** When active concurrent connections $N_{\text{active}}$ exceed baseline capacity $C_{\text{cap}}$, effective TTFT degrades:
   $$\text{TTFT}_{\text{effective}} = \text{TTFT}_{\text{base}} \times \left(1 + 1.2 \cdot \frac{N_{\text{active}}}{C_{\text{cap}}}\right)$$
4. **Fault & Limit Injection:**
   - Returns exact Groq-formatted 429 JSON with `retry-after` header when configured RPM/TPM is breached.
   - Injects configurable HTTP 500/503 errors at probability $P_{\text{error}} = 0.02$.
   - Triggers daily budget exhaustion responses when simulated daily token counter reaches threshold.

### 11.2 Key Architectural Differences from Real Groq
For complete transparency in the written report:
1. **Execution Hardware:** Stand-in runs on standard x86 CPU threads via `asyncio.sleep()`, lacking Groq's custom Language Processing Unit (LPU) SRAM tensor architecture.
2. **Text Generation:** Stand-in returns pre-structured or synthetic token streams rather than performing real autoregressive forward passes across 8 billion parameters.
3. **Noisy Neighbor Independence:** Stand-in latency is free from multi-tenant cloud cluster interference.

---

## 12. Essential Acceptance Tests

The testing suite enforces three critical invariant tests alongside standard unit and integration checks:

### Test 1: `test_retry_accounting`
- **Objective:** Prove that failed attempt runtimes are assigned to `working_ms`, backoff sleep is assigned to `retry_wait_ms`, and `total_ms` matches independent wall-clock time without double-counting.
- **Assertion:**
  $$\left|\texttt{total\_ms} - (\texttt{queue\_wait\_ms} + \texttt{working\_ms} + \texttt{retry\_wait\_ms})\right| < 10\text{ ms}$$
  $$\texttt{working\_ms} \ge \sum \text{duration}(\text{attempt}_i), \quad \texttt{retry\_wait\_ms} == \sum t_{\text{backoff}}$$

### Test 2: `test_ttl_behavior`
- **Objective:** Prove that requests waiting in the queue beyond `client_ttl_s` expire, are actively canceled, do not invoke downstream model providers, and are logged with `status: timeout`.
- **Assertion:** Enqueue 10 requests with TTL = 0.5s behind a blocked worker. Assert all 10 return HTTP 504 / timeout within 600 ms, with zero tokens consumed.

### Test 3: `test_verifier_invariant`
- **Objective:** Prove that under no circumstances is an ungrounded or unverified response emitted to the client.
- **Sub-cases Tested:**
  1. *Valid citation:* Response released to client (`verification_status: verified_grounded`).
  2. *Hallucinated claim:* Draft intercepted, re-retrieval triggered; if persistent, safe refusal emitted (`verification_status: failed_verification`).
  3. *Verifier timeout / provider 500:* Draft suppressed, safe refusal emitted (`verification_status: verifier_timeout` or `verifier_error`). User never receives unverified text.

---

## 13. Explicit Acceptance Criteria

The system is ready for formal review when all of the following criteria are satisfied:

1. **Pipeline Completeness:** 
   - At least 4 distinct steps (Router, Retriever tool, Draft generator, Grounding verifier).
   - At least 1 formal tool call (`retrieve_passages`).
   - At least 1 dynamic loop/branch (re-retrieval loop or grounded refusal branch).
2. **Grounding Safety Invariant:** 
   - 0% unverified or ungrounded drafts released to the client across all test suites.
   - Refusal emitted whenever retrieved context does not substantiate the query.
3. **Telemetry & Auditing Integrity:** 
   - Every metric in Tables 1, 2a, 2b, and 3 is strictly computable from `results.csv`.
   - Timing equality verified: $\left|\texttt{total\_ms} - (\texttt{queue\_wait\_ms} + \texttt{working\_ms} + \texttt{retry\_wait\_ms})\right| < 15\text{ ms}$.
   - Clear decomposition of delay before vs. after first token for slow requests.
4. **Provider Quota Protection:** 
   - Live Groq API calls remain strictly under daily and per-minute token quotas during evaluation.
   - Zero fabrication of provider limits or measurements.
5. **Stand-in Service Parity:** 
   - Stand-in reproduces real TTFT distribution, SSE streaming, concurrency queue degradation, and Groq 429 headers.
6. **Report Deliverable:** 
   - Two-page Markdown report (`docs/THE_RELAY_2_PAGE_REPORT.md`) covering all 8 mandated headings, 3 subheadings, and assistant interaction notes.

---

## 14. Scope Control & Four-Day Execution Schedule

```
DAY 1: Corpus Setup, Documented Limits & Minimal Probing
  ├─ M1: Download 3 exact arXiv PDFs; PyMuPDF parsing; extract sections into JSON
  ├─ M2: Verify dataset Q01-Q15 evidence against parsed PDFs; update evidence table
  ├─ M3: Run minimal probe script against Groq API (probe_groq.py); record observed limits
  └─ M4: Implement core 4-step pipeline engine with buffered draft & verifier invariant

DAY 2: Stand-in Service, Gateway & Telemetry Harness
  ├─ M5: Build StandInService (calibrated to observed probe TTFT/ITL, SSE streaming)
  ├─ M6: Implement Admission Gateway (bounded FIFO queue, TTL cancellation, concurrency semaphore)
  ├─ M7: Build telemetry recorder (results.csv writer) & latency accounting validation
  └─ M8: Implement and pass Essential Acceptance Tests (retry accounting, TTL, verifier invariant)

DAY 3: Benchmarking Runs & Experiments
  ├─ M9: Run Retrieval Experiments (Settings 1, 2, 3) on Q01-Q15; generate Table 3
  ├─ M10: Run Load Scenario 1 (Steady) on Stand-in and verified minimal Real Groq
  ├─ M11: Run Load Scenarios 2, 3, 4, 5 on Stand-in; stream metrics into results.csv
  └─ M12: Execute generate_tables.py to compile Tables 1, 2a, 2b directly from results.csv

DAY 4: Deep-Dive Analysis & Final 2-Page Report
  ├─ M13: Isolate and trace breakdown of one slow request for Heading 4 (Where the time goes)
  ├─ M14: Document retry storm case study (Heading 6) and 3:00 PM budget exhaustion (Heading 7)
  ├─ M15: Write 3 naive retriever failure modes (Heading 5.2) and Vaani critique (Heading 8.1)
  └─ M16: Compile final 2-page report (docs/THE_RELAY_2_PAGE_REPORT.md) under required headings
```

---

## 15. Assumptions, Confirmed Facts, and Deferred Work

### 15.1 Status Classification of Critical Assumptions
- **`llama-3.1-8b-instant` suitability:** [Confirmed from Groq docs] — Fast, 30 RPM, 6k TPM free-tier allocation.
- **Provider account connectivity & active model:** [Pending Verification] — To be established by Milestone M3 probe.
- **Paper library contents & exact citation locations:** [Pending Verification] — Awaiting PDF download in Milestone M1.
- **Client TTL = 10.0s hypothesis:** [Estimated Hypothesis] — Configurable, subject to sensitivity evaluation.
- **Retry Budget = 15% hypothesis:** [Estimated Hypothesis] — Configurable, subject to sensitivity evaluation.
- **Token consumption $\sim 3,000$ tokens/question:** [Estimated Hypothesis] — Subject to empirical measurement in M3/M9.

### 15.2 Explicitly Deferred Work
1. **Cross-Encoder Neural Re-ranking:** Deferred. Evaluated only if BM25 baseline exhibits unacceptable failure rates on Q12/Q13.
2. **Distributed Infrastructure (Redis / Celery / Docker):** Deferred as unnecessary. In-process `asyncio.Queue` and local FastAPI service satisfy all latency and concurrency requirements within the 4-day deadline.
3. **Priority Queueing:** Deferred. FIFO queue guarantees fairness and avoids starvation; priority scheduling introduces complexity without empirical justification.

---

## 16. Verification & Approval Gate

Revision 3 is complete and fully reconciled with all engineering constraints. No application code has been written.

**Awaiting user approval before proceeding to Day 1 implementation (Milestones M1–M4).**
