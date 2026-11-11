import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("src"))
from relay.config import PipelineConfig
from relay.models.respan_gateway import build_gateway_provider
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool


async def main():
    cfg = PipelineConfig()
    key = (cfg.respan_api_key or os.getenv("RESPAN_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Missing RESPAN_API_KEY in .env")

    idx = BM25Index.from_json_file("data/processed/chunks_setting_3.json")
    tool = RetrievalTool(idx, use_reranker=True, rerank_fetch_k=8, default_top_k=3)
    provider = build_gateway_provider(
        api_key=key, model_id="qwen/qwen3.8-27b", timeout_seconds=20.0
    )
    engine = PipelineEngine(tool, provider, max_loops=1, use_streaming=True)

    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        qs = json.load(f)

    # Select 4 answerable + 1 OOD negative = 5 live requests (~6k total tokens across ~44s -> stays safely under 8k TPM!)
    picked = [q for q in qs if q["id"] in {"Q01", "Q02", "Q04", "Q08", "Q14"}]

    live_rows = []
    for i, q in enumerate(picked):
        start_iso = datetime.now(timezone.utc).isoformat()
        ctx = RequestContext(
            query=q["question"],
            scenario="real_steady",
            source="real",
            retrieval_setting="setting_3_hybrid_rerank",
            ttl_seconds=20.0,
        )
        res = await engine.process_request(ctx)
        print(f"\n[{q['id']}] status={res.status} | verify={res.verification_status} | retries={res.retries}")
        print(f"  Question: {q['question']}")
        print(f"  Model TTFT: {res.timers.model_first_token_ms:.1f} ms | User TTFT / Total: {res.timers.user_first_token_ms:.1f} ms | Tokens in/out: {res.tokens_in}/{res.tokens_out}")
        print(f"  Citations: {res.citations}")
        print(f"  Response: {res.response_text[:260]}")
        print(f"  Last Groq headers: {provider._last_headers}")

        live_rows.append({
            "qid": q["id"],
            "request_id": res.request_id,
            "scenario": "real_steady",
            "source": "real",
            "start_time": start_iso,
            "first_token_ms": round(res.timers.user_first_token_ms, 3),
            "model_first_token_ms": round(res.timers.model_first_token_ms, 3),
            "total_ms": round(res.timers.total_ms, 3),
            "working_ms": round(res.timers.working_ms, 3),
            "status": res.status,
            "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out,
            "verification_status": res.verification_status,
            "headers": provider._last_headers,
        })
        if i < len(picked) - 1:
            await asyncio.sleep(11.0)

    os.makedirs("data", exist_ok=True)
    with open("data/real_groq_probe_results.json", "w", encoding="utf-8") as f:
        json.dump(live_rows, f, indent=2)
    print("\nSaved live Groq test results to data/real_groq_probe_results.json")


if __name__ == "__main__":
    asyncio.run(main())

