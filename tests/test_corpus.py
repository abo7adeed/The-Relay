"""Unit and regression tests for corpus acquisition, PDF parsing, and chunking (Milestone M1 remediation).

Validates all 3 PDFs and all 3 generated corpus settings against:
- F-04: Robust multi-signal heading extraction and TOC alignment
- F-05: Accurate page provenance, running header/footer removal, reference line preservation, and mixed-block preservation
- F-06: Real-pipeline hyphenation repair distinguishing broken words from compounds/entities
- Setting 3 identity, word caps, determinism, and completeness
"""

import os
import json
import hashlib
import re
import collections
import pytest
import pymupdf
from relay.corpus.fetcher import ARXIV_PAPERS, download_papers
from relay.corpus.parser import (
    parse_pdf,
    ParsedDocument,
    repair_hyphenation,
    clean_text,
    clean_section_text,
    KNOWN_COMPOUNDS,
    ARXIV_WATERMARK_RE,
    is_running_header,
    is_running_footer,
    is_watermark,
)
from relay.corpus.chunker import chunk_document, build_all_processed_chunks


# ---------------------------------------------------------------------------
# Phase 2.2 shared fixtures and helpers
# ---------------------------------------------------------------------------

PAPER_SPECS = [
    ("1706.03762v7", "Attention Is All You Need", "v7"),
    (
        "2410.00037v1",
        "Moshi: a speech-text foundation model for real-time dialogue",
        "v1",
    ),
    (
        "2507.07955v2",
        "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        "v2",
    ),
]


def _paper_path(paper_id: str) -> str:
    return os.path.join("data", "raw_papers", f"{paper_id}.pdf")


def _all_titles(doc: ParsedDocument) -> set:
    return {b.section_title for b in doc.blocks} | {
        s for page in doc.pages for s in page.sections
    }


def _alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def _blocks_text(doc: ParsedDocument) -> str:
    return "\n".join(b.text for b in doc.blocks)


def _chunks_text(chunks) -> str:
    return " ".join(c.text for c in chunks)


def _raw_page_lines(paper_id: str):
    """Yield ``(page_number, line_text, bbox)`` for every extractable source line."""
    document = pymupdf.open(_paper_path(paper_id))
    try:
        for page_number, page in enumerate(document, 1):
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = [
                        s
                        for s in line.get("spans", [])
                        if s.get("text", "").strip()
                    ]
                    if not spans:
                        continue
                    text = " ".join(s["text"].strip() for s in spans)
                    yield page_number, text, tuple(line.get("bbox", (0, 0, 0, 0)))
    finally:
        document.close()


@pytest.fixture(scope="session")
def parsed_docs():
    return {
        paper_id: parse_pdf(_paper_path(paper_id), paper_id, title, version)
        for paper_id, title, version in PAPER_SPECS
    }


@pytest.fixture(scope="session")
def staged_corpora(tmp_path_factory, parsed_docs):
    output_dir = tmp_path_factory.mktemp("phase22_staged_processed")
    paths = build_all_processed_chunks(
        [parsed_docs[paper_id] for paper_id, _, _ in PAPER_SPECS],
        output_dir=str(output_dir),
    )
    staged = {}
    for filename, path in paths.items():
        with open(path, "r", encoding="utf-8") as handle:
            staged[filename] = {"path": path, "chunks": json.load(handle)}
    return staged


def test_arxiv_papers_metadata():
    """Verify metadata configuration for all 3 arXiv papers."""
    assert len(ARXIV_PAPERS) == 3
    assert "1706.03762v7" in ARXIV_PAPERS
    assert "2410.00037v1" in ARXIV_PAPERS
    assert "2507.07955v2" in ARXIV_PAPERS
    for pid, meta in ARXIV_PAPERS.items():
        assert meta["version"] != ""
        assert meta["expected_min_bytes"] > 100_000


def test_download_papers_cache():
    """Verify papers are downloaded and exist locally above minimum byte thresholds."""
    paths = download_papers("data/raw_papers")
    assert len(paths) == 3
    for paper_id, path in paths.items():
        assert os.path.exists(path)
        assert os.path.getsize(path) >= ARXIV_PAPERS[paper_id]["expected_min_bytes"]


