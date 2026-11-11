"""In-memory BM25 lexical index using rank_bm25."""

import re
import json
from typing import List, Dict, Any, Optional
from rank_bm25 import BM25Okapi


def tokenize(text: str) -> List[str]:
    """Simple alphanumeric tokenizer for BM25."""
    return re.findall(r'\b[a-zA-Z0-9_\-]+\b', text.lower())


class BM25Index:
    """In-memory lexical search index over chunk corpus."""

    def __init__(self, chunks: List[Dict[str, Any]]):
        self.chunks = chunks
        self.corpus_tokens = [tokenize(c["text"]) for c in chunks]
        if self.corpus_tokens:
            self.bm25: Optional[BM25Okapi] = BM25Okapi(self.corpus_tokens)
        else:
            self.bm25 = None

    @classmethod
    def from_json_file(cls, path: str) -> "BM25Index":
        with open(path, "r", encoding="utf-8") as f:
            chunks = json.load(f)
        return cls(chunks)

    def search(
        self,
        query: str,
        top_k: int = 4,
        paper_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the corpus and return top_k chunks.

        Args:
            query: The search query string.
            top_k: Maximum chunks to retrieve.
            paper_filter: Optional substring or paper_id to restrict corpus search.

        Returns:
            List of matching chunk dicts with 'bm25_score' added.
        """
        if not self.bm25 or not self.chunks:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)

        # Fallback term-overlap scoring when tiny corpora (N <= 3) yield non-positive BM25 IDF
        if len(self.chunks) <= 3 and all(s <= 0.0 for s in scores):
            q_set = set(query_tokens)
            scores = [float(len(q_set & set(doc_toks))) for doc_toks in self.corpus_tokens]

        # Pair scores with chunks
        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        results = []
        for idx in ranked_indices:
            score = scores[idx]
            if score <= 0.0:
                continue
            chunk = self.chunks[idx].copy()
            chunk["bm25_score"] = float(score)

            if paper_filter:
                filter_lower = paper_filter.lower()
                title_lower = chunk.get("paper_title", "").lower()
                id_lower = chunk.get("paper_id", "").lower()
                if filter_lower not in title_lower and filter_lower not in id_lower:
                    continue

            results.append(chunk)
            if len(results) >= top_k:
                break

        return results
