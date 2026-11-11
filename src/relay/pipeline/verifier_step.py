"""Step 4: Citation Verification & Grounding Safety Audit (MANDATORY INVARIANT)."""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Set
from src.relay.retrieval.tool import RetrievedPassage
from src.relay.pipeline.state import RequestContext

logger = logging.getLogger(__name__)

SAFE_REFUSAL_MESSAGE = "The provided papers do not state sufficient evidence to answer this question."
VERIFICATION_FAILED_MESSAGE = (
    "Unable to verify answer citations against authoritative paper context. "
    "Refusing response to maintain correctness."
)


def _normalize_numeric_tokens(text: str) -> str:
    """Collapse PDF math spacing around decimals (e.g. '0 . 1' -> '0.1', '12 . 5' -> '12.5')."""
    return re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", text)


@dataclass
class VerificationDecision:
    approved: bool
    status: str  # 'verified_grounded', 'verified_refusal', 'failed_verification', 'needs_loop', 'verifier_error'
    final_text: str
    extracted_citations: List[str]
    loop_recommended: bool = False
    suggested_query: str = ""


class VerifierStep:
    """Step 4: Strict grounding audit. Enforces that unverified drafts NEVER reach users."""

    def __init__(self, strict_citation_format: bool = True):
        self.strict_citation_format = strict_citation_format
        self.citation_pattern = re.compile(r"\[([^\]]+,\s*[^\]]+,\s*Page\s*\d+)\]")
        self.page_num_pattern = re.compile(r"page\s+(\d+)", re.IGNORECASE)

    def _citation_matches_passage(self, cite_inner: str, p: RetrievedPassage) -> bool:
        """Strictly match citation paper title and exact integer page number against a passage."""
        m_page = self.page_num_pattern.search(cite_inner)
        if not m_page:
            return False
        cited_page = int(m_page.group(1))
        if cited_page != p.page_number:
            return False

        cite_lower = cite_inner.lower()
        title_prefix = p.paper_title.lower()[:10]
        return (p.paper_id.lower() in cite_lower) or (title_prefix in cite_lower)

    def _check_numerical_consistency(self, draft_without_cites: str, matched_passages: List[RetrievedPassage]) -> bool:
        """Verify critical numeric claims (decimals or integers >= 10) appear in cited passages."""
        norm_draft = _normalize_numeric_tokens(draft_without_cites)
        # Extract decimal numbers and multi-digit numbers (excluding small list numbers like 1, 2, 8)
        draft_nums: Set[str] = set(re.findall(r"\b\d+\.\d+\b|\b\d{2,}\b", norm_draft))
        if not draft_nums:
            return True

        norm_evidence = _normalize_numeric_tokens(" ".join(p.text for p in matched_passages))
        evidence_nums: Set[str] = set(re.findall(r"\b\d+\.\d+\b|\b\d{2,}\b", norm_evidence))
        # Also allow common unit conversions mentioned directly in question/prose (e.g. 160ms vs 200ms) if numbers match
        for num in draft_nums:
            if num not in evidence_nums and num not in norm_evidence:
                logger.warning("Numerical claim %r in draft not found in cited passage evidence.", num)
                return False
        return True

    def verify(
        self,
        ctx: RequestContext,
        draft_text: str,
        passages: List[RetrievedPassage],
        loop_count: int = 0,
    ) -> VerificationDecision:
        """Audit generated draft against retrieved evidence passages."""
        # Case 1: Model explicitly refused because evidence is missing
        refusal_indicators = [
            "do not state", "does not state", "not contain information",
            "cannot answer", "not mentioned", "not provided", "do not provide",
        ]
        if any(ind in draft_text.lower() for ind in refusal_indicators):
            return VerificationDecision(
                approved=True,
                status="verified_refusal",
                final_text=SAFE_REFUSAL_MESSAGE,
                extracted_citations=[],
                loop_recommended=False,
            )

        # Case 2: Extract inline citations
        citations = self.citation_pattern.findall(draft_text)

        if not citations:
            if loop_count == 0:
                logger.info("Draft missing citations on loop 0. Recommending query expansion loop.")
                return VerificationDecision(
                    approved=False,
                    status="needs_loop",
                    final_text=SAFE_REFUSAL_MESSAGE,
                    extracted_citations=[],
                    loop_recommended=True,
                    suggested_query=f"{ctx.query} specification details",
                )
            logger.warning("Draft missing citations after loop. Suppressing draft with safe refusal.")
            return VerificationDecision(
                approved=False,
                status="failed_verification",
                final_text=SAFE_REFUSAL_MESSAGE,
                extracted_citations=[],
                loop_recommended=False,
            )

        # Case 3: Verify citations match retrieved passages strictly (exact page + paper)
        valid_citations: List[str] = []
        matched_passages: List[RetrievedPassage] = []
        for cite in citations:
            matched_p: Optional[RetrievedPassage] = None
            for p in passages:
                if self._citation_matches_passage(cite, p):
                    matched_p = p
                    break
            if matched_p is not None:
                valid_citations.append(f"[{cite}]")
                matched_passages.append(matched_p)

        if not valid_citations:
            if loop_count == 0:
                return VerificationDecision(
                    approved=False,
                    status="needs_loop",
                    final_text=SAFE_REFUSAL_MESSAGE,
                    extracted_citations=[],
                    loop_recommended=True,
                    suggested_query=f"{ctx.query} exact section",
                )
            return VerificationDecision(
                approved=False,
                status="failed_verification",
                final_text=VERIFICATION_FAILED_MESSAGE,
                extracted_citations=[],
                loop_recommended=False,
            )

        # Case 4: Verify numerical consistency against matched passages
        draft_no_cites = self.citation_pattern.sub("", draft_text)
        if not self._check_numerical_consistency(draft_no_cites, matched_passages):
            if loop_count == 0:
                return VerificationDecision(
                    approved=False,
                    status="needs_loop",
                    final_text=SAFE_REFUSAL_MESSAGE,
                    extracted_citations=[],
                    loop_recommended=True,
                    suggested_query=f"{ctx.query} exact numbers",
                )
            return VerificationDecision(
                approved=False,
                status="failed_verification",
                final_text=VERIFICATION_FAILED_MESSAGE,
                extracted_citations=[],
                loop_recommended=False,
            )

        return VerificationDecision(
            approved=True,
            status="verified_grounded",
            final_text=draft_text,
            extracted_citations=valid_citations,
            loop_recommended=False,
        )
