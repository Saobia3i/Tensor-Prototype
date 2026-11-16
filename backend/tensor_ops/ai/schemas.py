"""Versioned output schemas. JSON Schema is sent to the provider; Pydantic re-validates the response in code."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EVIDENCE_REVIEW_V = "evidence_review.v1"
QUESTIONNAIRE_V = "questionnaire_answer.v1"

_CITATION = {
    "type": "object",
    "properties": {"chunk_id": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["chunk_id", "quote"],
    "additionalProperties": False,
}

EVIDENCE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_type": {"type": "string", "description": "Short label, e.g. 'Access review sign-off'"},
        "summary": {"type": "string", "description": "Two or three factual sentences about what the document shows."},
        "citations": {"type": "array", "items": _CITATION},
        "candidate_controls": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "control_code": {"type": "string"},
                "rationale": {"type": "string"},
                "citation_chunk_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["control_code", "rationale", "citation_chunk_ids"],
            "additionalProperties": False,
        }},
        "period_start": {"type": "string", "description": "YYYY-MM-DD or empty string if not stated"},
        "period_end": {"type": "string", "description": "YYYY-MM-DD or empty string if not stated"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "description": "0.0 to 1.0"},
        "decision": {"type": "string", "enum": ["supported", "partially_supported", "insufficient_evidence"]},
        "suspicious_instructions_detected": {"type": "boolean"},
    },
    "required": ["evidence_type", "summary", "citations", "candidate_controls", "period_start", "period_end", "gaps",
                 "contradictions", "confidence", "decision", "suspicious_instructions_detected"],
    "additionalProperties": False,
}

QUESTIONNAIRE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "answer_type": {"type": "string",
                        "enum": ["yes", "no", "partial", "insufficient_evidence", "not_applicable"]},
        "citations": {"type": "array", "items": _CITATION},
        "confidence": {"type": "number"},
        "follow_up_question": {"type": "string", "description": "What to ask the client if evidence is missing; else empty"},
    },
    "required": ["answer", "answer_type", "citations", "confidence", "follow_up_question"],
    "additionalProperties": False,
}


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str = Field(max_length=20)
    quote: str = Field(max_length=400)


class CandidateControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_code: str = Field(max_length=40)
    rationale: str = Field(max_length=1500)
    citation_chunk_ids: list[str] = Field(max_length=10)


class EvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_type: str = Field(max_length=200)
    summary: str = Field(max_length=2000)
    citations: list[Citation] = Field(max_length=30)
    candidate_controls: list[CandidateControl] = Field(max_length=15)
    period_start: str
    period_end: str
    gaps: list[str] = Field(max_length=20)
    contradictions: list[str] = Field(max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    decision: Literal["supported", "partially_supported", "insufficient_evidence"]
    suspicious_instructions_detected: bool


class QuestionnaireAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=3000)
    answer_type: Literal["yes", "no", "partial", "insufficient_evidence", "not_applicable"]
    citations: list[Citation] = Field(max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)
    follow_up_question: str = Field(max_length=1000)
