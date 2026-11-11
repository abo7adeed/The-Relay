"""Fetcher module for acquiring and verifying authoritative arXiv research papers."""

import os
import urllib.request
import logging
from typing import Dict

logger = logging.getLogger(__name__)

ARXIV_PAPERS: Dict[str, Dict[str, str]] = {
    "1706.03762v7": {
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/pdf/1706.03762v7",
        "filename": "1706.03762v7.pdf",
        "version": "v7",
        "expected_min_bytes": 1_000_000,
    },
    "2410.00037v1": {
        "title": "Moshi: a speech-text foundation model for real-time dialogue",
        "url": "https://arxiv.org/pdf/2410.00037v1",
        "filename": "2410.00037v1.pdf",
        "version": "v1",
        "expected_min_bytes": 3_000_000,
    },
    "2507.07955v2": {
        "title": "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        "url": "https://arxiv.org/pdf/2507.07955v2",
        "filename": "2507.07955v2.pdf",
        "version": "v2",
        "expected_min_bytes": 1_000_000,
    },
}


def download_papers(output_dir: str = "data/raw_papers") -> Dict[str, str]:
    """Download the 3 target arXiv PDFs if not already cached and verified.

    Returns:
        Dict mapping paper_id to local file path.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths: Dict[str, str] = {}

    for paper_id, meta in ARXIV_PAPERS.items():
        dest = os.path.join(output_dir, meta["filename"])
        if os.path.exists(dest) and os.path.getsize(dest) >= meta["expected_min_bytes"]:
            logger.info("Paper %s verified in cache: %s (%d bytes)", paper_id, dest, os.path.getsize(dest))
            paths[paper_id] = dest
            continue

        logger.info("Downloading %s from %s...", paper_id, meta["url"])
        req = urllib.request.Request(
            meta["url"],
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TheRelayBenchmark/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())

        actual_size = os.path.getsize(dest)
        if actual_size < meta["expected_min_bytes"]:
            raise IOError(f"Downloaded file {dest} too small ({actual_size} bytes). Expected >= {meta['expected_min_bytes']}")
        
        logger.info("Successfully fetched %s (%d bytes)", paper_id, actual_size)
        paths[paper_id] = dest

    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_papers()

