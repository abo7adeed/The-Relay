# The Relay — Final Engineering Report

**Project:** Grounded Research Assistant Pipeline & Load-Resilient Gateway (`github.com/abo7adeed/The-Relay`)
**Corpus Scope:** `Attention Is All You Need` (`arXiv:1706.03762v7`), `Moshi` (`arXiv:2410.00037v1`), `H-Net / Dynamic Chunking` (`arXiv:2507.07955v2`)

---

## Page 1 — System Design and Engineering

### 1. Executive Summary
The Relay is a four-stage Python/FastAPI research assistant designed to answer technical questions strictly from the three authoritative arXiv papers above, citing `[Paper Title, Section Title, Page X]` or safely refusing when evidence is insufficient. Because multi-step LLM pipelines exhaust free-tier token buckets during synchronized bursts and fail catastrophically when unverified drafts stream prematurely, The Relay isolates every request behind a bounded FIFO `AdmissionGateway`, enforces a hard in-memory verification invariant prior to client byte release, sheds retrieval context under quota pressure, and trips fast-fail circuit breaking upon daily exhaustion.

### 2. Architecture and Request Lifecycle
Every call enters `AdmissionGateway.submit(ctx)` and flows through deterministic admission, processing, and verification guards:

1. **Admission & Queueing (`src/relay/gateway/gateway.py`):** Requests enter a bounded `asyncio.Queue(maxsize)` drained by `C` async workers (`concurrency_limit`). Excess bursts fail immediately (`429 rate_limited`, `0` tokens). On dequeue, `queue_wait_ms` is recorded (`t_dequeue - start_mono`), open `CircuitBreaker` state triggers fast-fail (< 3 ms), and expired `ctx.deadline_mono` requests terminate cleanly (`timeout`).
2. **Routing (`QueryRouter`):** Inspects explicit paper entities (e.g., *Transformer*, *Moshi/Mimi*, *H-Net/Dynamic Chunking*); single-paper queries filter to that paper, while multi-paper or ambiguous queries fall back conservatively to all 3 papers (`paper_filter=None`).
3. **Retrieval (`RetrievalTool.retrieve_passages`):** Executes lexical BM25 retrieval (`BM25Index`) with optional Setting 3 `HybridReranker` fusion, dynamically halving `effective_k -> max(2, effective_k // 2)` when `LocalRateLimiter.is_under_quota_pressure()` is true.
4. **Buffered Generation (`GeneratorStep`):** Invokes `GroqProvider` (or `StandInService` / `Respan` BYOK) with `temperature=0.0`, `max_tokens=600`. When `use_streaming=True`, SSE deltas buffer strictly in RAM while recording internal `model_first_token_ms`; zero unverified text reaches the caller.
5. **Verification & Re-Retrieval Branch (`VerifierStep`, max `loops=1`):** Regex-checks `[Title, Section, Page X]` against retrieved passages (exact paper title prefix AND integer page match — blocking page-substring bugs like Page `1` matching Page `10`), checks numerical claims (decimals and integers `>= 10` normalized across commas/PDF math spaces), and if citations fail while deadline remains, triggers one re-retrieval loop (`top_k=6`). Unverified outputs return `VERIFICATION_FAILED_MESSAGE`; `user_first_token_ms` fires strictly after verification succeeds (`PipelineConfig.enforce_verification_always=True` cannot be disabled).

### 3. Retrieval Design
PyMuPDF corpus ingestion (`src/relay/corpus/parser.py`, `chunker.py`) repairs line-break hyphens conservatively while preserving technical compounds, avoids promoting table column headers (`Model`, `Background`) to sections, and tags every chunk with physical PDF page numbers:

| Setting ID | Chunk Target / Avg Words | Index & Fetch Strategy | Reranking Mechanism |
| :--- | :--- | :--- | :--- |
| `setting_1_bm25_base` | `350w / 50o` (`297` chunks, `217.9` avg words) | `BM25Okapi`, `top_k=4` | None (pure BM25 lexical score) |
| `setting_2_bm25_granular` | `175w / 25o` (`481` chunks, `139.8` avg words) | `BM25Okapi`, `top_k=6` | None (pure BM25 lexical score) |
| `setting_3_hybrid_rerank` *(Selected)* | `350w / 50o` (`297` chunks, `217.9` avg words) | `BM25Okapi` (`fetch_k=8`) -> `top_k=3` | Hybrid RRF (`k=60`): BM25 + TF-IDF subword cosine (`#4gram` + word n-grams) + title/section term boost |

