"""PDF parsing module using PyMuPDF with multi-signal heading extraction and real-pipeline hyphenation repair.

F-04 safeguards:
- Heading decisions combine structural table immunity (``page.find_tables()``), font style
  flags (bold/small-caps), relative font size versus the document body size, reading position,
  and line shape. No rule relies exclusively on a hardcoded font family name.
- Table column headers are never promoted to section headings, but their text is preserved as
  ordinary source content so that F-05 completeness is not reduced.

F-06 safeguards:
- A line-break hyphen is removed only when evidence indicates a broken word. Genuine
  compounds, named entities, URLs, numeric compounds, and connector constructions keep the
  hyphen. Evidence is taken from a document-wide vocabulary built before repair and from a
  curated set of observed compounds/broken words.
- Same-line suspended hyphens (``word- word``) are never touched because the repair regex
  requires an actual line break.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pymupdf


# ---------------------------------------------------------------------------
# Hyphenation knowledge
# ---------------------------------------------------------------------------

KNOWN_COMPOUNDS: Set[str] = {
    "multi-head",
    "encoder-decoder",
    "real-time",
    "state-of-the-art",
    "self-attention",
    "cross-entropy",
    "feed-forward",
    "end-to-end",
    "task-specific",
    "well-known",
    "sequence-aligned",
    "sequence-to-sequence",
    "seq-to-seq",
    "position-wise",
    "point-wise",
    "dot-product",
    "layer-norm",
    "residual-dropout",
    "byte-pair",
    "out-of-vocabulary",
    "large-scale",
    "zero-shot",
    "few-shot",
    "one-shot",
    "fine-tuning",
    "fine-tune",
    "fine-tuned",
    "warm-up",
    "co-attention",
    "source-target",
    "attention-based",
    "context-dependent",
    "bpe-based",
    "high-resource",
    "h-net",
    "bits-per-byte",
    "fineweb-edu-chinese-v",
    "english-to-german",
    "english-to-french",
    "berkeley-parser",
    "tokenizer-free",
    "non-autoregressive",
    "auto-regressive",
    "full-duplex",
    "speech-text",
    "split-rvq",
    "pre-training",
    "post-training",
    "multi-stream",
    "cross-attention",
    "down-sampler",
    "down-sampling",
    "de-chunking",
    # Phase 2.2: line-break compounds observed in the three-paper corpus.
    "text-to-speech",
    "speech-to-speech",
    "semantic-to-acoustic",
    "single-turn",
    "text-only",
    "0-shot",
    "lower-capacity",
    "speechgpt-gen",
    "pre-decay",
    "compute-optimal",
    "mamba-2",
    "mel-spectrogram",
    "number-tokenization-blog",
    "bitter-lesson-tokenization-and-blt",
}

# Broken words that can look like compound prefixes when the document contains the
# joined form only at the line break. This is a safety net; document-frequency evidence
# is always consulted first.
KNOWN_JOINED: Set[str] = {
    "accelerating",
    "bestselling",
    "complexity",
    "conflict",
    "convolutional",
    "demonstrate",
    "dialogue",
    "fundamentally",
    "generation",
    "ginsburg",
    "goldstein",
    "highest",
    "identifier",
    "information",
    "intermediate",
    "lecun",
    "malcolm",
    "multiplicative",
    "outrageously",
    "oxidation",
    "particularly",
    "performance",
    "purposely",
    "ruxandra",
    "salakhutdinov",
    "schrittwieser",
    "segmentation",
    "surprisingly",
    "thanumalayan",
    "theoretical",
    "thornton",
    "transduction",
    "transformation",
    "understanding",
    "vocabulary",
    "washington",
}

COMPOUND_SUFFIXES: Set[str] = {
    "based",
    "aware",
    "aligned",
    "dependent",
    "guided",
    "driven",
    "specific",
    "centric",
    "oriented",
    "free",
    "like",
    "rich",
    "level",
    "scale",
    "wise",
    "bound",
    "preserving",
    "invariant",
}

# Connector particles are strong evidence that a line-end hyphen belongs to a
# multi-part compound such as ``text-to-speech`` or ``bits-per-byte``.
CONNECTORS: Set[str] = {
    "to",
    "of",
    "and",
    "or",
    "per",
    "by",
    "in",
    "on",
    "for",
    "with",
    "vs",
    "from",
    "into",
    "onto",
    "via",
    "as",
}

# Conservative productive prefixes. Prefixes such as ``re``, ``de``, ``in``, ``un``
# and ``inter`` are deliberately excluded because they frequently occur inside a
# single broken word (e.g. ``inter-mediate`` -> ``intermediate``).
COMPOUND_PREFIXES: Set[str] = {
    "anti",
    "auto",
    "bi",
    "closed",
    "co",
    "compute",
    "cross",
    "deep",
    "dual",
    "end",
    "far",
    "few",
    "front",
    "full",
    "half",
    "high",
    "higher",
    "hyper",
    "ill",
    "left",
    "long",
    "longer",
    "low",
    "lower",
    "macro",
    "many",
    "mel",
    "meta",
    "micro",
    "mid",
    "mono",
    "multi",
    "near",
    "non",
    "one",
    "open",
    "over",
    "poly",
    "post",
    "pre",
    "quad",
    "real",
    "right",
    "self",
    "semi",
    "shallow",
    "short",
    "single",
    "speechgpt",
    "sub",
    "super",
    "text",
    "top",
    "tri",
    "ultra",
    "under",
    "upper",
    "well",
    "wide",
    "wider",
    "zero",
}

URL_MARKERS: Tuple[str, ...] = (
    "http://",
    "https://",
    "www.",
    "doi.org",
    "url:",
    "arxiv.org/abs",
)

BROKEN_LINE_RE = re.compile(
    r"(\b[A-Za-z0-9][A-Za-z0-9\-_']*)-[ \t]*\n[ \t]*"
    r"([A-Za-z0-9][A-Za-z0-9\-_']*)"
    r"((?:-[A-Za-z0-9][A-Za-z0-9\-_']*)*)"
)
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")
NUMERIC_RE = re.compile(r"^[0-9]+$")

ARXIV_WATERMARK_RE = re.compile(
    r"^arXiv:\d{4}\.\d{4,5}(v\d+)?\s*\[[a-zA-Z\.\-]+\]\s*\d+\s+[A-Za-z]+\s+\d{4}$"
)

STANDARD_SECTIONS: Set[str] = {
    "abstract",
    "introduction",
    "background",
    "related work",
    "model architecture",
    "model",
    "datasets and training",
    "evaluation",
    "safety",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "acknowledgements",
    "references",
    "appendix",
}

SEC_NUM_RE = re.compile(r"^[1-9]\d*(\.[1-9]\d*)*\.?$")
LETTER_NUM_RE = re.compile(r"^[A-Z](\.[1-9]\d*)*\.?$")
COMBINED_NUM_RE = re.compile(r"^([1-9]\d*(\.[1-9]\d*)*\.?)\s+([A-Z].+)$")
COMBINED_LETTER_RE = re.compile(r"^([A-Z](\.[1-9]\d*)*\.?)\s+([A-Z].+)$")
APPENDIX_EXPLICIT_RE = re.compile(
    r"^Appendix\s+([A-Z]|\d+)(\.|\:)?(\s+.*)?$", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Document vocabulary for evidence-based hyphenation
# ---------------------------------------------------------------------------


@dataclass
class DocumentVocabulary:
    """Counts of intact hyphenated words, intact joined words, and word parts."""

    hyphenated: Counter = field(default_factory=Counter)
    joined: Counter = field(default_factory=Counter)
    words: Counter = field(default_factory=Counter)

    def hyphen_count(self, value: str) -> int:
        return self.hyphenated.get(value.lower(), 0)

    def joined_count(self, value: str) -> int:
        return self.joined.get(value.lower(), 0)

    def is_word(self, value: str) -> bool:
        return self.words.get(value.lower(), 0) > 0


def _iter_raw_line_texts(doc: "pymupdf.Document") -> Iterable[str]:
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if spans:
                    yield " ".join(s["text"].strip() for s in spans)


def build_document_vocabulary(doc: "pymupdf.Document") -> DocumentVocabulary:
    """Build a document-wide vocabulary *before* any hyphen repair.

    The final token of a line that ends in ``-`` is a fragment and is deliberately
    excluded; otherwise a broken word would vote for its own hyphenated form.
    """
    vocabulary = DocumentVocabulary()
    for text in _iter_raw_line_texts(doc):
        stripped = text.rstrip()
        line_ends_with_hyphen = stripped.endswith("-")
        tokens = TOKEN_RE.findall(stripped)
        for index, token in enumerate(tokens):
            lowered = token.lower()
            if line_ends_with_hyphen and index == len(tokens) - 1 and token.endswith("-"):
                continue
            if "-" in token:
                vocabulary.hyphenated[lowered] += 1
                for part in token.split("-"):
                    if len(part) >= 2:
                        vocabulary.words[part.lower()] += 1
            else:
                vocabulary.joined[lowered] += 1
            if len(lowered) >= 2:
                vocabulary.words[lowered] += 1
    return vocabulary


def _line_context(text: str, match: re.Match) -> str:
    """Return the source line on which the line-break hyphen begins."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    break_pos = text.find("\n", match.start())
    if break_pos == -1:
        return text[line_start:]
    return text[line_start:break_pos]


