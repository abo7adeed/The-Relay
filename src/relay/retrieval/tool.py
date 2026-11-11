"""Passage retrieval tool implementing the formal pipeline tool call."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from src.relay.retrieval.bm25_index import BM25Index
from src.relay.retrieval.reranker import HybridReranker


@dataclass
class RetrievedPassage:
    chunk_id: str
    paper_id: str
    paper_title: str
    version: str
    section_title: str
    page_number: int
    text: str
    score: float

    @property
    def citation(self) -> str:
        return f"[{self.paper_title}, {self.section_title}, Page {self.page_number}]"


class RetrievalTool:
    """Tool wrapper providing formal retrieve_passages invocation."""

    def __init__(
        self,
        index: BM25Index,
        use_reranker: bool = False,
        rerank_fetch_k: int = 8,
        default_top_k: int = 4,
    ):
        self.index = index
        self.use_reranker = use_reranker
        self.rerank_fetch_k = rerank_fetch_k
        self.default_top_k = default_top_k
        self.reranker = HybridReranker(index.chunks) if use_reranker else None

    def retrieve_passages(
        self,
        query: str,
        top_k: Optional[int] = None,
        paper_filter: Optional[str] = None,
        quota_pressure: bool = False,
    ) -> List[RetrievedPassage]:
        """Execute passage retrieval over the authoritative paper library.

        Args:
            query: Question or technical keywords.
            top_k: Number of passages to return. Uses default_top_k if None.
            paper_filter: Optional paper restriction.
            quota_pressure: When True, dynamically sheds context (reduces k) to stay under TPM limits.

        Returns:
            List of RetrievedPassage objects.
        """
        effective_k = top_k if top_k is not None else self.default_top_k
        if quota_pressure and effective_k > 2:
            effective_k = max(2, effective_k // 2)

        if self.use_reranker and self.reranker is not None:
            fetch_k = max(self.rerank_fetch_k, effective_k * 2)
            bm25_candidates = self.index.search(query=query, top_k=fetch_k, paper_filter=paper_filter)
            raw_results = self.reranker.search_and_rerank(
                query=query,
                bm25_candidates=bm25_candidates,
                all_chunks=self.index.chunks,
                top_n=effective_k,
                fetch_k=fetch_k,
                paper_filter=paper_filter,
            )
        else:
            raw_results = self.index.search(query=query, top_k=effective_k, paper_filter=paper_filter)

        passages: List[RetrievedPassage] = []
        for r in raw_results:
            passages.append(RetrievedPassage(
                chunk_id=r["chunk_id"],
                paper_id=r["paper_id"],
                paper_title=r["paper_title"],
                version=r["version"],
                section_title=r["section_title"],
                page_number=r["page_number"],
                text=r["text"],
                score=r.get("rerank_score", r.get("bm25_score", 0.0)),
            ))
        return passages

    def format_context_for_prompt(self, passages: List[RetrievedPassage]) -> str:
        """Format passages into an unambiguous cited context string for the LLM."""
        if not passages:
            return "NO RELEVANT PASSAGES FOUND IN THE LIBRARY."

        blocks = []
        for i, p in enumerate(passages, start=1):
            blocks.append(
                f"--- EVIDENCE PASSAGE {i} {p.citation} ---\n{p.text}"
            )
        return "\n\n".join(blocks)