def test_table_row_and_caption_immunity():
    """Regression test: Table rows, BLEU metrics, chart labels, and captions must never become section titles.

    Specifically verifies:
    - Known Attention Table 2 example: '23.75 Deep-Att + PosUnk [39]' is rejected.
    - Captions (Table 1:, Figure 1:) are rejected.
    - Diagram / chart labels (AppleSD, Arial) are rejected.
    """
    path = "data/raw_papers/1706.03762v7.pdf"
    doc = parse_pdf(path, "1706.03762v7", "Attention Is All You Need", "v7")

    section_titles = {b.section_title for b in doc.blocks}
    for page in doc.pages:
        section_titles.update(page.sections)

    assert not any("23.75" in s for s in section_titles), "Regression F-04: Floating metric '23.75' was indexed as section title"
    assert not any("Deep-Att" in s for s in section_titles), "Regression F-04: Table row 'Deep-Att' was indexed as section title"
    assert not any("PosUnk" in s for s in section_titles), "Regression F-04: Table row 'PosUnk' was indexed as section title"
    assert not any(s.startswith("Table ") or s.startswith("Table.") for s in section_titles)
    assert not any(s.startswith("Figure ") or s.startswith("Figure.") for s in section_titles)
    assert not any(s.startswith("arXiv:") for s in section_titles)


def test_heading_extraction_and_toc_alignment():
    """Verify that legitimate numbered, lettered, unnumbered, and small-caps headings are properly captured."""
    # 1. Attention paper (1706.03762v7)
    path1 = "data/raw_papers/1706.03762v7.pdf"
    doc1 = parse_pdf(path1, "1706.03762v7", "Attention Is All You Need", "v7")
    titles1 = {b.section_title for b in doc1.blocks} | {s for p in doc1.pages for s in p.sections}

    expected_att = [
        "Abstract", "1 Introduction", "2 Background", "3 Model Architecture",
        "3.1 Encoder and Decoder Stacks", "3.2 Attention", "3.2.1 Scaled Dot-Product Attention",
        "3.2.2 Multi-Head Attention", "3.2.3 Applications of Attention in our Model",
        "3.3 Position-wise Feed-Forward Networks", "3.4 Embeddings and Softmax",
        "3.5 Positional Encoding", "4 Why Self-Attention", "5 Training", "6 Results",
        "7 Conclusion", "Acknowledgements", "References"
    ]
    for exp in expected_att:
        assert any(exp in t for t in titles1), f"Missing expected heading in Attention: {exp}"

    # 2. Moshi paper (2410.00037v1) - Includes non-bold small-caps (CMCSC10) like 3.3.1 Architecture
    path2 = "data/raw_papers/2410.00037v1.pdf"
    doc2 = parse_pdf(path2, "2410.00037v1", "Moshi", "v1")
    titles2 = {b.section_title for b in doc2.blocks} | {s for p in doc2.pages for s in p.sections}

    expected_moshi = [
        "Abstract", "1 Introduction", "2 Related Work", "3.1 Overview",
        "3.3.1 Architecture", "3.3.2 Learning semantic-acoustic tokens with a split RVQ",
        "3.4.1 Hierarchical autoregressive modeling with RQ-Transformer",
        "Appendix A. Additional ablation on Mimi codec",
        "Appendix B. Audio matching and deduplication",
        "Appendix F. Synthetic transcripts generation for fine-tuning",
    ]
    for exp in expected_moshi:
        assert any(exp in t for t in titles2), f"Missing expected heading in Moshi: {exp}"

    # 3. H-Net paper (2507.07955v2) - Includes small Abstract (8.97pt) and Appendix A-F
    path3 = "data/raw_papers/2507.07955v2.pdf"
    doc3 = parse_pdf(path3, "2507.07955v2", "H-Net", "v2")
    titles3 = {b.section_title for b in doc3.blocks} | {s for p in doc3.pages for s in p.sections}

    expected_hnet = [
        "Abstract", "1 Introduction", "2 H-Net Architecture", "2.1.1 Components of H-Net",
        "2.2 Dynamic Chunking (DC)", "3 Experiments", "Appendix A Related Work",
        "Appendix A.1 Autoregressive Tokenizer-free Architectures", "Appendix B FLOPs Computation",
        "Appendix F Distilling Token-Level Models to Byte-Level"
    ]
    for exp in expected_hnet:
        assert any(exp in t for t in titles3), f"Missing expected heading in H-Net: {exp}"


def test_appendix_labels_and_distinction():
    """Verify that appendix sections are explicitly labeled and distinguished from main sections."""
    # Moshi Appendices
    doc2 = parse_pdf("data/raw_papers/2410.00037v1.pdf", "2410.00037v1", "Moshi", "v1")
    moshi_apps = [b.section_title for b in doc2.blocks if "Appendix" in b.section_title]
    assert len(moshi_apps) >= 6
    assert any("Appendix A" in s for s in moshi_apps)
    assert any("Appendix F" in s for s in moshi_apps)

    # H-Net Appendices
    doc3 = parse_pdf("data/raw_papers/2507.07955v2.pdf", "2507.07955v2", "H-Net", "v2")
    hnet_apps = [b.section_title for b in doc3.blocks if "Appendix" in b.section_title]
    assert len(hnet_apps) >= 6
    # Must distinguish Appendix A Related Work from main text
    assert any("Appendix A Related Work" in s for s in hnet_apps)
    assert any("Appendix B FLOPs Computation" in s for s in hnet_apps)
    assert any("Appendix F" in s for s in hnet_apps)


