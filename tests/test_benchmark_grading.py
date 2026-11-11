"""Focused regression tests for the Revision 4 grading defect (P0)."""

import json
import pytest

from relay.evaluation.grading import (
    CITATION_CORRECT,
    CITATION_NOT_APPLICABLE,
    CITATION_WRONG,
    grade_citation,
    grade_eval_answer,
)


def _question(**overrides):
    base = {
        "id": "T01",
        "type": "specific_fact",
        "expected_facts": ["1.1", "12.5"],
        "target_citation": "[Moshi, 3.3.1 Architecture, Page 11]",
    }
    base.update(overrides)
    return base


def test_all_expected_facts_present_is_correct():
    grade = grade_eval_answer(
        _question(),
        "Mimi operates at 12.5 Hz and a bitrate of 1.1 kbps.",
        "verified_grounded",
    )
    assert grade == "correct"


def test_only_one_of_multiple_expected_facts_is_wrong():
    grade = grade_eval_answer(
        _question(),
        "Mimi operates at a bitrate of 1.1 kbps.",
        "verified_grounded",
    )
    assert grade == "wrong"


def test_no_expected_facts_present_is_wrong():
    grade = grade_eval_answer(
        _question(),
        "Mimi is a neural audio codec.",
        "verified_grounded",
    )
    assert grade == "wrong"


@pytest.mark.parametrize("facts", [[], None])
def test_empty_or_missing_expected_fact_configuration_fails_loudly(facts):
    question = _question()
    if facts is None:
        question.pop("expected_facts")
    else:
        question["expected_facts"] = facts
    with pytest.raises(ValueError, match="expected_facts"):
        grade_eval_answer(question, "1.1 and 12.5", "verified_grounded")


def test_correct_refusal_for_false_friend_is_refused_correctly():
    question = _question(
        type="false_friend",
        expected_facts=[],
        target_citation="REFUSED_CORRECTLY",
    )
    grade = grade_eval_answer(
        question,
        "The provided papers do not state sufficient evidence to answer this question.",
        "verified_refusal",
    )
    assert grade == "refused_correctly"


def test_incorrect_non_refusal_for_trap_is_wrong():
    question = _question(
        type="unanswerable",
        expected_facts=[],
        target_citation="REFUSED_CORRECTLY",
    )
    grade = grade_eval_answer(
        question,
        "LLaMA 3 was pretrained with a batch size of 4M tokens.",
        "verified_grounded",
    )
    assert grade == "wrong"


def test_answerable_refusal_is_refused_wrongly():
    grade = grade_eval_answer(
        _question(),
        "The provided papers do not state sufficient evidence to answer this question.",
        "verified_refusal",
    )
    assert grade == "refused_wrongly"


@pytest.mark.parametrize(
    ("expected", "response"),
    [
        (["1.1"], "The value was 11.1 kbps."),
        (["1.1"], "The value was 1.10 kbps."),
        (["4000"], "The value was 14000 steps."),
        (["dna"], "We evaluated a DNase assay."),
    ],
)
def test_fact_matching_rejects_embedded_numeric_or_word_substrings(expected, response):
    grade = grade_eval_answer(_question(expected_facts=expected), response, "verified_grounded")
    assert grade == "wrong"


def test_real_q09_partial_answer_is_no_longer_correct():
    """The original defect graded a single 4x mention as fully correct for Q09."""
    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        questions = {q["id"]: q for q in json.load(f)}

    partial = grade_eval_answer(
        questions["Q09"],
        "H-Net achieved nearly a 4x improvement in data efficiency on DNA sequences.",
        "verified_grounded",
    )
    complete = grade_eval_answer(
        questions["Q09"],
        "H-Net achieved nearly a 4x improvement, specifically 3.6x compared to isotropic models.",
        "verified_grounded",
    )
    assert partial == "wrong"
    assert complete == "correct"


def test_real_q05_single_fact_is_no_longer_correct():
    with open("data/eval_questions.json", "r", encoding="utf-8") as f:
        questions = {q["id"]: q for q in json.load(f)}

    assert grade_eval_answer(
        questions["Q05"], "Mimi uses a bitrate of 1.1 kbps.", "verified_grounded"
    ) == "wrong"
    assert grade_eval_answer(
        questions["Q05"], "Mimi uses 12.5 Hz frames at a bitrate of 1.1 kbps.", "verified_grounded"
    ) == "correct"


def test_citation_grade_requires_exact_target_citation():
    question = _question()
    assert grade_citation(question, ["[Moshi, 3.3.1 Architecture, Page 11]"]) == CITATION_CORRECT
    assert grade_citation(question, ["[Moshi, 3.3.1 Architecture, Page 10]"]) == CITATION_WRONG
    assert grade_citation(question, []) == CITATION_WRONG


def test_citation_grade_not_applicable_for_traps():
    question = _question(type="false_friend", expected_facts=[], target_citation="REFUSED_CORRECTLY")
    assert grade_citation(question, []) == CITATION_NOT_APPLICABLE
