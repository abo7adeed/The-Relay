"""Strict, auditable grading for the 15-question evaluation set.

Two independent judgments are produced:

* ``grade_eval_answer`` checks that every configured mandatory expected fact is
  present.  Partial answers are ``wrong``; they are never ``correct``.
* ``grade_citation`` checks that the response carries the exact target citation
  from ``data/eval_questions.json``.  It does **not** re-verify the evidence
  quotation per response; corpus-level quotation provenance is covered by
  ``tests/test_eval_set.py`` and the pipeline verifier covers fetched-passage
  provenance and numeric consistency.

A missing or empty ``expected_facts`` configuration for an answerable question
raises ``ValueError``.  Silently treating an empty list as "all facts present"
would recreate the original grading defect in a different form.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

TRAP_TYPES = frozenset({"false_friend", "unanswerable"})

GRADE_CORRECT = "correct"
GRADE_WRONG = "wrong"
GRADE_REFUSED_CORRECTLY = "refused_correctly"
GRADE_REFUSED_WRONGLY = "refused_wrongly"

CITATION_CORRECT = "citation_correct"
CITATION_WRONG = "citation_wrong"
CITATION_NOT_APPLICABLE = "citation_not_applicable"

_NUMERIC_FACT_RE = re.compile(r"^(\d+(?:\.\d+)?)(x?)$", re.IGNORECASE)
_REFUSAL_INDICATORS = (
    "do not state",
    "does not state",
    "not contain information",
    "cannot answer",
    "not mentioned",
    "not provided",
    "do not provide",
    "unable to verify",
)


def _normalize_text(text: str) -> str:
    """Normalize case, PDF decimal spacing, and multiplication notation."""
    normalized = str(text).casefold()
    normalized = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", normalized)
    normalized = re.sub(r"(?<=\d)\s*[x\u00d7]\s*", "x", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _fact_present(fact: str, normalized_response: str) -> bool:
    """Match a fact without accepting embedded numeric substrings.

    ``1.1`` must not match ``11.1`` or ``1.10``; ``4000`` must not match
    ``14000``.  Text facts use alphanumeric word boundaries so ``dna`` does not
    match ``dnase``.
    """
    normalized_fact = _normalize_text(fact)
    if not normalized_fact:
        return False

    escaped = re.escape(normalized_fact)
    if _NUMERIC_FACT_RE.fullmatch(normalized_fact):
        return re.search(
            rf"(?<![\d.]){escaped}(?![\d.])", normalized_response
        ) is not None

    if re.fullmatch(r"[a-z0-9 ]+", normalized_fact):
        return re.search(
            rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", normalized_response
        ) is not None

    return normalized_fact in normalized_response


def validate_expected_facts(question: Dict[str, Any]) -> List[str]:
    """Return mandatory facts for an answerable question or fail loudly.

    Trap/out-of-library questions are refusal-only and intentionally have no
    expected facts.
    """
    qtype = question.get("type")
    if not qtype:
        raise ValueError(f"Question {question.get('id', '<unknown>')} has no type.")
    if qtype in TRAP_TYPES:
        return []

    facts = question.get("expected_facts")
    if facts is None:
        raise ValueError(
            f"Question {question.get('id', '<unknown>')} has no expected_facts "
            "configuration; refusing to grade with an implicit empty set."
        )
    if not isinstance(facts, list):
        raise ValueError(
            f"Question {question.get('id', '<unknown>')} expected_facts must be a list."
        )

    cleaned: List[str] = []
    for fact in facts:
        if not isinstance(fact, str) or not fact.strip():
            raise ValueError(
                f"Question {question.get('id', '<unknown>')} contains an empty "
                "or non-string expected fact."
            )
        cleaned.append(fact.strip())

    if not cleaned:
        raise ValueError(
            f"Question {question.get('id', '<unknown>')} has an empty "
            "expected_facts configuration; cannot grade as correct."
        )
    return cleaned


def _is_refusal(response_text: str, verification_status: str) -> bool:
    response_lower = _normalize_text(response_text)
    return (
        verification_status == "verified_refusal"
        or any(indicator in response_lower for indicator in _REFUSAL_INDICATORS)
    )


def grade_eval_answer(
    question: Dict[str, Any],
    response_text: str,
    verification_status: str,
) -> str:
    """Grade a benchmark answer against the configured mandatory facts.

    Refusal handling is preserved for false-friend and unanswerable questions.
    For answerable questions, every mandatory fact must be present.
    """
    qtype = question.get("type")
    if not qtype:
        raise ValueError(f"Question {question.get('id', '<unknown>')} has no type.")

    facts = validate_expected_facts(question)
    refused = _is_refusal(response_text, verification_status)

    if qtype in TRAP_TYPES:
        return GRADE_REFUSED_CORRECTLY if refused else GRADE_WRONG

    if refused or verification_status != "verified_grounded":
        return GRADE_REFUSED_WRONGLY

    normalized_response = _normalize_text(response_text)
    if all(_fact_present(fact, normalized_response) for fact in facts):
        return GRADE_CORRECT
    return GRADE_WRONG


def _normalize_citation(citation: str) -> str:
    value = str(citation).strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return _normalize_text(value)


def grade_citation(
    question: Dict[str, Any],
    citations: Optional[Iterable[str]],
) -> str:
    """Check whether the exact target citation is present in the response.

    This is a citation-identity check only.  It is not a per-response evidence
    quotation/entailment check.
    """
    if question.get("type") in TRAP_TYPES:
        return CITATION_NOT_APPLICABLE

    target = question.get("target_citation")
    if not target or target == "REFUSED_CORRECTLY":
        return CITATION_NOT_APPLICABLE

    target_normalized = _normalize_citation(target)
    for citation in citations or []:
        if _normalize_citation(citation) == target_normalized:
            return CITATION_CORRECT
    return CITATION_WRONG