`HybridReranker` (`src/relay/retrieval/reranker.py`) constructs a normalized sparse TF-IDF matrix combining content tokens (`>=5`-char words expanded into 4-char subword hashes `#w[i:i+4]`), section headers, and paper titles, fusing candidate ranks via Reciprocal Rank Fusion without GPU inference overhead.

### 4. Reliability, Concurrency, and Caching
- **Deadlines & Latency Isolation:** Monotonic `ctx.deadline_mono` (`ttl_s`) is checked at enqueue, dequeue, before generator dispatch, prior to retry sleep, and prior to loop re-retrieval. `total_ms = queue_wait_ms + working_ms + retry_wait_ms` is conserved within `0.195 ms` measured error (validator threshold `15.0 ms`).
- **Rate Limiting & Context Shedding:** `LocalRateLimiter` tracks sliding 60s request and token windows; above `85%` of configured RPM/TPM limits, `quota_pressure=True` halves retrieval `top_k` (`3 -> 2`) to trim prompt tokens while preserving full `VerifierStep` enforcement.
- **Retries & Circuit Breaker:** `RetryPolicy` caps total attempts at `max_attempts=2` (one retry), applies full jittered exponential backoff (`base=0.6s`, `cap=3.0s`), enforces a global `15%` retry budget after `5` primary calls, aborts if `retry-after > remaining_ttl`, never retries `400/401/403/404` or `daily_quota_exhausted`, and isolates backoff inside `retry_wait_ms`. Daily exhaustion immediately trips `CircuitBreaker` to `OPEN` (`cooldown=3600s` or manual reset); `10` consecutive `5xx` errors also open it.
- **Implemented vs. Absent Caching:** PDFs are cached on disk (`fetcher.py`), chunk corpora JSONs are precomputed under `data/processed/`, `BM25Index`/`HybridReranker` indices stay resident in memory, and PyMuPDF table bounding boxes use `_TABLE_BBOX_CACHE` offline. **No query, embedding, or LLM response cache is implemented on the online request path.**

### 5. Key Engineering Decisions
- **Buffered Safety Over Speculative Streaming:** Withholding tokens until `VerifierStep` completes adds `~350–610 ms` post-model-TTFT delay (up to `610.2 ms` on slow trace `7afa5482...`), trading raw streaming responsiveness to guarantee zero unverified or fabricated numerical claims reach users.
- **Hybrid RRF Over Neural Cross-Encoders & Small Chunks:** `setting_3_hybrid_rerank` avoids GPU deployment complexity while shrinking prompt context by **27.7%** (`1,373.1` vs `1,900.3` tok/ans) at accuracy parity (`53.3%`), whereas finer `175w` chunking splits evidence across boundaries (`Q05`: `Mimi` vs `1.1 kbps`).
- **Strict FIFO Over Priority Queues:** Guarantees bounded non-starvation wait times and simple reasoning under overload bursts compared to multi-lane priority scheduling or external Redis queues.

---

## Page 2 — Experiments, Results and Limitations

