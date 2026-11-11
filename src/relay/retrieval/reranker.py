"""Hybrid lexical + TF-IDF subword cosine RRF reranker for Retrieval Setting 3."""

import math
import re
from collections import Counter
from typing import List, Dict, Any, Set, Optional
import numpy as np
from src.relay.retrieval.bm25_index import tokenize

STOPWORDS: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "what", "how", "many",
    "in", "of", "to", "for", "and", "or", "on", "at", "by", "from", "with",
    "does", "did", "do", "used", "use", "that", "this", "which", "both",
    "beyond", "than", "between", "about", "its", "their", "into",
}


def _extract_subword_features(text: str) -> List[str]:
    """Extract content words plus 4-char subword n-grams to handle morphological variations."""
    words = [w for w in tokenize(text) if w not in STOPWORDS]
    feats = list(words)
    for w in words:
        if len(w) >= 5:
            for i in range(len(w) - 3):
                feats.append(f"#{w[i:i+4]}")
    return feats


class HybridReranker:
    """Reranks chunks via Reciprocal Rank Fusion (BM25 + TF-IDF subword cosine + section boost)."""

    def __init__(self, chunks: Optional[List[Dict[str, Any]]] = None):
        self._vocab: Dict[str, int] = {}
        self._idf: Optional[np.ndarray] = None
        self._chunk_matrix: Optional[np.ndarray] = None
        self._chunk_ids: Dict[str, int] = {}
        if chunks:
            self._build_tfidf_index(chunks)

    def _build_tfidf_index(self, chunks: List[Dict[str, Any]]) -> None:
        doc_feats = []
        df: Counter = Counter()
        for idx, c in enumerate(chunks):
            self._chunk_ids[c["chunk_id"]] = idx
            full = f"{c.get('paper_title', '')} {c.get('section_title', '')} {c['text']}"
            feats = _extract_subword_features(full)
            doc_feats.append(feats)
            for f in set(feats):
                df[f] += 1

        n_docs = len(chunks)
        self._vocab = {term: i for i, (term, cnt) in enumerate(df.items()) if cnt >= 1}
        n_vocab = len(self._vocab)
        if n_docs == 0 or n_vocab == 0:
            return

        idf = np.zeros(n_vocab, dtype=np.float32)
        for term, i in self._vocab.items():
            idf[i] = math.log((1.0 + n_docs) / (1.0 + df[term])) + 1.0
        self._idf = idf

        mat = np.zeros((n_docs, n_vocab), dtype=np.float32)
        for doc_idx, feats in enumerate(doc_feats):
            counts = Counter(feats)
            for term, cnt in counts.items():
                v_idx = self._vocab.get(term)
                if v_idx is not None:
                    mat[doc_idx, v_idx] = (1.0 + math.log(cnt)) * idf[v_idx]
            norm = np.linalg.norm(mat[doc_idx])
            if norm > 0:
                mat[doc_idx] /= norm
        self._chunk_matrix = mat

    def _query_vector(self, query: str) -> Optional[np.ndarray]:
        if self._idf is None or not self._vocab:
            return None
        feats = _extract_subword_features(query)
        counts = Counter(feats)
        vec = np.zeros(len(self._vocab), dtype=np.float32)
        for term, cnt in counts.items():
            v_idx = self._vocab.get(term)
            if v_idx is not None:
                vec[v_idx] = (1.0 + math.log(cnt)) * self._idf[v_idx]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def search_and_rerank(
        self,
        query: str,
        bm25_candidates: List[Dict[str, Any]],
        all_chunks: List[Dict[str, Any]],
        top_n: int = 3,
        fetch_k: int = 8,
        paper_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Combine BM25 candidates and TF-IDF dense subword candidates via RRF + section reranking."""
        if not all_chunks:
            return []

        if self._chunk_matrix is None or len(self._chunk_ids) != len(all_chunks):
            self._build_tfidf_index(all_chunks)

        # Build pool: BM25 candidates + top TF-IDF subword cosine candidates
        pool_by_id: Dict[str, Dict[str, Any]] = {}
        bm25_rank: Dict[str, int] = {}
        for rank, c in enumerate(bm25_candidates[:fetch_k], start=1):
            cid = c["chunk_id"]
            pool_by_id[cid] = c.copy()
            bm25_rank[cid] = rank

        tfidf_scores_by_id: Dict[str, float] = {}
        tfidf_rank: Dict[str, int] = {}
        q_vec = self._query_vector(query)
        if q_vec is not None and self._chunk_matrix is not None:
            sims = self._chunk_matrix @ q_vec
            ranked_indices = np.argsort(-sims)
            t_rank = 1
            for idx in ranked_indices:
                c = all_chunks[int(idx)]
                if paper_filter:
                    pf = paper_filter.lower()
                    if pf not in c.get("paper_title", "").lower() and pf not in c.get("paper_id", "").lower():
                        continue
                cid = c["chunk_id"]
                score = float(sims[int(idx)])
                tfidf_scores_by_id[cid] = score
                if t_rank <= fetch_k:
                    tfidf_rank[cid] = t_rank
                    if cid not in pool_by_id:
                        item = c.copy()
                        item["bm25_score"] = 0.0
                        pool_by_id[cid] = item
                t_rank += 1
                if t_rank > fetch_k * 2 and cid in pool_by_id:
                    break

        if not pool_by_id:
            return []

        q_tokens = [t for t in tokenize(query) if t not in STOPWORDS]
        q_token_set = set(q_tokens)

        rescored: List[Dict[str, Any]] = []
        rrf_k = 10.0
        for cid, item in pool_by_id.items():
            r_bm25 = 1.0 / (rrf_k + bm25_rank[cid]) if cid in bm25_rank else 0.0
            r_tfidf = 1.0 / (rrf_k + tfidf_rank[cid]) if cid in tfidf_rank else 0.0
            sim_val = tfidf_scores_by_id.get(cid, 0.0)

            sec_lower = item.get("section_title", "").lower()
            sec_tokens = set(tokenize(sec_lower))
            sec_overlap = len(q_token_set & sec_tokens)
            sec_boost = min(0.06, 0.025 * sec_overlap)

            # De-prioritize bibliography and appendix entries relative to main prose sections
            ref_penalty = -0.04 if "reference" in sec_lower else (-0.02 if "appendix" in sec_lower else 0.0)

            combined = r_bm25 + r_tfidf + (0.12 * sim_val) + sec_boost + ref_penalty
            item["rerank_score"] = float(combined)
            item["bm25_score"] = float(combined)
            rescored.append(item)

        rescored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return rescored[:top_n]


# Backward-compatible alias
LexicalSectionReranker = HybridReranker
