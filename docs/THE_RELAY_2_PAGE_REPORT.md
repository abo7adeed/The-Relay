# The Relay — Engineering Report

## 1 What I built and why
I built **The Relay**, a four-step Python/FastAPI research assistant pipeline (`QueryRouter` → `RetrievalTool` → buffered `GeneratorStep` → `VerifierStep`) behind a bounded FIFO `AdmissionGateway` (`asyncio.Queue`, concurrency semaphore, sliding-window `LocalRateLimiter`, `RetryPolicy`, and `CircuitBreaker`). One dynamic branch handles citation failures by reformulating and re-retrieving once (`max_loops=1`) before falling back to a structured refusal.

**The single decision that shaped it most** was enforcing **buffered verification as a hard invariant**: drafts are streamed/generated strictly into memory (`model_first_token_ms`), verified against retrieved passage provenance and numeric consistency (`VerifierStep`), and only then released to the client (`user_first_token_ms`). Streaming directly from the model before checking citations would save ~350–610 ms of TTFT, but violates the requirement never to emit an unverified or embarrassing answer.

## 2 The real service and my stand-in
For the hosted Groq tier (`llama-3.1-8b-instant`), published limits are **30 RPM, 6,000 TPM, 14,400 RPD, and 500,000 TPD**. Because no `GROQ_API_KEY` was configured in the local execution environment, `benchmarks/probe_groq.py` honestly logged live observed limits as **not measured** (`NOT_VERIFIED_MISSING_CREDENTIALS` in `data/observed_limits.json`).

My local **Stand-in Service** (`src/relay/models/standin_service.py`) is an independent FastAPI HTTP/SSE server replicating `/v1/chat/completions`, Groq rate-limit headers (`x-ratelimit-*`, `retry-after`), 429 JSON error schemas (`rate_limit_exceeded` vs. `daily_quota_exhausted`), and concurrency saturation (`base_ms * (1 + 1.2 * max(0, active/cap - 1))`). Base TTFT follows a Log-Normal distribution ($\mu=5.45, \sigma=0.35$, ~233 ms median) with 6 ms inter-token SSE decode. Its **largest differences from Groq** are running `asyncio.sleep()` on x86 rather than LPU SRAM tensor execution, synthesizing evidence-checked responses rather than 8B-parameter forward passes, and freedom from multi-tenant noisy neighbors.

## 3 Results
All 120 end-to-end requests (45 retrieval evaluations + 75 load requests) ran over live HTTP/SSE against the calibrated **stand-in** and were recorded in `results.csv` (real service observed runs: **not measured**). Every table number below reconciles directly with `results.csv` ($\epsilon < 15\text{ ms}$).

**A number we could not have guessed without running it:** at `1,373.1` blended tokens per answered query in Setting 3, the **6,000 TPM** free-tier ceiling saturates at just **~4.4 questions/minute**—meaning a moderate 30 RPM ceiling is almost never the bottleneck, and even an 8 QPM trickle breaches TPM within 48 seconds. Across the 15 curated evaluation questions (`Q01–Q15`), Setting 3 achieved **66.7% correct & cited** (10/11 answerable), **26.7% correctly refused** (4/4 traps/OOD), and **0.0% wrong** answers.

## 4 Where the time goes
Tracing slow successful overload request `fc345e38-6746-4e7b-ba32-fbe25e3aaeec` (`status=ok`, `tokens_in=1088`, `tokens_out=61`, `retries=0`):
- **`total_ms`:** `2,015.176 ms` (`user_first_token_ms = 2,015.176 ms`)
- **`queue_wait_ms`:** `1,164.290 ms` (57.8% of total — spent waiting in FIFO queue behind earlier saturated requests)
- **`working_ms`:** `850.879 ms` (42.2% — active retrieval, HTTP/SSE generation, and verification; $\epsilon = 0.007\text{ ms}$)
- **`retry_wait_ms`:** `0.000 ms`

Comparing **`model_first_token_ms` (`1,403.799 ms`)** to **`user_first_token_ms` (`2,015.176 ms`)** proves that **69.7% (`1,403.8 ms`)** occurred *before* the model emitted its first internal token (`1,164.3 ms` queue wait + `239.5 ms` prefill TTFT), while **30.3% (`611.4 ms`)** occurred *after* the first token during full SSE token buffering and grounding verification.

