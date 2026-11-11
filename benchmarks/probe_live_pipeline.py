"""Controlled 5-query live SSE pipeline verification on Direct Groq or Respan gateway."""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))

from relay.config import PipelineConfig
from relay.evaluation.grading import grade_citation, grade_eval_answer
from relay.models.groq_provider import DEFAULT_GROQ_MODEL, GroqProvider
from relay.models.respan_gateway import RESPAN_GATEWAY_BASE_URL, build_gateway_provider
from relay.pipeline.engine import PipelineEngine
from relay.pipeline.state import RequestContext
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool

PROBE_QIDS = {"Q01", "Q02", "Q04", "Q08", "Q14"}


async def main() -> Dict[str, Any]:
    cfg = PipelineConfig()
    respan_key = (cfg.respan_api_key or os.getenv("RESPAN_API_KEY") or "").strip()
    groq_key = (cfg.groq_api_key or os.getenv("GROQ_API_KEY") or "").strip()

    if respan_key:
        provider_type = "respan_gateway"
        endpoint_url = RESPAN_GATEWAY_BASE_URL
        auth_mode = "respan_gateway_byok" if groq_key else "respan_gateway_managed"
        provider = build_gateway_provider(
            api_key=respan_key, model_id=DEFAULT_GROQ_MODEL, timeout_seconds=20.0, cfg=cfg
        )
    elif groq_key:
        provider_type = "direct_groq"
        endpoint_url = cfg.groq_base_url
        auth_mode = "direct_groq_api_key"
        provider = GroqProvider(
            api_key=groq_key,
            model_id=DEFAULT_GROQ_MODEL,
            base_url=cfg.groq_base_url,
            timeout_seconds=20.0,
        )
    else:
        raise RuntimeError("Missing both RESPAN_API_KEY and GROQ_API_KEY in .env/environment")

    idx = BM25Index.from_json_file("data/processed/chunks_setting_3.json")
    tool = RetrievalTool(idx, use_reranker=True, rerank_fetch_k=8, default_top_k=3)
    engine = PipelineEngine(tool, provider, max_loops=1, use_streaming=True)

    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        qs = json.load(f)

    # 4 answerable + 1 OOD negative = 5 live requests spaced by 11s to stay within 8,000 TPM.
    picked = [q for q in qs if q["id"] in PROBE_QIDS]
    live_rows: List[Dict[str, Any]] = []

    print(f"Running 5-query live SSE pipeline check via {provider_type} ({endpoint_url}) model={DEFAULT_GROQ_MODEL}")

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
        ans_grade = grade_eval_answer(q, res.response_text, res.verification_status)
        cite_grade = grade_citation(q, res.citations)

        print(
            f"\n[{q['id']}] status={res.status} | verify={res.verification_status} | "
            f"ans={ans_grade} | cite={cite_grade} | retries={res.retries}"
        )
        print(
            f"  TTFT={res.timers.user_first_token_ms:.1f} ms | ModelTTFT={res.timers.model_first_token_ms:.1f} ms | "
            f"Total={res.timers.total_ms:.1f} ms | Tokens in/out={res.tokens_in}/{res.tokens_out}"
        )
        print(f"  Citations: {res.citations}")
        print(f"  Preview: {res.response_text[:200]}")

        live_rows.append(
            {
                "qid": q["id"],
                "question_type": q["type"],
                "request_id": res.request_id,
                "provider_type": provider_type,
                "endpoint_url": endpoint_url,
                "model_configured": DEFAULT_GROQ_MODEL,
                "scenario": "real_steady",
                "source": "real",
                "start_time": start_iso,
                "first_token_ms": round(res.timers.user_first_token_ms, 3),
                "model_first_token_ms": round(res.timers.model_first_token_ms, 3),
                "total_ms": round(res.timers.total_ms, 3),
                "working_ms": round(res.timers.working_ms, 3),
                "retries": res.retries,
                "status": res.status,
                "tokens_in": res.tokens_in,
                "tokens_out": res.tokens_out,
                "verification_status": res.verification_status,
                "citations": res.citations,
                "answer_grade": ans_grade,
                "citation_grade": cite_grade,
                "response_preview": res.response_text[:280],
                "headers": dict(provider._last_headers),
            }
        )
        if i < len(picked) - 1:
            await asyncio.sleep(11.0)

    n_ok = sum(1 for r in live_rows if r["status"] == "ok")
    n_correct = sum(1 for r in live_rows if r["answer_grade"] == "correct")
    n_refused_ok = sum(1 for r in live_rows if r["answer_grade"] == "refused_correctly")
    n_cite_ok = sum(1 for r in live_rows if r["citation_grade"] == "citation_correct")

    payload: Dict[str, Any] = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider_type": provider_type,
        "endpoint_url": endpoint_url,
        "auth_mode": auth_mode,
        "model_configured": DEFAULT_GROQ_MODEL,
        "retrieval_setting": "setting_3_hybrid_rerank",
        "use_streaming": True,
        "pacing_sleep_seconds": 11.0,
        "summary": {
            "total_requests": len(live_rows),
            "succeeded": n_ok,
            "correct_answers": n_correct,
            "refused_correctly": n_refused_ok,
            "citation_correct": n_cite_ok,
            "total_tokens_in": sum(r["tokens_in"] for r in live_rows),
            "total_tokens_out": sum(r["tokens_out"] for r in live_rows),
        },
        "requests": live_rows,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/real_groq_probe_results.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print("\nSaved reconciled live probe results to data/real_groq_probe_results.json")
    return payload


if __name__ == "__main__":
    asyncio.run(main())
