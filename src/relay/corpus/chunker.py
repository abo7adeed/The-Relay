"""Chunking module implementing configurable word-level sliding window with page provenance."""

import json
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from relay.corpus.parser import ParsedDocument


@dataclass
class Chunk:
    chunk_id: str
    paper_id: str
    paper_title: str
    version: str
    section_title: str
    page_number: int
    chunk_index: int
    text: str
    word_count: int

    def to_citation_header(self) -> str:
        return f"[{self.paper_title}, {self.section_title}, Page {self.page_number}]"


def chunk_document(
    doc: ParsedDocument,
    chunk_size_words: int = 350,
    overlap_words: int = 50,
) -> List[Chunk]:
    """Slice parsed document into word-level chunks with strict physical page provenance.

    Args:
        doc: The parsed document structure with per-page section blocks.
        chunk_size_words: Maximum words per chunk.
        overlap_words: Sliding window overlap in words.

    Returns:
        List of structured Chunk instances.
    """
    if overlap_words >= chunk_size_words:
        raise ValueError("overlap_words must be strictly smaller than chunk_size_words")

    chunks: List[Chunk] = []
    chunk_idx = 0
    step = chunk_size_words - overlap_words

    for block in doc.blocks:
        words = block.text.split()
        if not words:
            continue

        if len(words) <= chunk_size_words:
            # Block fits entirely within chunk budget
            chunk_id = f"{doc.paper_id}_c{chunk_idx:04d}"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                paper_id=doc.paper_id,
                paper_title=doc.title,
                version=doc.version,
                section_title=block.section_title,
                page_number=block.page_number,
                chunk_index=chunk_idx,
                text=" ".join(words),
                word_count=len(words),
            ))
            chunk_idx += 1
        else:
            # Sliding window over block within page boundary
            for start in range(0, len(words), step):
                chunk_words = words[start:start + chunk_size_words]
                if not chunk_words:
                    break
                chunk_id = f"{doc.paper_id}_c{chunk_idx:04d}"
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    paper_id=doc.paper_id,
                    paper_title=doc.title,
                    version=doc.version,
                    section_title=block.section_title,
                    page_number=block.page_number,
                    chunk_index=chunk_idx,
                    text=" ".join(chunk_words),
                    word_count=len(chunk_words),
                ))
                chunk_idx += 1
                if start + chunk_size_words >= len(words):
                    break

    return chunks


def build_all_processed_chunks(
    parsed_docs: List[ParsedDocument],
    output_dir: str = "data/processed",
) -> Dict[str, str]:
    """Build and save chunks for Setting 1 (350w/50o), Setting 2 (175w/25o), Setting 3 (350w/50o).

    Returns:
        Dict mapping setting_name to saved file path.
    """
    os.makedirs(output_dir, exist_ok=True)
    configs = {
        "chunks_setting_1.json": (350, 50),
        "chunks_setting_2.json": (175, 25),
        "chunks_setting_3.json": (350, 50),
    }

    result_paths = {}
    for filename, (size, overlap) in configs.items():
        all_chunks: List[Dict[str, Any]] = []
        for doc in parsed_docs:
            doc_chunks = chunk_document(doc, chunk_size_words=size, overlap_words=overlap)
            all_chunks.extend(asdict(c) for c in doc_chunks)

        out_path = os.path.join(output_dir, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(all_chunks, f, indent=2, ensure_ascii=False)

        result_paths[filename] = out_path

    return result_paths
