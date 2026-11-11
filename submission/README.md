# The Relay — Submission Package

**Source Repository:** https://github.com/abo7adeed/The-Relay (`main`)
**Author:** Ahmed (Vaani Research ML Engineering Intern Assignment)

## Package Contents

- [`THE_RELAY_2_PAGE_REPORT.md`](THE_RELAY_2_PAGE_REPORT.md) — Engineering report satisfying every required section, architecture decision, failure case study, Vaani feedback, reflection, and benchmark Tables 1, 2a, 2b, and 3.
- [`results.csv`](results.csv) — Verified 120-row per-request Stand-in telemetry (`45` retrieval evaluations across Settings 1–3 + `75` load requests across `steady`, `burst`, `overload`, `limit_halved`, and `budget_exhausted`; `sha256: 441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`).
- [`AI_CONVERSATION_HISTORY.md`](AI_CONVERSATION_HISTORY.md) — Unedited verbatim chronological export of all 18 user prompts from the AI coding assistant session.
- [`evidence/observed_limits.json`](evidence/observed_limits.json) — Live Direct Groq probe (`VERIFIED_ONLINE`, `https://api.groq.com/openai/v1`, model `qwen/qwen3.8-27b`, observed `8,000` TPM / `1,000` RPD; `rpm`/`tpd` explicitly `null`).
- [`evidence/real_groq_probe_results.json`](evidence/real_groq_probe_results.json) — Controlled 5-query live Direct Groq SSE pipeline run (`Q01`, `Q02`, `Q04`, `Q08`, `Q14`), kept strictly separate from `results.csv`.
- [`evidence/benchmark_run_metadata.json`](evidence/benchmark_run_metadata.json) — Reproducibility hashes and counts for `results.csv`.
- [`FINAL_SUBMISSION_AUDIT.md`](FINAL_SUBMISSION_AUDIT.md) — Complete requirement-by-requirement verification audit.

## Reproduction (from repository root)

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v                     # 109 passed
python benchmarks/generate_report_tables.py    # Validates results.csv & recomputes Tables 2a, 2b, 3
```

*(Optional live Direct Groq verification requires setting your own `GROQ_API_KEY` in a local untracked `.env` file and running `python benchmarks/probe_groq.py` and `python benchmarks/probe_live_pipeline.py`. No credentials or `.env` files are included in this submission.)*

