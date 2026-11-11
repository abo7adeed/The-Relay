# The Relay — Submission Package

**Source Repository:** https://github.com/abo7adeed/The-Relay (`main`)
**Author:** Ahmed (Vaani Research ML Engineering Intern Assignment)

## Package Contents

- **Final Engineering Report (PDF, 2 pages):** [`THE_RELAY_FINAL_REPORT.pdf`](THE_RELAY_FINAL_REPORT.pdf)
- **Final Engineering Report Source (Markdown):** [`THE_RELAY_FINAL_REPORT.md`](THE_RELAY_FINAL_REPORT.md)
- **Complete 2-Page Assignment Write-Up (including Vaani Feedback & Raw Reflection):** [`THE_RELAY_2_PAGE_REPORT.md`](THE_RELAY_2_PAGE_REPORT.md)
- **Verified Raw Telemetry CSV:** [`results.csv`](results.csv) (`120` Stand-in rows: `45` retrieval evaluations across Settings 1–3 + `75` load scenarios across `steady`, `burst`, `overload`, `limit_halved`, and `budget_exhausted`; `sha256: 441b0004bf0bc493fe091ca23e9b3689c14b19c00868233ed0beb72cc23c3cdd`)
- **AI Interaction History (Verbatim):** [`AI_CONVERSATION_HISTORY.md`](AI_CONVERSATION_HISTORY.md) — Unedited chronological export of all `18` user prompts from the AI coding assistant session.
- **Evidence Directory (`evidence/`):**
  - [`evidence/observed_limits.json`](evidence/observed_limits.json) — Live Direct Groq header probe (`VERIFIED_ONLINE`, `https://api.groq.com/openai/v1`, model `qwen/qwen3.8-27b`, `8,000` TPM / `1,000` RPD; `rpm`/`tpd` explicitly `null`).
  - [`evidence/real_groq_probe_results.json`](evidence/real_groq_probe_results.json) — Controlled `5`-query live Direct Groq SSE pipeline run (`Q01`, `Q02`, `Q04`, `Q08`, `Q14`), kept strictly separate from `results.csv`.
  - [`evidence/benchmark_run_metadata.json`](evidence/benchmark_run_metadata.json) — Reproducibility hashes and scenario counts for `results.csv`.
- **Final Quality Gate Audit:** [`FINAL_SUBMISSION_AUDIT.md`](FINAL_SUBMISSION_AUDIT.md) — Requirement-by-requirement verification matrix.

## Reproduction (from Repository Root)

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v                     # 109 passed
python benchmarks/generate_report_tables.py    # Validates results.csv & recomputes Tables 2a, 2b, 3
```

*(Optional live Direct Groq verification requires setting `GROQ_API_KEY` in an untracked local `.env` file and running `python benchmarks/probe_groq.py` and `python benchmarks/probe_live_pipeline.py`. Zero credentials or `.env` files are included in this submission.)*