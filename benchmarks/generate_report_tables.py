"""Compiles Tables 1, 2a, 2b, and 3 directly from results.csv and data/observed_limits.json."""

import csv
from collections import defaultdict
from datetime import datetime
import json
import os
import sys
from typing import Dict, List, Any
import numpy as np

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))
from relay.telemetry.validator import validate_expected_scenarios, validate_results_csv


def pct_val(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    return float(np.percentile(vals, p))


def compute_tables(csv_path: str = "results.csv", limits_path: str = "data/observed_limits.json") -> Dict[str, Any]:
    ok, errs = validate_results_csv(csv_path)
    if not ok:
        raise RuntimeError(f"results.csv failed validation: {errs[:5]}")

    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    complete, completeness_errors = validate_expected_scenarios(rows)
    if not complete:
        raise RuntimeError(f"results.csv incomplete: {completeness_errors[:5]}")

    with open(limits_path, "r", encoding="utf-8") as f:
        limits_data = json.load(f)

    # Group by scenario for Tables 2a and 2b
    by_scen: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    by_ret: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        if r["scenario"] == "retrieval_eval":
            by_ret[r["retrieval_setting"]].append(r)
        else:
            by_scen[r["scenario"]].append(r)

    load_order = ["steady", "burst", "overload", "limit_halved", "budget_exhausted"]
    table2a = []
    table2b = []

    # Reference blended Groq llama-3.1-8b-instant pricing ($0.05/1M in, $0.08/1M out) for simulated cost math
    COST_IN_PER_1M = 0.05
    COST_OUT_PER_1M = 0.08

    for scen in load_order:
        srows = by_scen.get(scen, [])
        if not srows:
            continue
        n = len(srows)
        sources = sorted(set(r["source"] for r in srows))
        source_lbl = "/".join(sources)

        starts = [datetime.fromisoformat(r["start_time"]).timestamp() for r in srows]
        ends = [st + float(r["total_ms"]) / 1000.0 for st, r in zip(starts, srows)]
        # Offered rate uses submission span; total duration is end - min(start)
        submit_span_s = max(0.2, max(starts) - min(starts))
        duration_s = max(0.1, max(ends) - min(starts))
        offered_qpm = (n / submit_span_s) * 60.0 if n > 1 else 0.0

        fts = [float(r["first_token_ms"]) for r in srows]
        totals = [float(r["total_ms"]) for r in srows]

        table2a.append({
            "scenario": scen,
            "source": source_lbl,
            "requests": n,
            "duration_s": round(duration_s, 1),
            "offered_qpm": round(offered_qpm, 1),
            "ft_p50": round(pct_val(fts, 50), 1),
            "ft_p95": round(pct_val(fts, 95), 1),
            "ft_p99": round(pct_val(fts, 99), 1),
            "tot_p50": round(pct_val(totals, 50), 1),
            "tot_p95": round(pct_val(totals, 95), 1),
            "tot_p99": round(pct_val(totals, 99), 1),
        })

        n_ok = sum(1 for r in srows if r["status"] == "ok")
        n_rl = sum(1 for r in srows if r["status"] == "rate_limited")
        n_to = sum(1 for r in srows if r["status"] == "timeout")
        retries_avg = sum(int(r["retries"]) for r in srows) / n
        peak_q = max(int(r["peak_queue_length"]) for r in srows)
        toks_in = [int(r["tokens_in"]) for r in srows]
        toks_out = [int(r["tokens_out"]) for r in srows]
        toks_per_ans = sum(ti + to for ti, to in zip(toks_in, toks_out)) / max(1, n_ok) if n_ok > 0 else 0.0
        avg_in_ok = sum(toks_in) / max(1, n_ok) if n_ok > 0 else 0.0
        avg_out_ok = sum(toks_out) / max(1, n_ok) if n_ok > 0 else 0.0
        cost_per_1k = ((avg_in_ok * COST_IN_PER_1M / 1e6) + (avg_out_ok * COST_OUT_PER_1M / 1e6)) * 1000.0

        table2b.append({
            "scenario": scen,
            "succeeded_pct": round((n_ok / n) * 100.0, 1),
            "rate_limited_pct": round((n_rl / n) * 100.0, 1),
            "timed_out_pct": round((n_to / n) * 100.0, 1),
            "retries_per_req": round(retries_avg, 2),
            "peak_queue": peak_q,
            "tokens_per_answer": round(toks_per_ans, 1),
            "cost_per_1k": round(cost_per_1k, 4),
        })

    table3 = []
    ret_specs = [
        ("setting_1_bm25_base", "350w / 50o, k=4, rerank=No"),
        ("setting_2_bm25_granular", "175w / 25o, k=6, rerank=No"),
        ("setting_3_hybrid_rerank", "350w / 50o, k=8->3, rerank=Yes"),
    ]
    for sid, label in ret_specs:
        rrows = by_ret.get(sid, [])
        if not rrows:
            continue
        n = len(rrows)
        n_correct = sum(1 for r in rrows if r["answer_grade"] == "correct")
        n_wrong = sum(1 for r in rrows if r["answer_grade"] == "wrong")
        n_ref_ok = sum(1 for r in rrows if r["answer_grade"] == "refused_correctly")
        n_ref_wrong = sum(1 for r in rrows if r["answer_grade"] == "refused_wrongly")
        n_correct_cited = sum(
            1
            for r in rrows
            if r["answer_grade"] == "correct" and r["citation_grade"] == "citation_correct"
        )
        citation_checked = [r for r in rrows if r["citation_grade"] in ("citation_correct", "citation_wrong")]
        n_citation_correct = sum(1 for r in citation_checked if r["citation_grade"] == "citation_correct")
        citation_correct_pct = (
            round((n_citation_correct / len(citation_checked)) * 100.0, 1)
            if citation_checked else 0.0
        )
        # Note: percentages out of total 15 questions (or category-aware; brief puts sum/percentages on 15)
        toks_per_ans = sum(int(r["tokens_in"]) + int(r["tokens_out"]) for r in rrows) / n
        fts = [float(r["first_token_ms"]) for r in rrows]
        tots = [float(r["total_ms"]) for r in rrows]

        table3.append({
            "setting": label,
            "setting_id": sid,
            "questions": n,
            "correct_facts_pct": round((n_correct / n) * 100.0, 1),
            "citation_correct_pct": citation_correct_pct,
            "correct_and_cited_pct": round((n_correct_cited / n) * 100.0, 1),
            "wrong_pct": round((n_wrong / n) * 100.0, 1),
            "correctly_refused_pct": round((n_ref_ok / n) * 100.0, 1),
            "refused_wrongly_pct": round((n_ref_wrong / n) * 100.0, 1),
            "tokens_per_answer": round(toks_per_ans, 1),
            "ft_p50": round(pct_val(fts, 50), 1),
            "ft_p95": round(pct_val(fts, 95), 1),
            "tot_p50": round(pct_val(tots, 50), 1),
            "tot_p95": round(pct_val(tots, 95), 1),
        })

    # Find slow successful request trace with queue wait / retry or buffering lag (for Heading 4)
    slow_ok = [
        r for r in rows
        if r["scenario"] in ("burst", "overload", "limit_halved", "retrieval_eval")
        and r["status"] == "ok"
    ]
    slow_ok.sort(key=lambda r: float(r["total_ms"]), reverse=True)
    slow_trace = slow_ok[0] if slow_ok else None

    retry_fail = [
        r for r in rows
        if r["scenario"] in ("burst", "overload", "limit_halved")
        and int(r["retries"]) > 0
        and r["status"] != "ok"
    ]
    retry_fail.sort(key=lambda r: float(r["total_ms"]), reverse=True)
    retry_trace = retry_fail[0] if retry_fail else None

    return {
        "limits": limits_data,
        "table2a": table2a,
        "table2b": table2b,
        "table3": table3,
        "slow_trace": slow_trace,
        "retry_trace": retry_trace,
        "total_rows": len(rows),
    }


def print_summary(summary: Dict[str, Any]) -> None:
    print(f"Total rows in results.csv: {summary['total_rows']}\n")
    print("=== TABLE 2a: Latency ===")
    for r in summary["table2a"]:
        print(
            f"{r['scenario']:17s} | {r['source']:8s} | N={r['requests']:2d} | dur={r['duration_s']:5.1f}s | "
            f"rate={r['offered_qpm']:6.1f}/m | FT p50/p95/p99={r['ft_p50']}/{r['ft_p95']}/{r['ft_p99']} | "
            f"TOT p50/p95/p99={r['tot_p50']}/{r['tot_p95']}/{r['tot_p99']}"
        )
    print("\n=== TABLE 2b: Outcomes ===")
    for r in summary["table2b"]:
        print(
            f"{r['scenario']:17s} | ok={r['succeeded_pct']:5.1f}% | 429={r['rate_limited_pct']:5.1f}% | "
            f"to={r['timed_out_pct']:5.1f}% | retries={r['retries_per_req']:4.2f} | peakQ={r['peak_queue']:2d} | "
            f"tok/ans={r['tokens_per_answer']:6.1f} | $/1k=${r['cost_per_1k']:.4f}"
        )
    print("\n=== TABLE 3: Retrieval Settings ===")
    for r in summary["table3"]:
        print(
            f"{r['setting']:30s} | N={r['questions']} | facts={r['correct_facts_pct']:5.1f}% | "
            f"cite_ok={r['citation_correct_pct']:5.1f}% | facts+cite={r['correct_and_cited_pct']:5.1f}% | "
            f"wrong={r['wrong_pct']:4.1f}% | ref_ok={r['correctly_refused_pct']:5.1f}% | "
            f"ref_wrong={r['refused_wrongly_pct']:4.1f}% | "
            f"tok/ans={r['tokens_per_answer']:6.1f} | FT p50/p95={r['ft_p50']}/{r['ft_p95']} | "
            f"TOT p50/p95={r['tot_p50']}/{r['tot_p95']}"
        )
    if summary["slow_trace"]:
        s = summary["slow_trace"]
        print("\n=== SLOW SUCCESSFUL REQUEST TRACE (Heading 4) ===")
        print(json.dumps(s, indent=2))
    if summary.get("retry_trace"):
        print("\n=== RETRY-WORSENED REQUEST TRACE (Heading 6) ===")
        print(json.dumps(summary["retry_trace"], indent=2))


if __name__ == "__main__":
    summary = compute_tables()
    print_summary(summary)

