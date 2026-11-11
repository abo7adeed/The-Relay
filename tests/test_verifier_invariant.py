"""Essential Acceptance Test 3: Verifier Safety Invariant.

Verifies that ungrounded drafts are NEVER emitted to users and verification is NEVER bypassed.
"""

import pytest
from relay.pipeline.verifier_step import VerifierStep, SAFE_REFUSAL_MESSAGE, VERIFICATION_FAILED_MESSAGE
from relay.pipeline.state import RequestContext
from relay.retrieval.tool import RetrievedPassage


def create_sample_passage():
    return RetrievedPassage(
        chunk_id="att_001",
        paper_id="1706.03762v7",
        paper_title="Attention Is All You Need",
        version="v7",
        section_title="5.4 Regularization",
        page_number=8,
        text="During training, we employed label smoothing of value epsilon_ls = 0.1.",
        score=10.5
    )


def test_verifier_approves_valid_grounded_citation():
    verifier = VerifierStep()
    ctx = RequestContext(query="What is the label smoothing value?")
    passage = create_sample_passage()
    draft = "During training, label smoothing of value 0.1 was used [Attention Is All You Need, 5.4 Regularization, Page 8]."

    decision = verifier.verify(ctx, draft, [passage], loop_count=0)

    assert decision.approved is True
    assert decision.status == "verified_grounded"
    assert decision.final_text == draft
    assert len(decision.extracted_citations) == 1
    assert "Page 8" in decision.extracted_citations[0]


def test_verifier_intercepts_unsupported_claim_without_citations():
    verifier = VerifierStep()
    ctx = RequestContext(query="What is the label smoothing value?")
    passage = create_sample_passage()
    draft = "The label smoothing value used was 0.5 according to the authors."

    # Loop 0: Recommends loop
    decision_loop0 = verifier.verify(ctx, draft, [passage], loop_count=0)
    assert decision_loop0.approved is False
    assert decision_loop0.loop_recommended is True
    assert decision_loop0.status == "needs_loop"

    # Loop 1: Loop exhausted -> Safe refusal, draft is suppressed!
    decision_loop1 = verifier.verify(ctx, draft, [passage], loop_count=1)
    assert decision_loop1.approved is False
    assert decision_loop1.status == "failed_verification"
    assert decision_loop1.final_text == SAFE_REFUSAL_MESSAGE
    assert draft not in decision_loop1.final_text


def test_verifier_intercepts_hallucinated_citations():
    verifier = VerifierStep()
    ctx = RequestContext(query="What is the label smoothing value?")
    passage = create_sample_passage()
    # Model cites a page or section that does NOT exist in retrieved passages
    draft = "The label smoothing value was 0.1 [Unknown Paper, Section 99, Page 999]."

    decision = verifier.verify(ctx, draft, [passage], loop_count=1)

    assert decision.approved is False
    assert decision.status == "failed_verification"
    assert decision.final_text == VERIFICATION_FAILED_MESSAGE


def test_verifier_passes_clean_model_refusal():
    verifier = VerifierStep()
    ctx = RequestContext(query="What was the batch size of LLaMA 3?")
    passage = create_sample_passage()
    draft = "The provided papers do not state any information about LLaMA 3."

    decision = verifier.verify(ctx, draft, [passage], loop_count=0)

    assert decision.approved is True
    assert decision.status == "verified_refusal"
    assert decision.final_text == SAFE_REFUSAL_MESSAGE


def test_verifier_preserves_valid_cited_answer_with_hedged_disclaimer():
    verifier = VerifierStep()
    ctx = RequestContext(query="How many warmup steps?")
    passage = RetrievedPassage(
        chunk_id="att_022",
        paper_id="1706.03762v7",
        paper_title="Attention Is All You Need",
        version="v7",
        section_title="5.3 Optimizer",
        page_number=7,
        text="We used warmup _ steps = 4000 .",
        score=12.0,
    )
    draft = (
        "Warmup steps = 4,000 were used [Attention Is All You Need, 5.3 Optimizer, Page 7]. "
        "Other details were not mentioned in the remaining passages."
    )
    decision = verifier.verify(ctx, draft, [passage], loop_count=0)
    assert decision.approved is True
    assert decision.status == "verified_grounded"
    assert "4,000" in decision.final_text