### 1. Benchmark Methodology
All `120` matrix runs (`results.csv`, SHA-256 `441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`) executed on Windows 11 / Python 3.13 over localhost HTTP/SSE against `StandInService` (`src/relay/models/standin_service.py`), calibrated with Log-Normal TTFT ($\mu=5.45, \sigma=0.35$, ~233 ms base median), `6 ms`/token SSE decode, concurrency degradation factor `1 + 1.2 * max(0, active/5 - 1)`, and Groq `llama-3.1-8b-instant` reference caps (`30 RPM`, `6,000 TPM`, `500,000 TPD`, reference blend `$0.05/1M` input and `$0.08/1M` output):
- **Five Load Scenarios (`N=75`, Setting 3):** `steady` (`N=4`, `16.0s` spacing, `C=3, q=50, TTL=10s`), `burst` (`N=20`, `0.02s`, `C=2, q=18, TTL=2.2s`), `overload` (`N=24`, `0.15s`, `C=1, q=10, TTL=1.2s`), `limit_halved` (`N=12`, `2.0s`, `C=2, q=20, TTL=4.0s`, `15 RPM / 3,000 TPM`), `budget_exhausted` (`N=15`, `0.05s`, `C=3, q=50, TTL=10s`, `force_daily=True`).
- **Three Retrieval Evaluations (`N=45`, `15` questions x `3` settings):** TPM override `500k` isolates accuracy across `Q01–Q15` (`11` answerable + `2` false-friend traps `Q12–Q13` + `2` out-of-library negatives `Q14–Q15`), graded strictly via `src/relay/evaluation/grading.py` (all mandatory facts required; boundary regex `(?<![\d.])fact(?![\d.])`; decoupled exact `grade_citation`).

### 2. Quantitative Results (`results.csv` — All `stand-in`)

| Load Scenario (`source=stand-in`) | N | Dur (s) | Rate (/m) | FT p50/p95/p99 (ms) | TOT p50/p95/p99 (ms) | OK % | 429 % | TO % | Retries/Req | Peak Q | Tok/Ans | $/1k Ans |
| :--- | ---: | ---: | ---: | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `steady` | 4 | 48.9 | 5.0 | 622.1 / 886.6 / 916.0 | 622.1 / 886.6 / 916.0 | 100.0 | 0.0 | 0.0 | 0.00 | 1 | 978.0 | $0.0500 |
| `burst` | 20 | 1.9 | 2001.5 | 1064.7 / 1334.4 / 1673.4 | 1064.7 / 1334.4 / 1673.4 | 25.0 | 75.0 | 0.0 | 0.00 | 18 | 1118.2 | $0.0570 |
| `overload` | 24 | 3.6 | 399.7 | 1231.1 / 1772.0 / 1882.0 | 1231.1 / 1772.0 / 1882.0 | 20.8 | 37.5 | 41.7 | 0.00 | 10 | 1132.2 | $0.0579 |
| `limit_halved` | 12 | 22.1 | 32.6 | 11.4 / 3025.6 / 3027.3 | 11.4 / 3025.6 / 3027.3 | 16.7 | 83.3 | 0.0 | 0.17 | 1 | 1209.0 | $0.0616 |
| `budget_exhausted` | 15 | 0.9 | 1041.0 | 0.1 / 1.7 / 4.6 | 0.1 / 1.7 / 4.6 | 0.0 | 100.0 | 0.0 | 0.00 | 1 | 0.0 | $0.0000 |

| Retrieval Config (`N=15`, Stand-in) | Full Facts % | Cite OK % | Facts+Cite % | Wrong % | Refused OK % | Refused Wrong % | Tok/Ans | FT/TOT p50 / p95 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| Setting 1: `350w / 50o, k=4, no rerank` | 53.3% | 63.6% | 33.3% | 13.3% | 26.7% | 6.7% | 1900.3 | 579.1 / 737.8 |
| Setting 2: `175w / 25o, k=6, no rerank` | 46.7% | 63.6% | 33.3% | 13.3% | 26.7% | 13.3% | 1789.4 | 544.7 / 752.9 |
| Setting 3: `350w / 50o, k=8->3, rerank=Yes` | 53.3% | 54.5% | 33.3% | 13.3% | 26.7% | 6.7% | 1373.1 | 560.9 / 871.2 |