def _should_preserve_hyphen(
    w1: str,
    w2: str,
    following: str,
    context: str,
    vocabulary: Optional[DocumentVocabulary],
) -> bool:
    """Return True when the line-break hyphen is orthographically meaningful."""
    hyphenated = f"{w1}-{w2}{following}"
    joined = f"{w1}{w2}{following}"
    hkey = hyphenated.lower()
    jkey = joined.lower()

    # 1. URLs, DOIs and identifier-like strings are never reflowed.
    lowered_context = context.lower()
    if any(marker in lowered_context for marker in URL_MARKERS):
        return True

    # 2. Curated compounds observed in the target corpus (or general academic prose).
    if hkey in KNOWN_COMPOUNDS:
        return True

    # 3. Curated broken words that can masquerade as compounds.
    if jkey in KNOWN_JOINED:
        return False

    # 4. Document-frequency evidence. A form that occurs intact elsewhere wins.
    if vocabulary is not None:
        hyphen_count = vocabulary.hyphen_count(hkey)
        joined_count = vocabulary.joined_count(jkey)
        if hyphen_count > joined_count:
            return True
        if joined_count > hyphen_count:
            return False
        if vocabulary.is_word(jkey):
            return False

    # 5. Connector particles at the break (text-to-speech, bits-per-byte).
    last_part = w1.split("-")[-1].lower()
    if last_part in CONNECTORS or w2.lower() in CONNECTORS:
        return True

    # 6. Numeric head (0-shot). A numeric tail is intentionally not enough because
    #    superscript citation artifacts such as ``Normaliza-9`` must be joined.
    if NUMERIC_RE.match(w1):
        return True

    # 7. Capitalized named entities (H-Net, Berkeley-Parser, English-to-German).
    if w1[0].isupper() and w2[0].isupper():
        return True

    # 8. Productive compound suffix (attention-based, position-wise).
    if w2.lower() in COMPOUND_SUFFIXES:
        return True

    # 9. Conservative productive prefix (single-turn, lower-capacity, pre-decay).
    if last_part in COMPOUND_PREFIXES:
        return True

    # 10. Multi-part compound whose base is a known compound.
    if "-" in w1:
        base_w1 = w1.split("-")[-1].lower()
        if f"{base_w1}-{w2.lower()}" in KNOWN_COMPOUNDS:
            return True

    # Default: an ordinary broken word.
    return False


