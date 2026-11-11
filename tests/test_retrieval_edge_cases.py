"""Regression tests for retrieval index invalidation and BM25/reranker edge cases."""

from relay.retrieval.bm25_index import BM25Index
from relay.retrieval.reranker import HybridReranker


def _chunks(prefix: str, texts):
    return [
        {
            "chunk_id": f"{prefix}{i}",
            "paper_id": f"paper-{prefix}",
            "paper_title": f"Paper {prefix}",
            "version": "v1",
            "section_title": "Section",
            "page_number": i + 1,
            "text": text,
        }
        for i, text in enumerate(texts)
    ]


def test_bm25_non_positive_top_k_returns_no_results():
    index = BM25Index(_chunks("a", ["alpha transformer warmup", "beta moshi audio"]))
    assert index.search("alpha", top_k=0) == []
    assert index.search("alpha", top_k=-3) == []


def test_bm25_empty_query_returns_no_results():
    index = BM25Index(_chunks("a", ["alpha transformer warmup"]))
    assert index.search("", top_k=3) == []
    assert index.search("!!!", top_k=3) == []


def test_reranker_rebuilds_when_corpus_changes_but_length_is_equal():
    corpus_a = _chunks("a", ["alpha transformer warmup", "beta moshi audio"])
    corpus_b = _chunks("b", ["gamma hnet chunking", "delta dna sequence"])

    reranker = HybridReranker(corpus_a)
    assert set(reranker._chunk_ids) == {"a0", "a1"}

    results = reranker.search_and_rerank(
        "gamma hnet chunking",
        bm25_candidates=[],
        all_chunks=corpus_b,
        top_n=3,
        fetch_k=8,
    )

    # The old length-only cache check would keep the corpus-A matrix and IDs.
    assert set(reranker._chunk_ids) == {"b0", "b1"}
    assert results
    assert results[0]["chunk_id"] == "b0"


def test_reranker_empty_query_does_not_return_arbitrary_chunks():
    corpus = _chunks("a", ["alpha transformer warmup", "beta moshi audio"])
    reranker = HybridReranker(corpus)
    assert reranker.search_and_rerank("", [], corpus, top_n=3, fetch_k=8) == []