- **Latency Decomposition & Retry Pathology:** In `overload` request `7afa5482-51c8-46b4-8e71-eed4ecf95673` (`status=ok`), `total_ms=1914.041 ms` comprises `1147.689 ms` `queue_wait_ms` (`60.0%`) + `766.345 ms` `working_ms` (`40.0%`, $\epsilon=0.007\text{ ms}$); comparing `model_first_token_ms=1303.810 ms` shows `68.1%` occurred pre-TTFT and `31.9%` (`610.2 ms`) occurred post-TTFT buffering/verifying. Under `limit_halved`, request `3803d90f-e702-4925-9eb5-07205437281a` slept `3008.6 ms` (`retry_wait_ms`) on `retry-after: 3s` vs `18.956 ms` work (`~160x` inflation) and still failed `429` inside the 60s rolling window. Under `budget_exhausted`, request 1 trips `CircuitBreaker`; requests 2–15 fail fast in `0.1 ms` median (`p95=1.7 ms`, `0` tokens). At `1,373.1` blended tokens/answer, `6,000` TPM saturates at `~4.4` answers/min—well before a `30` RPM cap.

### 3. Live API Validation (`direct_groq` vs. Stand-In)
Live verification ran separately via `benchmarks/probe_groq.py` and `benchmarks/probe_live_pipeline.py` using local `.env` `GROQ_API_KEY` (`https://api.groq.com/openai/v1`). Results are strictly excluded from `results.csv`:

| Dimension / Limit | Documented Ref (`llama-3.1-8b-instant`) | Live Observed (`direct_groq`, `qwen/qwen3.8-27b`) | Verification Status & Evidence |
| :--- | :--- | :--- | :--- |
| Requests / min (`rpm`) | `30` | **`NOT VERIFIED` (`null`)** | Header `x-ratelimit-limit-requests=1000` (`reset=1m26.4s`) maps to RPD; RPM not emitted |
| Tokens / min (`tpm`) | `6,000` | **`8,000`** (`reset=269ms` / `6.2–13.3s` RAG) | `PASS` — HTTP header `x-ratelimit-limit-tokens` (`observed_limits.json`) |
| Requests / day (`rpd`) | `14,400` | **`1,000`** (`86,400s / 1,000 = 86.4s`) | `PASS` — HTTP header `x-ratelimit-limit-requests` (`observed_limits.json`) |
| Tokens / day (`tpd`) | `500,000` | **`NOT VERIFIED` (`null`)** | Not emitted in Groq HTTP response headers |
| Model availability / 429 burst | `llama-3.1-8b-instant` (`C=5` sim) | `/models`: `11` models (`llama-3.1-8b-instant` absent) | Single ping: `368.9 ms`; 5-query SSE check (`11s` spacing): `5/5` HTTP OK (`446.2–1,392.8 ms` TTFT, `6,370` in / `465` out); live 429 saturation & `RESPAN_API_KEY` gateway `NOT VERIFIED` |

### 4. Observed Limitations
- **Q02 Live Evaluation False Negative:** On live query `Q02` (`95aa0152...`, *warmup steps*), `qwen/qwen3.8-27b` cited `[Attention Is All You Need, 5.3 Optimizer, Page 7]` (`verification_status="verified_grounded"`, `citation_grade="citation_correct"`), and supplied `**4,000** warmup steps`. Unmodified `grade_eval_answer` marked it `refused_wrongly` because the opening phrase *"The provided papers do not state a single universal number..."* matched `_REFUSAL_INDICATORS` (`"do not state"`), and `_fact_present` rejects comma-formatted `"4,000"` against expected `"4000"`.
- **Lexical Retrieval Recall & Synthetic Variance:** Lexical BM25 + TF-IDF misses semantic synonyms (`Q10` misses *"routing module"* when asked *"without a tokenizer"*), yielding `6.7%` wrong refusal and `13.3%` incomplete-fact answers in Stand-in `setting_3` (`Q06`, `Q09`), while Stand-in omits real LPU hardware queuing and multi-tenant noise.

### 5. Conclusion
All `109/109` offline automated tests, `120`-row CSV schema/completeness checks, strict verification invariants, and live Direct Groq `qwen/qwen3.8-27b` SSE compatibility pass (`CI` run `43099888608` green on commit `a871a51`). Before production rollout, deployment requires an online semantic/response cache, dense semantic retrieval for vocabulary gaps (`Q10`), multi-worker external queueing (Redis), live verification of target model `RPM`/`TPD` burst saturation, and decoupling evaluation refusal heuristics from grounded, cited multi-paper answers (`Q02`).