## 5 When a limit is hit
- **Per-minute limit (429, `retry-after <= 60s`):** At 85% local TPM utilization (`LocalRateLimiter`), the pipeline dynamically sheds retrieval context (`top_k` halved `k -> max(2, k//2)`) without ever bypassing `VerifierStep`. Requests hitting upstream 429 retry only if remaining `client_ttl_s` and the global 15% `retry_budget_pct` allow; queued requests exceeding `ttl_s` cancel cleanly (`timeout`, 0 tokens), and excess arrivals when the FIFO queue is full shed immediately (`429 rate_limited`).
- **Per-day limit (429, `retry-after > 3600s` / `daily_quota_exhausted`):** Never retried. The first response trips `CircuitBreaker` to `OPEN`, causing all pending and arriving callers to fail fast (< 3 ms, 0 tokens). Latency falls on queued callers; drops hit overflow bursts and stale TTL requests.

### Retrieval: quality versus cost
All three settings ran across `Q01–Q15` (`results.csv` `scenario=retrieval_eval`):
- **Setting 1 (`350w / 50o, k=4`, BM25):** 66.7% correct (10/11 answerable), 26.7% correctly refused (4/4), 0.0% wrong, **1,900.3 tokens/answer**, median TTFT `565.0 ms`.
- **Setting 2 (`175w / 25o, k=6`, BM25):** Drops to **60.0% correct** (9/11) because smaller 175-word slices split cross-sentence context (`Q05`: separating `"Mimi"` from `"1.1kbps"`), consuming 1,789.4 tokens/answer.
- **Setting 3 (`350w / 50o, k=8->3`, Hybrid BM25 + subword TF-IDF RRF):** Matches Setting 1 accuracy (**66.7% correct, 26.7% refused, 0.0% wrong**) while slashing cost by **27.7%** to **1,373.1 tokens/answer** (`581.6 ms` median TTFT). **Chosen Setting: Setting 3**, optimizing token headroom under 6,000 TPM; under >= 85% quota pressure we dynamically shed `k=3 -> 2` while retaining verification.

