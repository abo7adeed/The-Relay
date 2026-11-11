"""Step 1: Conservative Query Router and Intent Analyzer."""

import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class RouteDecision:
    paper_filter: Optional[str] = None
    search_query: str = ""
    is_cross_paper: bool = False


class QueryRouter:
    """Conservative router identifying target paper domains without keyword drops."""

    def route(self, query: str) -> RouteDecision:
        q_lower = query.lower()

        # Check for explicit paper mentions
        mentions_transformer = any(k in q_lower for k in ["transformer", "vaswani", "attention is all you need", "multi-head", "encoder-decoder"])
        mentions_moshi = any(k in q_lower for k in ["moshi", "mimi", "kyutai", "rvq", "acoustic delay", "speech-text"])
        mentions_chunking = any(k in q_lower for k in ["dynamic chunking", "h-net", "hierarchical sequence", "tokenizer-free", "byte-level"])

        active_mentions = [mentions_transformer, mentions_moshi, mentions_chunking]
        mention_count = sum(1 for m in active_mentions if m)

        # Cross-paper or general queries search all 3 papers
        if mention_count > 1:
            return RouteDecision(paper_filter=None, search_query=query, is_cross_paper=True)
        elif mentions_transformer:
            return RouteDecision(paper_filter="Attention Is All You Need", search_query=query)
        elif mentions_moshi:
            return RouteDecision(paper_filter="Moshi", search_query=query)
        elif mentions_chunking:
            return RouteDecision(paper_filter="Dynamic Chunking", search_query=query)

        # Conservative default: do not filter by paper, search across entire library
        return RouteDecision(paper_filter=None, search_query=query)

