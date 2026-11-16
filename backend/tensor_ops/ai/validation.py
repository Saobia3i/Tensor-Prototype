"""Code-enforced validation of model output: schema, provenance and business rules."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from .schemas import EvidenceReview, QuestionnaireAnswer

_BANNED = re.compile(r"\b(is|are|fully|now)\s+(soc ?2\s+|iso ?27001\s+)?(compliant|certified)\b|\bpass(es|ed)? the audit\b|"
                     r"\bcontrol is effective\b|\bguarantee(d)? (compliance|certification)\b", re.I)


class InvalidOutput(ValueError):
    pass


@dataclass
class Validated:
    data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip().lower()


def _check_citations(cites: list[dict], chunk_text: dict[str, str]) -> None:
    for c in cites:
        h = c["chunk_id"]
        if h not in chunk_text:
            raise InvalidOutput(f"citation to unknown chunk id {h!r} (not in provided context)")
        q = _norm(c["quote"])
        if len(q) < 3:
            raise InvalidOutput(f"empty quote for {h}")
        if q not in _norm(chunk_text[h]):
            raise InvalidOutput(f"quote for {h} does not appear verbatim in the cited chunk")


def _iso_or_empty(v: str, name: str) -> None:
    if v == "":
        return
    try:
        date.fromisoformat(v)
    except ValueError as e:
        raise InvalidOutput(f"{name} is not YYYY-MM-DD or empty") from e


def validate_evidence_review(data: dict[str, Any], chunk_text: dict[str, str], allowed_controls: set[str],
                             review_date: date | None = None) -> Validated:
    try:
        m = EvidenceReview.model_validate(data)
    except ValidationError as e:
        raise InvalidOutput(f"schema: {e.errors()[0]['loc']} {e.errors()[0]['msg']}") from e
    d = m.model_dump()
    warnings: list[str] = []
    _check_citations(d["citations"], chunk_text)
    for cc in d["candidate_controls"]:
        if cc["control_code"] not in allowed_controls:
            raise InvalidOutput(f"unknown or out-of-scope control {cc['control_code']!r}")
        if not cc["citation_chunk_ids"]:
            raise InvalidOutput(f"control {cc['control_code']} proposed without a citation")
        for h in cc["citation_chunk_ids"]:
            if h not in chunk_text:
                raise InvalidOutput(f"control {cc['control_code']} cites unknown chunk {h!r}")
    _iso_or_empty(d["period_start"], "period_start")
    _iso_or_empty(d["period_end"], "period_end")
    if d["decision"] != "insufficient_evidence" and not d["candidate_controls"]:
        warnings.append("decision downgraded to insufficient_evidence: no supported control")
        d["decision"] = "insufficient_evidence"
    if d["decision"] == "insufficient_evidence" and d["candidate_controls"]:
        warnings.append("insufficient_evidence with candidate controls: candidates kept for expert judgement")
    text = " ".join([d["summary"], *(c["rationale"] for c in d["candidate_controls"])])
    if _BANNED.search(text):
        warnings.append("output contains a compliance conclusion; conclusions are human-owned and were not applied")
    if d["suspicious_instructions_detected"]:
        warnings.append("model flagged instruction-like text inside the evidence (possible prompt injection)")
    if review_date and d["period_end"]:
        age = (review_date - date.fromisoformat(d["period_end"])).days
        if age > 365:
            warnings.append(f"evidence period ended {age} days before review (stale)")
    return Validated(d, warnings)


def validate_questionnaire(data: dict[str, Any], chunk_text: dict[str, str]) -> Validated:
    try:
        m = QuestionnaireAnswer.model_validate(data)
    except ValidationError as e:
        raise InvalidOutput(f"schema: {e.errors()[0]['loc']} {e.errors()[0]['msg']}") from e
    d = m.model_dump()
    warnings: list[str] = []
    _check_citations(d["citations"], chunk_text)
    if d["answer_type"] in ("yes", "no", "partial") and not d["citations"]:
        raise InvalidOutput("substantive answer without any citation")
    if _BANNED.search(d["answer"]):
        warnings.append("answer contains a compliance/certification claim; verify against an issued report")
    return Validated(d, warnings)
