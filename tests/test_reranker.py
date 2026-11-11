"""Tests for Setting 3 HybridReranker and Verifier numerical consistency."""

import json
import pytest
from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.tool import RetrievalTool
from relay.pipeline.verifier_step import VerifierStep, VERIFICATION_FAILED_MESSAGE
from relay.pipeline.state import RequestContext
from relay.retrieval.tool import RetrievedPassage


def test_setting_3_hybrid_reranker_ranks_and_caps():
    with open("data/processed/chunks_setting_3.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)
    idx = BM25Index(chunks)
    tool = RetrievalTool(idx, use_reranker=True, rerank_fetch_k=8, default_top_k=3)

    passages = tool.retrieve_passages("Adam optimizer warmup_steps 4000", paper_filter="Attention Is All You Need")
    assert len(passages) == 3
    assert any("4000" in p.text and p.page_number == 7 for p in passages)


def test_verifier_page_1_vs_page_10_regression_and_numeric_check():
    verifier = VerifierStep()
    ctx = RequestContext(query="What dropout rate?")
    passage_p10 = RetrievedPassage(
        chunk_id="c10",
        paper_id="1706.03762v7",
        paper_title="Attention Is All You Need",
        version="v7",
        section_title="5.4 Regularization",
        page_number=10,
        text="We use dropout rate 0.1 during training.",
        score=10.0,
    )

    # Bug regression: Draft cites Page 1 when passage is Page 10 ("1" in "10")
    draft_wrong_page = "We use dropout 0.1 [Attention Is All You Need, 5.4 Regularization, Page 1]."
    dec_page = verifier.verify(ctx, draft_wrong_page, [passage_p10], loop_count=1)
    assert dec_page.approved is False
    assert dec_page.final_text == VERIFICATION_FAILED_MESSAGE

    # Numeric inconsistency: Draft claims 0.99 when passage states 0.1
    draft_wrong_num = "We use dropout 0.99 [Attention Is All You Need, 5.4 Regularization, Page 10]."
    dec_num = verifier.verify(ctx, draft_wrong_num, [passage_p10], loop_count=1)
    assert dec_num.approved is False
    assert dec_num.final_text == VERIFICATION_FAILED_MESSAGE