def repair_hyphenation(
    text: str, vocabulary: Optional[DocumentVocabulary] = None
) -> str:
    """Repair line-break hyphenation without blind hyphen removal or insertion.

    The decision for each ``word-\\nword`` pair is made by
    :func:`_should_preserve_hyphen`. Same-line suspended hyphens (``word- word``)
    do not match ``BROKEN_LINE_RE`` and are therefore preserved verbatim.
    """

    def repl(match: re.Match) -> str:
        w1 = match.group(1)
        w2 = match.group(2)
        following = match.group(3) or ""
        if _should_preserve_hyphen(
            w1, w2, following, _line_context(text, match), vocabulary
        ):
            return f"{w1}-{w2}{following}"
        return f"{w1}{w2}{following}"

    return BROKEN_LINE_RE.sub(repl, text)


def clean_section_text(
    text: str, vocabulary: Optional[DocumentVocabulary] = None
) -> str:
    """Clean multiline text by repairing hyphenation and collapsing soft wraps."""
    text = repair_hyphenation(text, vocabulary=vocabulary)
    lines = text.split("\n")
    cleaned_paragraphs: List[str] = []
    current_para: List[str] = []
    for line in lines:
        line_s = line.strip()
        if not line_s:
            if current_para:
                cleaned_paragraphs.append(" ".join(current_para))
                current_para = []
        else:
            current_para.append(line_s)
    if current_para:
        cleaned_paragraphs.append(" ".join(current_para))
    out = "\n\n".join(cleaned_paragraphs)
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()


