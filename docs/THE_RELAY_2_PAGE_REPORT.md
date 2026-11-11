# The Relay — Engineering Report

*Revision 4 + Final Reconciliation (2026-11-11): all 120 rows in `results.csv` (Tables 2a, 2b, 3) are measured on the calibrated local stand-in (`llama-3.1-8b-instant` simulation); live Direct Groq (`qwen/qwen3.8-27b`) is verified separately via `data/observed_limits.json` and `data/real_groq_probe_results.json`.*

## 1 What I built and why
I built **The Relay**, a four-step Python/FastAPI research assistant pipeline (`QueryRouter` → `RetrievalTool` → buffered `GeneratorStep` → `VerifierStep`) behind a bounded FIFO `AdmissionGateway` (`asyncio.Queue`, concurrency semaphore, sliding-window `LocalRateLimiter`, `RetryPolicy`, and `CircuitBreaker`). One dynamic branch handles citation failures by reformulating and re-retrieving once (`max_loops=1`) before falling back to a structured refusal.

**The single decision that shaped it most** was enforcing **buffered verification as a hard invariant**: drafts are streamed/generated strictly into memory (`model_first_token_ms`), verified against retrieved passage provenance and numeric consistency (`VerifierStep`), and only then released to the client (`user_first_token_ms`). Streaming directly from the model before checking citations would save ~350–610 ms of TTFT, but violates the requirement never to emit an unverified or embarrassing answer.

## 2 The real service and my stand-in
For the hosted Groq free tier reference simulated by our stand-in (`llama-3.1-8b-instant`), published reference limits are **30 RPM, 6,000 TPM, 14,400 RPD, and 500,000 TPD**. On the active live Direct Groq account (`https://api.groq.com/openai/v1`), `/openai/v1/models` exposes 11 models including `qwen/qwen3.8-27b` (while `llama-3.1-8b-instant` is not exposed on this account). Authenticated probing (`benchmarks/probe_groq.py` → `data/observed_limits.json`, **`VERIFIED_ONLINE`**) measured **`8,000` TPM** (`x-ratelimit-limit-tokens=8000`, `reset=269ms`) and **`1,000` RPD** (`x-ratelimit-limit-requests=1000`, `reset=1m26.4s` = `86,400s/1,000`); **`rpm` and `tpd` are not emitted in headers and remain unverified (`null`)**. `RESPAN_API_KEY` is unset, so Respan gateway (`https://gateway.respan.ai/v1`) was not exercised live, and earlier HTTP 401 / pre-reconciliation snapshots remain archived under `data/archive/`.

My local **Stand-in Service** (`src/relay/models/standin_service.py`) is an independent FastAPI HTTP/SSE server replicating `/v1/chat/completions`, Groq rate-limit headers (`x-ratelimit-*`, `retry-after`), 429 JSON error schemas (`rate_limit_exceeded` vs. `daily_quota_exhausted`), and concurrency saturation (`base_ms * (1 + 1.2 * max(0, active/cap - 1))`). Base TTFT follows a Log-Normal distribution ($\mu=5.45, \sigma=0.35$, ~233 ms median) with 6 ms inter-token SSE decode. Its **largest differences from Groq** are running `asyncio.sleep()` on x86 rather than LPU SRAM tensor execution, synthesizing evidence-checked responses rather than 8B-parameter forward passes, and freedom from multi-tenant noisy neighbors.

## 3 Results
All 120 end-to-end requests (45 retrieval evaluations + 75 load requests) ran over live HTTP/SSE against the calibrated **stand-in** and were recorded in `results.csv`. Every table number in Tables 2a, 2b, and 3 reconciles directly with `results.csv`; the maximum observed latency-conservation error is **0.195 ms** (validator threshold: 15 ms). Separate from `results.csv`, a controlled 5-request live SSE pipeline check against Direct Groq (`https://api.groq.com/openai/v1`, `qwen/qwen3.8-27b`, Setting 3, 11s spacing to stay below 8,000 TPM; `data/real_groq_probe_results.json`) succeeded `5/5` HTTP (`446.2–1,392.8 ms` `user_first_token_ms`, `6,370` input / `465` output tokens, `4/4` `citation_correct` on answerable queries, `3` `correct`, `1` `refused_correctly` on `Q14`, and `1` `refused_wrongly` on `Q02` where `qwen/qwen3.8-27b` emitted `"4,000"` after a multi-paper disclaimer despite `verified_grounded`).

