"""Unit and provenance tests for the evaluation dataset (Milestone M2)."""

import json
import os
import re
import pytest


def test_eval_questions_file_exists():
    path = "data/eval_questions.json"
    assert os.path.exists(path), f"File {path} must exist"


def test_eval_questions_count_and_schema():
    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        questions = json.load(f)

    assert len(questions) >= 15, "Evaluation set must contain at least 15 questions"

    valid_types = {
        "specific_fact", "formula_values", "table_numeric",
        "timing_mechanism", "architecture", "numeric_claim",
        "benchmark_claim", "mechanism", "modalities",
        "false_friend", "unanswerable"
    }

    for q in questions:
        assert "id" in q and q["id"].startswith("Q")
        assert "paper" in q
        assert "type" in q and q["type"] in valid_types
        assert "question" in q and len(q["question"]) > 10
        assert "ground_truth" in q and len(q["ground_truth"]) > 5
        assert "target_citation" in q
        assert "verified_quote" in q
        assert q["status"] == "VERIFIED_AGAINST_PDF"
        assert "expected_facts" in q

        if q["type"] in ("false_friend", "unanswerable"):
            assert q["expected_facts"] == [], f"{q['id']} trap must have no factual grading criteria"
        else:
            assert isinstance(q["expected_facts"], list) and q["expected_facts"], (
                f"{q['id']} answerable question must declare at least one mandatory expected fact"
            )
            assert all(isinstance(f, str) and f.strip() for f in q["expected_facts"])


def test_eval_questions_categories():
    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        questions = json.load(f)

    answerable = [q for q in questions if q["type"] not in ("false_friend", "unanswerable")]
    false_friends = [q for q in questions if q["type"] == "false_friend"]
    unanswerable = [q for q in questions if q["type"] == "unanswerable"]

    assert len(answerable) == 11, "Must have 11 answerable fact questions"
    assert len(false_friends) == 2, "Must have 2 cross-corpus false-friend questions"
    assert len(unanswerable) == 2, "Must have 2 out-of-domain unanswerable questions"


def test_eval_quotes_verbatim_in_corpus_and_citation_format():
    """Verify every answerable question's target_citation & verified_quote match the real chunks."""
    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        questions = json.load(f)
    with open("data/processed/chunks_setting_1.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)

    cite_re = re.compile(r"^\[([^,]+),\s*(.+),\s*Page\s*(\d+)\]$")

    for q in questions:
        if q["type"] in ("false_friend", "unanswerable"):
            assert q["target_citation"] == "REFUSED_CORRECTLY"
            continue

        m = cite_re.match(q["target_citation"])
        assert m is not None, f"{q['id']} invalid citation format: {q['target_citation']}"
        paper_title, section_title, page_str = m.group(1).strip(), m.group(2).strip(), int(m.group(3))

        matching = [
            c for c in chunks
            if c["paper_title"] == paper_title
            and c["section_title"] == section_title
            and c["page_number"] == page_str
            and q["verified_quote"] in c["text"]
        ]
        assert len(matching) >= 1, (
            f"{q['id']}: quote {q['verified_quote']!r} not found verbatim at "
            f"[{paper_title}, {section_title}, Page {page_str}]"
        )