def clean_text(text: str) -> str:
    """General text cleaning utility supporting single-string inputs."""
    return clean_section_text(text)


# ---------------------------------------------------------------------------
# Heading signals
# ---------------------------------------------------------------------------


def is_bold_or_styled(span: Dict) -> bool:
    """Check if a span uses bold weight, small-caps, or heading font styling.

    Font names remain one signal, but heading decisions also use the PyMuPDF style
    flags, relative font size, line shape, reading position, and structural table
    immunity. Graphic font families are excluded only as a secondary guard.
    """
    flags = span.get("flags", 0)
    font = span.get("font", "").lower()
    if any(key in font for key in ["applesd", "arial", "helvetica", "sans"]):
        return False
    is_bold = bool(flags & 16) or any(
        key in font for key in ["bold", "medi", "black", "cmbx", "heavy", "linlibertinetb"]
    )
    is_small_caps = any(key in font for key in ["csc", "smallcaps"])
    return is_bold or is_small_caps


def is_running_footer(line_text: str, bbox: Tuple[float, float, float, float]) -> bool:
    """Identify page-number footers at the bottom margin."""
    if bbox[3] >= 720 and line_text.strip().isdigit():
        return True
    return False


def is_running_header(line_text: str, bbox: Tuple[float, float, float, float]) -> bool:
    """Identify recurring top-of-page running headers and review notices."""
    if bbox[1] <= 50:
        if "Moshi: a speech-text foundation model" in line_text:
            return True
        if (
            "Preprint. Under review." in line_text
            or "Published as a conference paper" in line_text
        ):
            return True
    return False


def is_watermark(line_text: str, page_num: int) -> bool:
    """Identify marginal arXiv distribution watermarks on Page 1."""
    if page_num == 1 and ARXIV_WATERMARK_RE.match(line_text.strip()):
        return True
    return False