**A number we could not have guessed without running it:** at `1,373.1` blended tokens per answered query in Setting 3, the **6,000 TPM** free-tier ceiling saturates at just **~4.4 questions/minute**—meaning a moderate 30 RPM ceiling is almost never the bottleneck, and even an 8 QPM trickle breaches TPM within 48 seconds. Across the 15 curated evaluation questions (`Q01–Q15`), strict Revision 4 grading gives Setting 3 **53.3% fully correct facts** (8/15; 72.7% of 11 answerable), **33.3% correct + exact target citation** (5/15), **26.7% correctly refused** (4/4 traps/OOD), **13.3% wrong** (2/15), and **6.7% wrongly refused** (1/15). The previous `66.7% correct` figure came from an `any()` bug that accepted partial answers; it is no longer valid.

## 4 Where the time goes
Tracing slow successful overload request `7afa5482-51c8-46b4-8e71-eed4ecf95673` (`status=ok`, `tokens_in=1088`, `tokens_out=61`, `retries=0`):
- **`total_ms`:** `1,914.041 ms` (`user_first_token_ms = 1,914.041 ms`)
- **`queue_wait_ms`:** `1,147.689 ms` (60.0% of total — spent waiting in FIFO queue behind earlier saturated requests)
- **`working_ms`:** `766.345 ms` (40.0% — active retrieval, HTTP/SSE generation, and verification; $\epsilon = 0.007\text{ ms}$)
- **`retry_wait_ms`:** `0.000 ms`

Comparing **`model_first_token_ms` (`1,303.810 ms`)** to **`user_first_token_ms` (`1,914.041 ms`)** proves that **68.1% (`1,303.8 ms`)** occurred *before* the model emitted its first internal token (`1,147.7 ms` queue wait + `156.1 ms` prefill TTFT), while **31.9% (`610.2 ms`)** occurred *after* the first token during full SSE token buffering and grounding verification.

## 5 When a limit is hit
- **Per-minute limit (429, `retry-after <= 60s`):** At 85% local TPM utilization (`LocalRateLimiter`), the pipeline dynamically sheds retrieval context (`top_k` halved `k -> max(2, k//2)`) without ever bypassing `VerifierStep`. Requests hitting upstream 429 retry only if remaining `client_ttl_s` and the global 15% `retry_budget_pct` allow; queued requests exceeding `ttl_s` cancel cleanly (`timeout`, 0 tokens), and excess arrivals when the FIFO queue is full shed immediately (`429 rate_limited`).
- **Per-day limit (429, `retry-after > 3600s` / `daily_quota_exhausted`):** Never retried. The first response trips `CircuitBreaker` to `OPEN`, causing all pending and arriving callers to fail fast (< 3 ms, 0 tokens). Latency falls on queued callers; drops hit overflow bursts and stale TTL requests.

### Retrieval: quality versus cost
All three settings ran across `Q01–Q15` (`results.csv` `scenario=retrieval_eval`) and are graded only as fully correct when **every** mandatory fact is present:
- **Setting 1 (`350w / 50o, k=4`, BM25):** 53.3% fully correct facts (8/15; 8/11 answerable), 33.3% correct + exact target citation (5/15), 13.3% wrong, 26.7% correctly refused (4/4), 6.7% wrongly refused, **1,900.3 tokens/answer**, median TTFT `579.1 ms`.
- **Setting 2 (`175w / 25o, k=6`, BM25):** Drops to **46.7% fully correct facts** (7/15; 7/11 answerable) because smaller 175-word slices split cross-sentence context (`Q05`: separating `"Mimi"` from `"1.1kbps"`), consuming 1,789.4 tokens/answer; wrongly refused rises to 13.3%.
- **Setting 3 (`350w / 50o, k=8->3`, Hybrid BM25 + subword TF-IDF RRF):** Matches Setting 1 on fully correct facts (53.3%), correct + cited (33.3%), wrong (13.3%), and wrongly refused (6.7%) while slashing cost by **27.7%** to **1,373.1 tokens/answer** (`560.9 ms` median TTFT). **Chosen Setting: Setting 3**, optimizing token headroom under 6,000 TPM; under >= 85% quota pressure we dynamically shed `k=3 -> 2` while retaining verification.
- **Citation caveat:** `citation_grade` checks only whether the exact target citation string is present. It does **not** verify per-response evidence quotations or entailment; corpus quotation provenance is audited separately in `tests/test_eval_set.py`, and the pipeline verifier checks fetched-passage provenance and numeric consistency.