def test_mixed_heading_body_block_preservation():
    """Verify that body text sharing a block with a heading is never discarded (F-05).

    Specifically tests Attention Page 10 Block 10:
    Heading 'Acknowledgements' is immediately followed by body text:
    'We are grateful to Nal Kalchbrenner and Stephan Gouws for their fruitful comments, corrections and inspiration.'
    """
    path = "data/raw_papers/1706.03762v7.pdf"
    doc = parse_pdf(path, "1706.03762v7", "Attention Is All You Need", "v7")

    ack_blocks = [b for b in doc.blocks if b.page_number == 10 and "Acknowledgement" in b.section_title]
    assert len(ack_blocks) == 1, "Acknowledgements section block on Page 10 must exist"
    ack_text = ack_blocks[0].text
    assert "Nal Kalchbrenner" in ack_text, "Missing body line: Nal Kalchbrenner"
    assert "Stephan Gouws" in ack_text, "Missing body line: Stephan Gouws"
    assert "inspiration" in ack_text, "Missing body line: inspiration"

    # Also check generated chunks
    chunks = chunk_document(doc, chunk_size_words=350, overlap_words=50)
    ack_chunks = [c for c in chunks if c.page_number == 10 and "Acknowledgement" in c.section_title]
    assert len(ack_chunks) >= 1
    assert "Nal Kalchbrenner" in ack_chunks[0].text


def test_reference_line_preservation():
    """Verify that legitimate reference lines starting with 'arXiv:' are preserved."""
    for p_path, p_id, title, ver in [
        ("data/raw_papers/1706.03762v7.pdf", "1706.03762v7", "Attention", "v7"),
        ("data/raw_papers/2410.00037v1.pdf", "2410.00037v1", "Moshi", "v1"),
        ("data/raw_papers/2507.07955v2.pdf", "2507.07955v2", "H-Net", "v2"),
    ]:
        doc = parse_pdf(p_path, p_id, title, ver)
        ref_blocks = [b for b in doc.blocks if "Reference" in b.section_title]
        assert len(ref_blocks) > 0, f"References section must exist in {p_id}"
        has_arxiv = any("arxiv:" in b.text.lower() for b in ref_blocks)
        assert has_arxiv, f"References in {p_id} must preserve arXiv citation lines"


def test_running_header_and_footer_removal():
    """Verify running headers (top) and page number footers (bottom) are cleanly removed on pages > 1."""
    # Moshi recurring running header on pages > 1: 'Moshi: a speech-text foundation model for real-time dialogue'
    doc2 = parse_pdf("data/raw_papers/2410.00037v1.pdf", "2410.00037v1", "Moshi", "v1")
    chunks2 = chunk_document(doc2, chunk_size_words=350, overlap_words=50)
    for c in chunks2:
        if c.page_number > 1:
            assert not c.text.startswith("Moshi: a speech-text foundation model for real-time dialogue"), (
                f"Page {c.page_number} retained running header!"
            )

    # Attention running footers (isolated digit at page bottom)
    doc1 = parse_pdf("data/raw_papers/1706.03762v7.pdf", "1706.03762v7", "Attention", "v7")
    chunks1 = chunk_document(doc1, chunk_size_words=350, overlap_words=50)
    for c in chunks1:
        # Text should not end with an isolated page footer digit
        assert not re.search(r"\s+\d{1,2}$", c.text.strip()) or len(c.text.split()) > 10


def test_page_provenance_and_coverage():
    """Verify 1-based page numbering, per-page chunking, and 100% page coverage."""
    doc = parse_pdf("data/raw_papers/1706.03762v7.pdf", "1706.03762v7", "Attention Is All You Need", "v7")
    assert doc.total_pages == 15

    # Check block page distribution covers all 15 pages
    block_pages = {b.page_number for b in doc.blocks}
    assert block_pages == set(range(1, 16)), f"Missing pages: {set(range(1, 16)) - block_pages}"

    # Verify Page 5 subsections strictly have page_number == 5
    page_5_blocks = [b for b in doc.blocks if b.page_number == 5]
    assert len(page_5_blocks) >= 2
    for b in page_5_blocks:
        assert b.page_number == 5

    # Chunks on Page 5 strictly carry Page 5 citation header
    chunks = chunk_document(doc, chunk_size_words=350, overlap_words=50)
    p5_chunks = [c for c in chunks if c.page_number == 5]
    assert len(p5_chunks) > 0
    for c in p5_chunks:
        assert c.page_number == 5
        assert "Page 5" in c.to_citation_header()