def bbox_area(bbox: Tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_intersection_area(
    first: Tuple[float, float, float, float],
    second: Tuple[float, float, float, float],
) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def is_inside_bbox(
    inner: Tuple[float, float, float, float],
    outer: Tuple[float, float, float, float],
    threshold: float = 0.6,
) -> bool:
    """Return True when at least ``threshold`` of ``inner`` lies inside ``outer``."""
    area = bbox_area(inner)
    if area <= 0:
        return False
    return bbox_intersection_area(inner, outer) / area >= threshold


def get_table_bboxes(page: "pymupdf.Page") -> List[Tuple[float, float, float, float]]:
    """Return structural table bounding boxes for a page, tolerating detection failure."""
    try:
        return [tuple(table.bbox) for table in page.find_tables().tables]
    except Exception:
        return []


_TABLE_BBOX_CACHE: Dict[Tuple[str, int], List[Tuple[float, float, float, float]]] = {}


def _cached_table_bboxes(
    file_path: str, page: "pymupdf.Page", page_index: int
) -> List[Tuple[float, float, float, float]]:
    key = (os.path.abspath(file_path), page_index)
    if key not in _TABLE_BBOX_CACHE:
        _TABLE_BBOX_CACHE[key] = get_table_bboxes(page)
    return _TABLE_BBOX_CACHE[key]


def get_image_bboxes(page: "pymupdf.Page") -> List[Tuple[float, float, float, float]]:
    """Return image block bounding boxes for a page."""
    bboxes: List[Tuple[float, float, float, float]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") == 1 and block.get("bbox"):
            bboxes.append(tuple(block["bbox"]))
    return bboxes


def compute_document_body_size(doc: "pymupdf.Document") -> float:
    """Estimate the dominant body font size across the document.

    Long body lines dominate the mode; table cells and figure labels are too short
    or too varied to displace it for these papers.
    """
    sizes: Counter = Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = " ".join(s["text"].strip() for s in spans)
                if len(text) < 20:
                    continue
                avg = sum(float(s.get("size", 0.0)) for s in spans) / len(spans)
                if avg >= 6.0:
                    sizes[round(avg, 1)] += 1
    if not sizes:
        return 0.0
    return float(sizes.most_common(1)[0][0])


def is_generic_bold_heading(
    text: str,
    styled: bool,
    size: float,
    body_size: float,
    in_table: bool = False,
    in_image: bool = False,
    block_start: bool = True,
    page_number: int = 2,
) -> bool:
    """Conservative fallback for unnumbered, fully styled section headings.

    The rule intentionally refuses page 1 (title/author zone), terminal sentence
    punctuation, tables, images, affiliations, URLs, and long lines. It exists so
    that papers without numbered or standard section names still expose obvious
    bold standalone headings without promoting body text.
    """
    if not styled or in_table or in_image or not block_start:
        return False
    if page_number <= 1:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return False
    words = stripped.split()
    if not (2 <= len(words) <= 9):
        return False
    if not stripped[0].isupper():
        return False
    if stripped.endswith((".", ",", ";", ":", "?", "!")):
        return False
    lowered = stripped.lower()
    if any(
        marker in lowered
        for marker in ("http", "www.", "university", "department", "institute", "laboratory", "@")
    ):
        return False
    if any(ch.isdigit() for ch in stripped):
        return False
    if body_size <= 0:
        return False
    if not (body_size * 0.85 <= size <= body_size * 1.5):
        return False
    return True


@dataclass
class _LineInfo:
    text: str
    styled: bool
    size: float
    bbox: Tuple[float, float, float, float]
    in_table: bool
    in_image: bool
    block_start: bool


def _detect_heading(
    info: _LineInfo,
    next_info: Optional[_LineInfo],
    body_size: float,
    in_appendix_zone: bool,
    page_number: int,
) -> Optional[Tuple[str, int]]:
    """Return ``(heading_text, lines_consumed)`` or None for a body line."""
    text = info.text
    stripped = text.strip()
    styled = info.styled
    size = info.size
    is_large = body_size > 0 and size >= body_size * 1.08
    heading_style = styled or is_large

    if info.in_table or info.in_image:
        return None

    # Never treat captions, citations, URLs, or undersized fragments as headings.
    if stripped.startswith(("Table ", "Table.", "Figure ", "Figure.")):
        return None
    if stripped.startswith("[") or "http" in stripped or stripped.startswith("URL "):
        return None
    if body_size > 0 and size < body_size * 0.8:
        return None

    first_num: Optional[int] = None
    match_num = re.match(r"^(\d+)", stripped)
    if match_num:
        first_num = int(match_num.group(1))

    # 1. Explicit appendix heading.
    if APPENDIX_EXPLICIT_RE.match(stripped):
        return stripped, 1

    # 2. Combined numbered heading (e.g. "3.2.1 Scaled Dot-Product Attention").
    #    Styling or a clear size jump is mandatory: regular body-size numbered list
    #    items such as "1. How does our model behave ...?" must remain body text.
    if (
        COMBINED_NUM_RE.match(stripped)
        and len(stripped) < 80
        and first_num is not None
        and first_num <= 20
        and heading_style
        and not stripped.endswith("?")
    ):
        return stripped, 1

    # 3. Combined lettered/appendix heading.
    if (
        COMBINED_LETTER_RE.match(stripped)
        and len(stripped) < 80
        and stripped[0] in "ABCDEFGH"
        and heading_style
    ):
        return (
            f"Appendix {stripped}" if not stripped.startswith("Appendix") else stripped,
            1,
        )

    # 4. Two-line numbered heading (number on one line, title on the next).
    if (
        SEC_NUM_RE.match(stripped)
        and first_num is not None
        and first_num <= 20
        and next_info is not None
        and not next_info.in_table
        and not next_info.in_image
    ):
        next_text = next_info.text.strip()
        next_large = body_size > 0 and next_info.size >= body_size * 1.08
        if (
            (styled or next_info.styled or is_large or next_large)
            and next_text
            and next_text[0].isupper()
            and len(next_text) < 80
            and not next_text.startswith(("Table", "Figure", "["))
        ):
            return f"{stripped.rstrip('.')} {next_text}", 2

    # 5. Two-line lettered/appendix heading.
    if (
        LETTER_NUM_RE.match(stripped)
        and stripped[0] in "ABCDEFGH"
        and next_info is not None
        and not next_info.in_table
        and not next_info.in_image
    ):
        next_text = next_info.text.strip()
        next_large = body_size > 0 and next_info.size >= body_size * 1.08
        if (
            (styled or next_info.styled or is_large or next_large or in_appendix_zone)
            and next_text
            and next_text[0].isupper()
            and len(next_text) < 80
            and not next_text.startswith(("Table", "Figure", "["))
        ):
            prefix = "Appendix " if (in_appendix_zone or len(stripped) <= 2) else ""
            return f"{prefix}{stripped.rstrip('.')} {next_text}", 2

    # 6. Standard academic section name with heading styling or a size jump.
    normalized = stripped.lower().rstrip(":")
    if normalized in STANDARD_SECTIONS and heading_style:
        return stripped.rstrip(":"), 1

    # 7. Conservative generic unnumbered styled heading.
    if is_generic_bold_heading(
        stripped,
        styled,
        size,
        body_size,
        in_table=info.in_table,
        in_image=info.in_image,
        block_start=info.block_start,
        page_number=page_number,
    ):
        return stripped, 1

    return None


# ---------------------------------------------------------------------------
# Parsed document model
# ---------------------------------------------------------------------------


@dataclass
class SectionBlock:
    section_title: str
    page_number: int
    text: str


@dataclass
class PageContent:
    page_number: int
    text: str
    sections: List[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    paper_id: str
    title: str
    version: str
    total_pages: int
    pages: List[PageContent] = field(default_factory=list)
    blocks: List[SectionBlock] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_pdf(file_path: str, paper_id: str, title: str, version: str) -> ParsedDocument:
    """Parse a research paper PDF with multi-signal heading detection and full text completeness.

    Handles:
    - Mixed heading and body text blocks (never discards following body lines).
    - Multi-level numeric, lettered, and explicit appendix headings.
    - Non-bold small-caps (Moshi 10.91pt) and small bold headings (H-Net Abstract 8.97pt).
    - Structural table immunity: table column headers are preserved as body text but are
      never promoted to section titles.
    - Running header, running footer, and margin watermark removal without touching
      reference entries.
    - Evidence-based line-break hyphenation repair preserving compounds, entities, URLs,
      numeric compounds, and same-line suspended hyphens on the real pipeline.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    doc = pymupdf.open(file_path)
    vocabulary = build_document_vocabulary(doc)
    body_size = compute_document_body_size(doc)

    parsed = ParsedDocument(
        paper_id=paper_id,
        title=title,
        version=version,
        total_pages=len(doc),
    )

    current_section = "Abstract"
    in_appendix_zone = False

    for page_idx in range(len(doc)):
        page_num = page_idx + 1
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]
        table_bboxes = _cached_table_bboxes(file_path, page, page_idx)
        image_bboxes = get_image_bboxes(page)

        detected_sections_on_page: List[str] = []
        current_page_section_lines: List[str] = []

        def flush_page_section(sec_title: str) -> None:
            if current_page_section_lines:
                raw_block_text = "\n".join(current_page_section_lines)
                cleaned = clean_section_text(raw_block_text, vocabulary=vocabulary)
                if cleaned:
                    parsed.blocks.append(
                        SectionBlock(
                            section_title=sec_title,
                            page_number=page_num,
                            text=cleaned,
                        )
                    )
                current_page_section_lines.clear()

        for block in blocks:
            if block.get("type") != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue

            extracted_lines: List[_LineInfo] = []
            for line in lines:
                spans = [
                    s for s in line.get("spans", []) if s.get("text", "").strip()
                ]
                if not spans:
                    continue
                line_text = " ".join(s["text"].strip() for s in spans)
                bbox = line.get("bbox", (0, 0, 0, 0))

                # Filter headers, footers, and watermarks. All other lines, including
                # table cells and figure labels, are preserved as source content.
                if is_running_footer(line_text, bbox):
                    continue
                if is_running_header(line_text, bbox):
                    continue
                if is_watermark(line_text, page_num):
                    continue

                styled_spans = sum(1 for s in spans if is_bold_or_styled(s))
                styled = styled_spans / len(spans) >= 0.7
                avg_sz = sum(float(s.get("size", 0.0)) for s in spans) / len(spans)
                in_table = any(
                    is_inside_bbox(bbox, table_bbox) for table_bbox in table_bboxes
                )
                in_image = any(
                    is_inside_bbox(bbox, image_bbox) for image_bbox in image_bboxes
                )
                extracted_lines.append(
                    _LineInfo(
                        text=line_text,
                        styled=styled,
                        size=avg_sz,
                        bbox=bbox,
                        in_table=in_table,
                        in_image=in_image,
                        block_start=(len(extracted_lines) == 0),
                    )
                )

            if not extracted_lines:
                continue

            line_idx = 0
            while line_idx < len(extracted_lines):
                info = extracted_lines[line_idx]
                next_info = (
                    extracted_lines[line_idx + 1]
                    if line_idx + 1 < len(extracted_lines)
                    else None
                )
                detected = _detect_heading(
                    info,
                    next_info,
                    body_size,
                    in_appendix_zone,
                    page_num,
                )

                if detected:
                    heading_text, lines_consumed = detected
                    flush_page_section(current_section)
                    current_section = heading_text
                    detected_sections_on_page.append(current_section)
                    normalized = heading_text.lower().rstrip(":")
                    if normalized == "references":
                        in_appendix_zone = True
                    if in_appendix_zone and normalized == "related work":
                        current_section = "Appendix Related Work"
                        detected_sections_on_page[-1] = current_section
                    line_idx += lines_consumed
                else:
                    current_page_section_lines.append(info.text)
                    line_idx += 1

        flush_page_section(current_section)

        parsed.pages.append(
            PageContent(
                page_number=page_num,
                text=clean_section_text(page.get_text("text"), vocabulary=vocabulary),
                sections=detected_sections_on_page,
            )
        )

    return parsed