### How this library will mislead a naive retriever
1. **Vocabulary gap without synonyms:** `Q10` (*"How does H-Net dynamically determine chunk boundaries without a tokenizer?"*) misses Section `2.2.1 Chunking Layer` (Page 6) in lexical BM25 because the page uses *"routing module"* and *"calculates boundary probabilities"* rather than *"determine"* or *"without a tokenizer"*.
2. **Cross-paper terminology ("false friends"):** `Q12` asks about *"streaming speech tokens in H-Net"* (mixing Moshi's speech tokens with H-Net), and `Q13` asks about *"Transformer dynamically adjusting chunk boundaries"*. Naive keyword matchers retrieve high-scoring passages from the wrong paper unless paper routing + grounding verification intercept them.
3. **Chunk-boundary splitting & PDF artifact traps:** Splitting 175-word windows (`Q05` on Page 10–11) separates codec names (*Mimi*) from numeric tables (*1.1 kbps*). Similarly, uncleaned PDF table metrics (`23.75 Deep-Att`) or line-break hyphens (`transduc- tion`) corrupt lexical tokenization if the parser does not repair them.

## 6 Retries
Policy: up to `max_attempts=2` with exponential backoff + full jitter capped by remaining request TTL, protected by a **15% global `retry_budget_pct`**, and **0 retries** on daily quota exhaustion.

**One case where retrying made things worse:** Request `0b4eb37e-476d-4bc7-94d0-bf61039adda2` (`limit_halved`, halved `15 RPM / 3000 TPM`). Attempt 1 failed with `429` in `12.8 ms`. Because upstream returned `retry-after: 3s` (within its `4.0s` TTL), `RetryPolicy` slept **`3,012.7 ms`** (`retry_wait_ms`), holding a scarce concurrency worker idle. Attempt 2 immediately failed with `429` again because the 60s rolling TPM window had not cleared—inflating total latency **236x** (`3,025.6 ms` vs `~13 ms` fail-fast) for zero throughput gain.

## 7 What the person sees
At 3:00 PM (`budget_exhausted` scenario, `N=15`), Request 1 probes upstream, catches `429 daily_quota_exhausted` (`retry-after=28800`), trips `CircuitBreaker` to `OPEN`, and emits zero model tokens. Requests 2–15 fail immediately in **0.1 ms median** (`p95 = 2.6 ms`, `0` retries, `0` tokens) returning a clear, honest refusal: *"Daily provider token budget is exhausted for today. Please retry after 00:00 UTC reset."*

## 8 What I'm unsure about, what I left out, what I learned
- **Least certain assumption:** Real Groq free-tier queue saturation and `retry-after` jitter could not be empirically calibrated without a live API key (`not measured`); if Groq enforces stricter burst TPM penalties or sub-minute micro-windows, `LocalRateLimiter` would need tighter pre-emptive throttling.
- **Two things deliberately left out:** (1) Distributed Redis/Celery workers (unnecessary overhead vs. in-process `asyncio.Queue`), and (2) neural cross-encoder reranking (Setting 3 subword TF-IDF RRF already recovers precision without GPU/latency overhead).
- **One thing learned:** Treating `total_ms = queue + work + retry` as a definition hides runtime bugs; measuring each clock independently reveals real asyncio overhead ($\epsilon < 1\text{ ms}$) and proved that short backoffs (`3s`) inside a `60s` sliding token window amplify tail latency without improving goodput.

## Your feedback on Vaani
Exploring `vaani.dev`, the **Teacher AI** configuration-diff workflow and **sub-500 ms P-90 turn-taking / anti-interruption engine** (*zero dead air* fillers while executing backend tool calls) stood out as genuinely solving real telephony pain points vs. generic LLM wrappers. What confused me initially was distinguishing self-serve BYOK vs. bundled telephony pricing across modular add-ons (like warm transfers and screening). One thing I would change is surfacing a live interactive TTFT/turn-latency trace inspector directly in the public sandbox docs so engineers can benchmark tool-call masking latency before signing up.

## The results tables (required)

### Table 1: Limits you found in the real service
| Limit | Value you found | How you found it | Confidence |
| :--- | :--- | :--- | :--- |
| Requests per minute | Documented: 30 \| Observed: not measured | Groq docs; `probe_groq.py` unauthenticated (`no GROQ_API_KEY`) | medium (docs) / low (live) |
| Tokens per minute | Documented: 6,000 \| Observed: not measured | Groq docs (`llama-3.1-8b-instant`) | medium (docs) |
| Requests per day | Documented: 14,400 \| Observed: not measured | Groq docs (`llama-3.1-8b-instant`) | medium (docs) |
| Tokens per day | Documented: 500,000 \| Observed: not measured | Groq docs (`llama-3.1-8b-instant`) | medium (docs) |
| Requests in flight at once | not measured (simulated cap = 5) | Pending live concurrency probe | low |
| What it returns when a limit is hit | HTTP 429 JSON + `retry-after` (simulated; real: not measured) | Groq API spec & OpenAI schema | medium |
| Time until it recovers | Sliding 60s window / 00:00 UTC daily reset (real: not measured) | Groq rate-limit documentation | medium |

### Table 2a: Latency
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| `steady` | stand-in | 4 | 48.7 | 5.0 | 586.4 / 777.8 / 794.4 | 586.4 / 777.8 / 794.4 |
| `burst` | stand-in | 20 | 2.0 | 2036.2 | 1228.9 / 1466.1 / 1798.4 | 1228.9 / 1466.1 / 1798.4 |
| `overload` | stand-in | 24 | 3.6 | 398.4 | 1275.4 / 1856.9 / 1979.3 | 1275.4 / 1856.9 / 1979.3 |
| `limit_halved` | stand-in | 12 | 22.2 | 32.5 | 15.5 / 3020.7 / 3025.6 | 15.5 / 3020.7 / 3025.6 |
| `budget_exhausted` | stand-in | 15 | 0.9 | 1041.9 | 0.1 / 2.6 / 7.2 | 0.1 / 2.6 / 7.2 |

### Table 2b: Outcomes
| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `steady` | 100.0% | 0.0% | 0.0% | 0.00 | 1 | 978.0 | $0.0500 |
| `burst` | 25.0% | 75.0% | 0.0% | 0.00 | 18 | 1118.2 | $0.0570 |
| `overload` | 20.8% | 33.3% | 45.8% | 0.00 | 10 | 1101.6 | $0.0563 |
| `limit_halved` | 16.7% | 83.3% | 0.0% | 0.17 | 1 | 1209.0 | $0.0616 |
| `budget_exhausted` | 0.0% | 100.0% | 0.0% | 0.00 | 1 | 0.0 | $0.0000 |

*(Simulated cost reference uses Groq published `llama-3.1-8b-instant` pricing: $0.05/1M input, $0.08/1M output tokens).*

### Table 3: Retrieval settings
| Setting (piece size, pieces fetched, re-rank yes or no) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- | :--- |
| Setting 1: `350w / 50o, k=4, rerank=No` | 15 | 66.7% | 0.0% | 26.7% | 1900.3 | 565.0 / 743.8 | 565.0 / 743.8 |
| Setting 2: `175w / 25o, k=6, rerank=No` | 15 | 60.0% | 0.0% | 26.7% | 1789.4 | 549.5 / 837.9 | 549.5 / 837.9 |
| Setting 3: `350w / 50o, k=8->3, rerank=Yes` | 15 | 66.7% | 0.0% | 26.7% | 1373.1 | 581.6 / 740.8 | 581.6 / 740.8 |

---

## How I worked with the assistant

### Raw Prompt Trajectory
1. Initial planning prompt: analyze `The_Relay_Assignment_Brief.pdf` and author `docs/IMPLEMENTATION_PLAN.md` without fabricating numbers or writing premature code.
2. Engineering critiques (Revisions 2 & 3): eliminate pre-verification user streaming, make TTFT vs. `user_first_token_ms` and `queue/work/retry` timers non-tautological, and separate Documented vs. Observed vs. Simulated limits.
3. Day 1 implementation & adversarial audits (Phases 1–2.2): inspect parsed corpus chunks, challenge heading heuristics, audit missing Moshi/Attention lines and 15 malformed hyphenated compounds, and enforce a full 52-test verification suite before promoting corpus artifacts.
4. Completion prompt (`"finish this project"`): promote remediated chunks, build Setting 3 Hybrid RRF reranking, complete `AdmissionGateway` + `CircuitBreaker` + `RetryPolicy`, run live HTTP/SSE benchmarks to generate verified `results.csv`, and compile final tables and write-up.

### Three Prompts That Mattered Most, Catches & Overrides
- **Three prompts that mattered most:** (1) Enforcing Revision 3's buffered-verifier safety invariant so unverified drafts are never emitted; (2) rejecting early Phase 2 heuristics when adversarial checks found table headers (`Model`, `Background`), missing `Acknowledgements` body lines, and 269 broken `word- word` hyphens; (3) forcing strict distinction between real measured vs. stand-in simulated metrics (`"not measured"` on live Groq).
- **Bugs caught in the assistant's output:** First, the parser joined lines with spaces before running hyphenation repair regex (`-\n`), leaving line-break hyphens unfixed in `data/processed/`. Second, `VerifierStep` checked `str(p.page_number) in cite_clean`, allowing `"1"` to falsely validate `"Page 10"`, and our numeric consistency checker caught `StandInService` extrapolating `"80 ms"` onto a Page 26 citation. Third, `BM25Okapi` scored `<= 0` on single-document test fixtures due to negative IDF.
- **Where I overruled the assistant:** I rejected heuristic keyword OOD filters, priority scheduling, and neural cross-encoders in favor of a strict FIFO gateway, Setting 3 subword TF-IDF + BM25 RRF reranking, and deterministic numeric/provenance verification invariants grounded directly in `results.csv`.
- **Distinctly mine:** Complete empirical reproducibility linking exact chunk byte hashes, line-level PDF completeness tests, strict latency conservation ($\epsilon < 0.01\text{ ms}$), and transparent reporting of unmeasured real-service credentials.