### How this library will mislead a naive retriever
1. **Vocabulary gap without synonyms:** `Q10` (*"How does H-Net dynamically determine chunk boundaries without a tokenizer?"*) misses Section `2.2.1 Chunking Layer` (Page 6) in lexical BM25 because the page uses *"routing module"* and *"calculates boundary probabilities"* rather than *"determine"* or *"without a tokenizer"*.
2. **Cross-paper terminology ("false friends"):** `Q12` asks about *"streaming speech tokens in H-Net"* (mixing Moshi's speech tokens with H-Net), and `Q13` asks about *"Transformer dynamically adjusting chunk boundaries"*. Naive keyword matchers retrieve high-scoring passages from the wrong paper unless paper routing + grounding verification intercept them.
3. **Chunk-boundary splitting & PDF artifact traps:** Splitting 175-word windows (`Q05` on Page 10–11) separates codec names (*Mimi*) from numeric tables (*1.1 kbps*). Similarly, uncleaned PDF table metrics (`23.75 Deep-Att`) or line-break hyphens (`transduc- tion`) corrupt lexical tokenization if the parser does not repair them.

## 6 Retries
Policy: up to `max_attempts=2` with exponential backoff + full jitter capped by remaining request TTL, protected by a **15% global `retry_budget_pct`**, and **0 retries** on daily quota exhaustion.

**One case where retrying made things worse:** Request `3803d90f-e702-4925-9eb5-07205437281a` (`limit_halved`, halved `15 RPM / 3000 TPM`). The first attempt failed with `429`. Because upstream returned `retry-after: 3s` (within its `4.0s` TTL), `RetryPolicy` slept **`3,008.6 ms`** (`retry_wait_ms`), holding a scarce concurrency worker idle. The retry then failed with `429` again because the rolling TPM window had not cleared—inflating total latency to **`3,027.7 ms`** versus **`19.0 ms`** of actual working time (~160x) for zero throughput gain. Revision 4 also corrected the retry off-by-one so `max_attempts=2` now means two total calls (one retry), not three.

## 7 What the person sees
At 3:00 PM (`budget_exhausted` scenario, `N=15`), Request 1 probes upstream, catches `429 daily_quota_exhausted` (`retry-after=28800`), trips `CircuitBreaker` to `OPEN`, and emits zero model tokens. Requests 2–15 fail immediately in **0.1 ms median** (`p95 = 1.7 ms`, `0` retries, `0` tokens) returning a clear, honest refusal: *"Daily provider token budget is exhausted for today. Please retry after 00:00 UTC reset."*

## 8 What I'm unsure about, what I left out, what I learned
- **Least certain assumption:** Direct Groq HTTP headers verified `8,000` TPM and `1,000` RPD for `qwen/qwen3.8-27b` (`446–1,393 ms` TTFT across 5 paced SSE requests), but **RPM, TPD, multi-tenant queue saturation, sustained burst 429 `retry-after` jitter, and `llama-3.1-8b-instant` live quotas remain unmeasured**. If Groq enforces stricter burst TPM penalties or sub-minute micro-windows, `LocalRateLimiter` would need tighter pre-emptive throttling.
- **Two things deliberately left out:** (1) Distributed Redis/Celery workers (unnecessary overhead vs. in-process `asyncio.Queue`), and (2) neural cross-encoder reranking (Setting 3 subword TF-IDF RRF already recovers precision without GPU/latency overhead).
- **One thing learned:** Treating `total_ms = queue + work + retry` as a definition hides runtime bugs; measuring each clock independently reveals real asyncio overhead ($\epsilon < 1\text{ ms}$) and proved that short backoffs (`3s`) inside a `60s` sliding token window amplify tail latency without improving goodput.

## Your feedback on Vaani
Exploring `vaani.dev`, the **Teacher AI** configuration-diff workflow and **sub-500 ms P-90 turn-taking / anti-interruption engine** (*zero dead air* fillers while executing backend tool calls) stood out as genuinely solving real telephony pain points vs. generic LLM wrappers. What confused me initially was distinguishing self-serve BYOK vs. bundled telephony pricing across modular add-ons (like warm transfers and screening). One thing I would change is surfacing a live interactive TTFT/turn-latency trace inspector directly in the public sandbox docs so engineers can benchmark tool-call masking latency before signing up.

## The results tables (required)

### Table 1: Limits you found in the real service
| Limit | Value you found | How you found it | Confidence |
| :--- | :--- | :--- | :--- |
| Requests per minute | Documented (`llama-3.1-8b-instant` ref): `30` \| Observed (`qwen/qwen3.8-27b`): **not measured (`null`)** | Live headers emit `x-ratelimit-limit-requests=1000` (`reset=1m26.4s`, RPD window); no RPM header | medium (docs) / low (live RPM unverified) |
| Tokens per minute | Documented (`llama-3.1-8b-instant` ref): `6,000` \| Observed (`qwen/qwen3.8-27b`): **`8,000`** | Live Direct Groq header `x-ratelimit-limit-tokens=8000` (`reset=269ms`) | high (live headers for `qwen/qwen3.8-27b`) |
| Requests per day | Documented (`llama-3.1-8b-instant` ref): `14,400` \| Observed (`qwen/qwen3.8-27b`): **`1,000`** | Live Direct Groq header `x-ratelimit-limit-requests=1000` (`86,400s / 1,000 = 86.4s`) | high (live headers for `qwen/qwen3.8-27b`) |
| Tokens per day | Documented (`llama-3.1-8b-instant` ref): `500,000` \| Observed (`qwen/qwen3.8-27b`): **not measured (`null`)** | Not emitted in Groq HTTP response headers | medium (docs) / low (live TPD unverified) |
| Requests in flight at once | **not measured** (`stand-in` simulated cap = `5`) | Steady live probe ran 1-in-flight sequentially (`5/5` ok); saturation concurrency untested | low |
| What it returns when a limit is hit | HTTP `429` JSON + `retry-after` (`stand-in` simulated; real `429` not triggered) | Groq/OpenAI error spec; live probe spaced 11s to avoid 8,000 TPM exhaustion | medium |
| Time until it recovers | Observed token reset: `269 ms` (20-tok ping) / `6.2–13.3 s` (RAG SSE); request reset step: `1m26.4s`; sustained 429 recovery unmeasured | Live headers `x-ratelimit-reset-tokens` and `x-ratelimit-reset-requests` on `data/real_groq_probe_results.json` | medium–high (headers) / low (429 burst) |

### Table 2a: Latency
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| `steady` | stand-in | 4 | 48.9 | 5.0 | 622.1 / 886.6 / 916.0 | 622.1 / 886.6 / 916.0 |
| `burst` | stand-in | 20 | 1.9 | 2001.5 | 1064.7 / 1334.4 / 1673.4 | 1064.7 / 1334.4 / 1673.4 |
| `overload` | stand-in | 24 | 3.6 | 399.7 | 1231.1 / 1772.0 / 1882.0 | 1231.1 / 1772.0 / 1882.0 |
| `limit_halved` | stand-in | 12 | 22.1 | 32.6 | 11.4 / 3025.6 / 3027.3 | 11.4 / 3025.6 / 3027.3 |
| `budget_exhausted` | stand-in | 15 | 0.9 | 1041.0 | 0.1 / 1.7 / 4.6 | 0.1 / 1.7 / 4.6 |

### Table 2b: Outcomes
| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `steady` | 100.0% | 0.0% | 0.0% | 0.00 | 1 | 978.0 | $0.0500 |
| `burst` | 25.0% | 75.0% | 0.0% | 0.00 | 18 | 1118.2 | $0.0570 |
| `overload` | 20.8% | 37.5% | 41.7% | 0.00 | 10 | 1132.2 | $0.0579 |
| `limit_halved` | 16.7% | 83.3% | 0.0% | 0.17 | 1 | 1209.0 | $0.0616 |
| `budget_exhausted` | 0.0% | 100.0% | 0.0% | 0.00 | 1 | 0.0 | $0.0000 |

*(Simulated cost reference uses Groq published `llama-3.1-8b-instant` pricing: $0.05/1M input, $0.08/1M output tokens).*

### Table 3: Retrieval settings
| Setting (piece size, pieces fetched, re-rank yes or no) | Questions asked | Fully correct facts % | Correct + target-cited % | Wrong % | Correctly refused % | Wrongly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- | :--- |
| Setting 1: `350w / 50o, k=4, rerank=No` | 15 | 53.3% | 33.3% | 13.3% | 26.7% | 6.7% | 1900.3 | 579.1 / 737.8 | 579.1 / 737.8 |
| Setting 2: `175w / 25o, k=6, rerank=No` | 15 | 46.7% | 33.3% | 13.3% | 26.7% | 13.3% | 1789.4 | 544.7 / 752.9 | 544.7 / 752.9 |
| Setting 3: `350w / 50o, k=8->3, rerank=Yes` | 15 | 53.3% | 33.3% | 13.3% | 26.7% | 6.7% | 1373.1 | 560.9 / 871.2 | 560.9 / 871.2 |

Percentages are out of all 15 questions. `Fully correct facts` requires **every** configured mandatory fact; partial answers count as wrong. `Correct + target-cited` additionally requires the exact target citation string. `Wrongly refused` means the system refused an answerable question. Citation grading is an exact target-citation identity check, not per-response evidence-quotation/entailment verification.

---

## How I worked with the assistant

### Raw Prompt Trajectory
**Revision 4 remediation:** audit the benchmark grading defect that counted partial answers as correct, replace it with mandatory-fact grading, add regression tests, preserve the original evidence, and regenerate every table from a complete 120-row rerun. See [`REVISION_4_IMPLEMENTATION_REPORT.md`](REVISION_4_IMPLEMENTATION_REPORT.md).
1. Initial planning prompt: analyze `The_Relay_Assignment_Brief.pdf` and author `docs/IMPLEMENTATION_PLAN.md` without fabricating numbers or writing premature code.
2. Engineering critiques (Revisions 2 & 3): eliminate pre-verification user streaming, make TTFT vs. `user_first_token_ms` and `queue/work/retry` timers non-tautological, and separate Documented vs. Observed vs. Simulated limits.
3. Day 1 implementation & adversarial audits (Phases 1–2.2): inspect parsed corpus chunks, challenge heading heuristics, audit missing Moshi/Attention lines and 15 malformed hyphenated compounds, and enforce a full 52-test verification suite before promoting corpus artifacts.
4. Completion prompt (`"finish this project"`): promote remediated chunks, build Setting 3 Hybrid RRF reranking, complete `AdmissionGateway` + `CircuitBreaker` + `RetryPolicy`, run live HTTP/SSE benchmarks to generate verified `results.csv`, and compile final tables and write-up.

### Three Prompts That Mattered Most, Catches & Overrides
- **Three prompts that mattered most:** (1) Enforcing Revision 3's buffered-verifier safety invariant so unverified drafts are never emitted; (2) rejecting early Phase 2 heuristics when adversarial checks found table headers (`Model`, `Background`), missing `Acknowledgements` body lines, and 269 broken `word- word` hyphens; (3) forcing strict distinction between real measured vs. stand-in simulated metrics (`"not measured"` on live Groq).
- **Bugs caught in the assistant's output:** First, the parser joined lines with spaces before running hyphenation repair regex (`-\n`), leaving line-break hyphens unfixed in `data/processed/`. Second, `VerifierStep` checked `str(p.page_number) in cite_clean`, allowing `"1"` to falsely validate `"Page 10"`, and our numeric consistency checker caught `StandInService` extrapolating `"80 ms"` onto a Page 26 citation. Third, `BM25Okapi` scored `<= 0` on single-document test fixtures due to negative IDF. Fourth, the Revision 3 benchmark grader used `any(expected fact present)` and substring numeric matching, so partial answers (for example `4x` without `3.6x`, or `1.1` inside `1.10`) were falsely counted correct; Revision 4 requires all facts with numeric/word-boundary matching and adds regression tests.
- **Where I overruled the assistant:** I rejected heuristic keyword OOD filters, priority scheduling, and neural cross-encoders in favor of a strict FIFO gateway, Setting 3 subword TF-IDF + BM25 RRF reranking, and deterministic numeric/provenance verification invariants grounded directly in `results.csv`.
- **Distinctly mine:** Complete empirical reproducibility linking exact chunk byte hashes, line-level PDF completeness tests, strict latency conservation ($\epsilon < 0.01\text{ ms}$), and transparent reporting of unmeasured real-service credentials.