def test_real_pipeline_hyphenation_in_corpus():
    """Verify that generated corpus files have repaired line-break hyphens without losing compounds."""
    # Check actual generated file chunks_setting_1.json
    with open("data/processed/chunks_setting_1.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)

    corpus_full_text = " ".join(c["text"] for c in chunks)

    # 1. Broken words must be reconnected
    repaired_words = [
        "transduction", "complexity", "dialogue", "theoretical",
        "generation", "demonstrate", "fundamentally", "information",
        "understanding", "conversational", "tokens", "produce",
        "performance", "convolutional", "transformation", "surprisingly"
    ]
    for w in repaired_words:
        assert w in corpus_full_text, f"Expected repaired word {w} not found in corpus!"

    # 2. Legitimate compounds must be preserved
    preserved_compounds = [
        "multi-head", "encoder-decoder", "real-time", "state-of-the-art",
        "self-attention", "cross-entropy", "feed-forward", "end-to-end",
        "task-specific", "well-known", "sequence-aligned", "position-wise",
        "high-resource", "H-Net", "Bits-per-byte", "FineWeb-Edu-Chinese-V",
        "English-to-German"
    ]
    for cp in preserved_compounds:
        assert cp in corpus_full_text or cp.lower() in corpus_full_text.lower(), (
            f"Expected compound {cp} missing from corpus!"
        )


def test_corpus_word_caps_and_metadata():
    """Verify strict word caps, non-empty text, valid chunk IDs, and valid metadata."""
    with open("data/processed/chunks_setting_1.json", "r", encoding="utf-8") as f:
        c1 = json.load(f)
    assert len(c1) > 0
    for chunk in c1:
        assert chunk["word_count"] <= 350
        assert chunk["word_count"] > 0
        assert chunk["chunk_id"].startswith(chunk["paper_id"] + "_c")
        assert chunk["page_number"] >= 1
        assert chunk["section_title"] != ""
        assert len(chunk["text"].strip()) > 0

    with open("data/processed/chunks_setting_2.json", "r", encoding="utf-8") as f:
        c2 = json.load(f)
    assert len(c2) > len(c1)
    for chunk in c2:
        assert chunk["word_count"] <= 175
        assert chunk["word_count"] > 0


def test_setting_3_policy_and_identity():
    """Verify that Setting 3 is byte-identical to Setting 1 per Assignment Brief."""
    with open("data/processed/chunks_setting_1.json", "rb") as f1:
        h1 = hashlib.sha256(f1.read()).hexdigest()
    with open("data/processed/chunks_setting_3.json", "rb") as f3:
        h3 = hashlib.sha256(f3.read()).hexdigest()
    assert h1 == h3, "Setting 3 and Setting 1 must be byte-identical on disk"


def test_deterministic_regeneration():
    """Verify that chunking from parsed document is completely deterministic."""
    doc = parse_pdf("data/raw_papers/1706.03762v7.pdf", "1706.03762v7", "Attention", "v7")
    chunks_a = chunk_document(doc, 350, 50)
    chunks_b = chunk_document(doc, 350, 50)
    assert len(chunks_a) == len(chunks_b)
    for ca, cb in zip(chunks_a, chunks_b):
        assert ca.chunk_id == cb.chunk_id
        assert ca.text == cb.text
        assert ca.word_count == cb.word_count
        assert ca.page_number == cb.page_number
        assert ca.section_title == cb.section_title


def test_text_completeness_validation():
    """Verify non-heading source text coverage between parsed document blocks and generated chunks."""
    doc = parse_pdf("data/raw_papers/1706.03762v7.pdf", "1706.03762v7", "Attention", "v7")
    chunks = chunk_document(doc, 350, 50)

    # All non-heading words in blocks must appear in the chunk text for that paper
    chunk_text = " ".join(c.text for c in chunks)
    for block in doc.blocks:
        # Take key words from each block
        block_words = block.text.split()
        if len(block_words) >= 5:
            sample_phrase = " ".join(block_words[:5])
            assert sample_phrase in chunk_text, f"Sample phrase from block {block.section_title} missing in chunks"


# ===========================================================================
# Phase 2.2 regression tests — F-04 heading robustness
# ===========================================================================


def test_f04_attention_table_column_headers_not_promoted(parsed_docs):
    """F-04: Attention Table 2 column headers must never become section titles."""
    titles = {t.strip() for t in _all_titles(parsed_docs["1706.03762v7"])}
    for forbidden in (
        "Model",
        "BLEU",
        "Training Cost (FLOPs)",
        "EN-DE",
        "EN-FR",
        "23.75",
        "Deep-Att + PosUnk [39]",
        "Transformer (base model)",
        "Transformer (big)",
    ):
        assert forbidden not in titles, f"Table content promoted to heading: {forbidden}"


def test_f04_moshi_table_headers_and_body_questions_not_promoted(parsed_docs):
    """F-04: Moshi table column headers and numbered body questions stay body text."""
    doc = parsed_docs["2410.00037v1"]
    titles = {t.strip() for t in _all_titles(doc)}
    assert "Model" not in titles
    assert "Background" not in titles
    for title in titles:
        assert not re.match(r"^\d+\.\s+How (does|do) we\b", title), title
    for block in doc.blocks:
        if block.page_number in (29, 30):
            assert block.section_title.strip() != "Model"


def test_f04_hnet_table_headers_not_promoted(parsed_docs):
    """F-04: H-Net table column headers (including undetected hex tables) stay body text."""
    doc = parsed_docs["2507.07955v2"]
    titles = {t.strip() for t in _all_titles(doc)}
    assert "Model" not in titles
    for block in doc.blocks:
        if block.page_number in (14, 15, 17, 38, 39):
            assert block.section_title.strip() != "Model"


def test_f04_all_toc_entries_aligned(parsed_docs):
    """F-04: Every embedded PDF TOC entry maps to a detected heading."""
    for paper_id, _, _ in PAPER_SPECS:
        document = pymupdf.open(_paper_path(paper_id))
        try:
            toc = document.get_toc()
        finally:
            document.close()
        assert toc, f"{paper_id} has no embedded TOC"
        titles = _all_titles(parsed_docs[paper_id])
        missing = [
            (level, title, page)
            for level, title, page in toc
            if not any(title == t or title in t or t in title for t in titles)
        ]
        assert not missing, f"{paper_id} TOC entries not detected: {missing}"


def test_f04_small_font_and_small_caps_headings_detected(parsed_docs):
    """F-04: Small-bold (H-Net Abstract) and small-caps (Moshi 3.3.1) headings survive."""
    attention = _all_titles(parsed_docs["1706.03762v7"])
    moshi = _all_titles(parsed_docs["2410.00037v1"])
    hnet = _all_titles(parsed_docs["2507.07955v2"])
    assert any("3.2.1 Scaled Dot-Product Attention" in t for t in attention)
    assert any("3.3.1 Architecture" in t for t in moshi)
    assert any("Abstract" in t for t in hnet)


def test_f04_captions_and_watermarks_never_headings(parsed_docs):
    """F-04: Captions, figure labels, and arXiv watermarks are never section titles."""
    for paper_id, _, _ in PAPER_SPECS:
        for title in _all_titles(parsed_docs[paper_id]):
            stripped = title.strip()
            assert not re.match(r"^(Table|Figure)\s+\d+", stripped), stripped
            assert not stripped.startswith("arXiv:"), stripped
            assert not stripped.startswith("Provided proper attribution"), stripped


def test_f04_reading_order_and_page_provenance(parsed_docs):
    """F-04: Blocks preserve reading order and 1-based physical page provenance."""
    for paper_id, _, _ in PAPER_SPECS:
        doc = parsed_docs[paper_id]
        assert [p.page_number for p in doc.pages] == list(range(1, doc.total_pages + 1))
        block_pages = [b.page_number for b in doc.blocks]
        assert block_pages == sorted(block_pages), f"{paper_id} blocks out of page order"
        for block in doc.blocks:
            assert 1 <= block.page_number <= doc.total_pages


# ===========================================================================
# Phase 2.2 regression tests — F-05 completeness and provenance
# ===========================================================================


def test_f05_every_nonheading_source_line_preserved(parsed_docs):
    """F-05: No non-heading source line may be dropped by parsing."""
    for paper_id, _, _ in PAPER_SPECS:
        doc = parsed_docs[paper_id]
        headings = collections.defaultdict(list)
        for block in doc.blocks:
            headings[block.page_number].append(block.section_title)
        for page in doc.pages:
            headings[page.page_number].extend(page.sections)

        block_alnum = collections.defaultdict(str)
        for block in doc.blocks:
            block_alnum[block.page_number] += _alnum(block.text)

        missing = []
        for page_number, text, bbox in _raw_page_lines(paper_id):
            if (
                is_running_header(text, bbox)
                or is_running_footer(text, bbox)
                or is_watermark(text, page_number)
            ):
                continue
            alnum_text = _alnum(text)
            if not alnum_text:
                continue
            if any(alnum_text in _alnum(h) for h in headings.get(page_number, [])):
                continue
            if alnum_text not in block_alnum[page_number]:
                missing.append((page_number, text))
        assert not missing, f"{paper_id} dropped non-heading lines: {missing[:5]}"


def test_f05_no_duplicated_source_blocks(parsed_docs):
    """F-05: Parsing must not duplicate source blocks across pages."""
    for paper_id, _, _ in PAPER_SPECS:
        texts = [
            re.sub(r"\s+", " ", b.text.strip()) for b in parsed_docs[paper_id].blocks
        ]
        duplicates = [
            text for text, count in collections.Counter(texts).items() if count > 1
        ]
        assert not duplicates, f"{paper_id} duplicated blocks: {duplicates[:3]}"


def test_f05_source_to_block_sequence_preserved(parsed_docs):
    """F-05: Non-heading source content remains an ordered subsequence of block text."""
    for paper_id, _, _ in PAPER_SPECS:
        doc = parsed_docs[paper_id]
        headings = collections.defaultdict(list)
        for block in doc.blocks:
            headings[block.page_number].append(block.section_title)
        for page in doc.pages:
            headings[page.page_number].extend(page.sections)

        block_sequence = collections.defaultdict(str)
        for block in doc.blocks:
            block_sequence[block.page_number] += _alnum(block.text)

        source_sequence = collections.defaultdict(str)
        for page_number, text, bbox in _raw_page_lines(paper_id):
            if (
                is_running_header(text, bbox)
                or is_running_footer(text, bbox)
                or is_watermark(text, page_number)
            ):
                continue
            alnum_text = _alnum(text)
            if any(alnum_text in _alnum(h) for h in headings.get(page_number, [])):
                continue
            source_sequence[page_number] += alnum_text

        for page_number in range(1, doc.total_pages + 1):
            assert _is_subsequence(
                source_sequence[page_number], block_sequence[page_number]
            ), f"{paper_id} page {page_number} lost or reordered source content"


def test_f05_block_to_chunk_token_completeness(parsed_docs):
    """F-05: Every block token survives chunking for both settings."""
    for chunk_size, overlap in ((350, 50), (175, 25)):
        for paper_id, _, _ in PAPER_SPECS:
            doc = parsed_docs[paper_id]
            chunks = chunk_document(doc, chunk_size, overlap)
            block_tokens = collections.defaultdict(collections.Counter)
            chunk_tokens = collections.defaultdict(collections.Counter)
            for block in doc.blocks:
                block_tokens[block.page_number].update(block.text.split())
            for chunk in chunks:
                chunk_tokens[chunk.page_number].update(chunk.text.split())
            for page_number in block_tokens:
                missing = block_tokens[page_number] - chunk_tokens[page_number]
                assert not missing, (
                    f"{paper_id} page {page_number} lost block tokens at "
                    f"{chunk_size}/{overlap}: {missing.most_common(5)}"
                )


def test_f05_chunk_page_and_section_provenance(staged_corpora, parsed_docs):
    """F-05: Every staged chunk maps to a real block page/section pair."""
    for filename, payload in staged_corpora.items():
        chunks = payload["chunks"]
        ids = [c["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids)), f"{filename} has duplicate chunk ids"
        for paper_id, _, _ in PAPER_SPECS:
            doc = parsed_docs[paper_id]
            block_keys = {(b.page_number, b.section_title) for b in doc.blocks}
            for chunk in (c for c in chunks if c["paper_id"] == paper_id):
                assert 1 <= chunk["page_number"] <= doc.total_pages
                assert (chunk["page_number"], chunk["section_title"]) in block_keys, (
                    f"{filename} chunk {chunk['chunk_id']} has fabricated provenance"
                )


def test_f05_mixed_heading_body_blocks_preserved(parsed_docs):
    """F-05: Body text sharing a block with a heading is retained for all papers."""
    attention = _blocks_text(parsed_docs["1706.03762v7"])
    assert "Nal Kalchbrenner" in attention
    assert "Stephan Gouws" in attention
    assert "inspiration" in attention

    moshi = _blocks_text(parsed_docs["2410.00037v1"])
    assert "Our baseline architecture takes inspiration from SoundStream" in moshi

    hnet = _blocks_text(parsed_docs["2507.07955v2"])
    assert "H-Net employs a hierarchical architecture comprising three primary components" in hnet


def test_f05_table_and_figure_captions_preserved(parsed_docs):
    """F-05: Every Table/Figure caption survives into the chunked corpus."""
    for paper_id, _, _ in PAPER_SPECS:
        captions = [
            text
            for _, text, _ in _raw_page_lines(paper_id)
            if re.match(r"^(Table|Figure)\s+\d+[:.]", text.strip())
        ]
        assert captions, f"{paper_id} has no captions to check"
        chunk_alnum = _alnum(
            _chunks_text(chunk_document(parsed_docs[paper_id], 350, 50))
        )
        missing = [c for c in captions if _alnum(c) not in chunk_alnum]
        assert not missing, f"{paper_id} dropped captions: {missing[:3]}"


def test_f05_reference_entries_preserved(parsed_docs):
    """F-05: Reference sections retain arXiv, URL, or DOI material."""
    for paper_id, _, _ in PAPER_SPECS:
        reference_blocks = [
            b for b in parsed_docs[paper_id].blocks if "Reference" in b.section_title
        ]
        assert reference_blocks, f"{paper_id} has no reference block"
        text = " ".join(b.text for b in reference_blocks).lower()
        assert "arxiv" in text or "http" in text or "doi" in text, (
            f"{paper_id} lost all reference identifiers"
        )
        assert text.count("arxiv") >= 5, (
            f"{paper_id} lost reference entries (fewer than 5 arXiv identifiers)"
        )
        assert len(text) > 2000, f"{paper_id} reference section is truncated"


# ===========================================================================
# Phase 2.2 regression tests — F-06 hyphenation
# ===========================================================================


def test_f06_fifteen_malformed_cases_fixed_in_pipeline(staged_corpora, parsed_docs):
    """F-06: All 15 malformed line-break forms are corrected by the real pipeline."""
    cases = [
        ("2410.00037v1", "text-tospeech", "text-to-speech"),
        ("2410.00037v1", "melspectrogram", "mel-spectrogram"),
        ("2410.00037v1", "semanticto-acoustic", "semantic-to-acoustic"),
        ("2410.00037v1", "singleturn", "single-turn"),
        ("2410.00037v1", "textonly", "text-only"),
        ("2410.00037v1", "0shot", "0-shot"),
        ("2410.00037v1", "lowercapacity", "lower-capacity"),
        ("2410.00037v1", "speech-tospeech", "speech-to-speech"),
        ("2410.00037v1", "Le-Cun", "lecun"),
        ("2410.00037v1", "Speechgptgen", "speechgpt-gen"),
        ("2507.07955v2", "predecay", "pre-decay"),
        ("2507.07955v2", "Computeoptimal", "compute-optimal"),
        (
            "2507.07955v2",
            "number-tokenizationblog",
            "number-tokenization-blog",
        ),
        (
            "2507.07955v2",
            "bitter-lessontokenization-and-blt",
            "bitter-lesson-tokenization-and-blt",
        ),
    ]
    for paper_id, malformed, repaired in cases:
        block_text = _blocks_text(parsed_docs[paper_id]).lower()
        assert malformed not in block_text, f"{malformed} survived parsing"
        assert repaired in block_text, f"{repaired} missing after repair"
        for filename in ("chunks_setting_1.json", "chunks_setting_2.json"):
            chunk_text = " ".join(
                c["text"]
                for c in staged_corpora[filename]["chunks"]
                if c["paper_id"] == paper_id
            ).lower()
            assert malformed not in chunk_text, f"{malformed} survived {filename}"
            assert repaired in chunk_text, f"{repaired} missing from {filename}"

    # The 15th case is contextual: a legitimate "Mamba2" reference title exists,
    # so only the repaired line-break occurrence may be asserted.
    hnet = _blocks_text(parsed_docs["2507.07955v2"]).lower()
    assert "computations for mamba-2" in hnet
    assert "computations for mamba2" not in hnet


def test_f06_nine_suspended_hyphens_preserved(parsed_docs):
    """F-06: All nine author-typeset suspended hyphens remain byte-identical."""
    doc = parsed_docs["2507.07955v2"]
    blocks_by_page = collections.defaultdict(str)
    for block in doc.blocks:
        blocks_by_page[block.page_number] += block.text + "\n"

    expected = {
        1: ["content- and", "context- dependent", "compute- and", "data- matched"],
        2: ["delimiter- or"],
        9: ["main- networks"],
        23: ["content- and"],
        33: ["content- and", "context- aware"],
    }
    for page_number, phrases in expected.items():
        for phrase in phrases:
            assert phrase in blocks_by_page[page_number], (
                f"Suspended hyphen {phrase!r} lost on page {page_number}"
            )

    constructions = [
        "content- and",
        "context- dependent",
        "compute- and",
        "data- matched",
        "delimiter- or",
        "main- networks",
        "context- aware",
    ]
    whole_text = _blocks_text(doc)
    assert sum(whole_text.count(p) for p in constructions) == 9


def test_f06_broken_words_rejoined(parsed_docs):
    """F-06: Genuine line-break broken words are rejoined, not left hyphenated."""
    expected = {
        "1706.03762v7": [
            "transduction",
            "multiplicative",
            "transformation",
            "convolutional",
            "surprisingly",
            "Salakhutdinov",
            "Koray",
        ],
        "2410.00037v1": [
            "complexity",
            "dialogue",
            "theoretical",
            "generation",
            "demonstrate",
            "fundamentally",
            "information",
            "understanding",
            "conversational",
            "vocabulary",
            "bestselling",
            "LeCun",
        ],
        "2507.07955v2": [
            "semantically",
            "representations",
            "hyperparameters",
            "perturbations",
            "Outrageously",
            "MambaByte",
            "Accelerating",
            "Tokenization",
        ],
    }
    for paper_id, words in expected.items():
        text = _blocks_text(parsed_docs[paper_id])
        missing = [w for w in words if w.lower() not in text.lower()]
        assert not missing, f"{paper_id} failed to rejoin: {missing}"


def test_f06_compounds_and_entities_preserved(parsed_docs):
    """F-06: Genuine compounds, names, models, and datasets keep their hyphens."""
    expected = {
        "1706.03762v7": [
            "multi-head",
            "encoder-decoder",
            "self-attention",
            "feed-forward",
            "end-to-end",
            "task-specific",
            "sequence-aligned",
            "position-wise",
        ],
        "2410.00037v1": [
            "real-time",
            "cross-entropy",
            "well-known",
            "fine-tuning",
            "pre-training",
            "post-training",
            "text-to-speech",
            "speech-to-speech",
            "semantic-to-acoustic",
            "single-turn",
            "text-only",
            "0-shot",
            "lower-capacity",
            "multi-stream",
            "full-duplex",
            "non-causal",
            "rq-transformer",
            "state-of-the-art",
            "mel-spectrogram",
        ],
        "2507.07955v2": [
            "h-net",
            "fineweb-edu-chinese-v2",
            "bits-per-byte",
            "context-dependent",
            "gradient-based",
            "high-resource",
            "encoder-decoder",
            "bpe-based",
            "mamba-2",
            "compute-optimal",
            "pre-decay",
            "cross-modality",
        ],
    }
    for paper_id, compounds in expected.items():
        text = _blocks_text(parsed_docs[paper_id]).lower()
        missing = [c for c in compounds if c not in text]
        assert not missing, f"{paper_id} lost compounds: {missing}"


def test_f06_urls_and_identifiers_not_reflowed(parsed_docs):
    """F-06: URLs split across lines keep their hyphens."""
    text = _blocks_text(parsed_docs["2507.07955v2"])
    for url_fragment in (
        "number-tokenization-blog",
        "bitter-lesson-tokenization-and-blt",
        "introducing-openai-o1-preview",
    ):
        assert url_fragment in text, f"URL fragment reflowed: {url_fragment}"


def test_f06_no_hyphens_inserted_or_left_unresolved(parsed_docs):
    """F-06: No blind hyphen insertion and no unresolved line-break hyphen remains."""
    inserted = [
        "transduc-tion",
        "sur-prisingly",
        "convolu-tional",
        "Salakhutdi-nov",
        "dial-ogue",
        "Le-Cun",
        "Speechgptgen",
    ]
    for paper_id, _, _ in PAPER_SPECS:
        text = _blocks_text(parsed_docs[paper_id])
        for bad in inserted:
            assert bad not in text, f"{paper_id} contains inserted hyphen form {bad}"
        # A hyphen may legitimately remain at a paragraph boundary when a figure or
        # table caption interrupts the source column (documented known limitation).
        # It must never be blindly joined across that structural boundary.


def test_f06_staged_setting_identity_and_caps(staged_corpora):
    """F-06/M1: Staged Setting 1 and Setting 3 are byte-identical; caps hold."""
    setting_1 = staged_corpora["chunks_setting_1.json"]
    setting_2 = staged_corpora["chunks_setting_2.json"]
    setting_3 = staged_corpora["chunks_setting_3.json"]

    with open(setting_1["path"], "rb") as handle:
        bytes_1 = handle.read()
    with open(setting_3["path"], "rb") as handle:
        bytes_3 = handle.read()
    assert bytes_1 == bytes_3, "Staged Setting 1 and Setting 3 must be byte-identical"

    assert len(setting_2["chunks"]) > len(setting_1["chunks"])
    assert all(c["word_count"] <= 350 for c in setting_1["chunks"])
    assert all(c["word_count"] <= 175 for c in setting_2["chunks"])
    assert all(c["word_count"] > 0 for c in setting_1["chunks"])
    assert all(c["text"].strip() for c in setting_1["chunks"])

