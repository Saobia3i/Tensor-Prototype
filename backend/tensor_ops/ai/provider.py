"""Provider-agnostic structured generation interface (ADR-005)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings, get_settings


class ProviderError(RuntimeError):
    pass


@dataclass
class AIResult:
    data: dict[str, Any]
    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw_text: str = ""

    def cost(self, s: Settings) -> float:
        return round(self.input_tokens / 1e6 * s.ai_price_in_per_mtok + self.output_tokens / 1e6 * s.ai_price_out_per_mtok, 6)


@dataclass
class Request:
    task_type: str
    system: str
    user: str
    schema: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)  # structured inputs; only the mock provider reads these
    max_output_tokens: int = 4000


class Provider(Protocol):
    name: str
    model: str

    def generate_structured(self, req: Request) -> AIResult: ...


class AnthropicProvider:
    """Claude via the official SDK, using structured outputs (output_config.format = json_schema)."""
    name = "anthropic"

    def __init__(self, s: Settings) -> None:
        import anthropic
        self.model = s.ai_model
        self._client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_s, max_retries=2)

    def generate_structured(self, req: Request) -> AIResult:
        import anthropic
        t0 = time.monotonic()
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=req.max_output_tokens,
                system=req.system,
                messages=[{"role": "user", "content": req.user}],
                output_config={"format": {"type": "json_schema", "schema": req.schema}},
            )
        except anthropic.APIError as e:
            raise ProviderError(f"provider error: {type(e).__name__}") from e
        latency = int((time.monotonic() - t0) * 1000)
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"model stopped: {resp.stop_reason}")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError("model returned non-JSON output") from e
        return AIResult(data=data, provider=self.name, model=resp.model, request_id=resp.id,
                        input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                        latency_ms=latency, raw_text=text)


_INJECTION = re.compile(r"(ignore (all |any )?(previous|prior|above) instructions|disregard .{0,30}instructions|"
                        r"you are now|system prompt|approve (this|all)|export all)", re.I)
_DATE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")
_STOP = set("the a an and or of to in on for is are be by with do does your you we our it this that have has as at "
            "from all any who what how when which per".split())


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2 and w not in _STOP}


def _quote_around(text: str, needle: str, width: int = 160) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return text.strip()[:width]
    a = max(0, i - 40)
    return text[a:a + width].strip()


class MockProvider:
    """Deterministic, offline provider for tests, evals and demos. Uses keyword evidence matching only."""
    name = "mock"
    model = "mock-keyword-v1"

    def generate_structured(self, req: Request) -> AIResult:
        t0 = time.monotonic()
        if req.task_type == "evidence_review":
            data = self._review(req.context)
        elif req.task_type == "questionnaire_answer":
            data = self._questionnaire(req.context)
        else:
            raise ProviderError(f"mock has no task {req.task_type}")
        return AIResult(data=data, provider=self.name, model=self.model, request_id=f"mock-{int(t0 * 1000)}",
                        input_tokens=0, output_tokens=0,  # offline: no billable usage
                        latency_ms=int((time.monotonic() - t0) * 1000))

    def _review(self, ctx: dict[str, Any]) -> dict[str, Any]:
        chunks, controls = ctx["chunks"], ctx["controls"]
        suspicious = any(_INJECTION.search(c["text"]) for c in chunks)
        cands, cites, seen = [], [], set()
        for ctl in controls:
            best = None
            for c in chunks:
                for kw in ctl["keywords"]:
                    if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()), c["text"].lower()):
                        best = (c, kw)
                        break
                if best:
                    break
            if best:
                c, kw = best
                cands.append({"control_code": ctl["code"], "citation_chunk_ids": [c["handle"]],
                              "rationale": f"The document mentions '{kw}', which is typical evidence for: {ctl['title']}."})
                if c["handle"] not in seen:
                    cites.append({"chunk_id": c["handle"], "quote": _quote_around(c["text"], kw)})
                    seen.add(c["handle"])
        cands = cands[:5]
        dates = sorted({d for c in chunks for d in _DATE.findall(c["text"])})
        gaps = [] if cands else ["No passage supports any in-scope control."]
        if suspicious:
            gaps.append("Document contains instruction-like text; it was ignored.")
        return {
            "evidence_type": "Document" if not cands else f"Evidence for {cands[0]['control_code']}",
            "summary": f"Keyword-based draft review covering {len(chunks)} passage(s).",
            "citations": cites,
            "candidate_controls": cands,
            "period_start": dates[0] if dates else "",
            "period_end": dates[-1] if dates else "",
            "gaps": gaps,
            "contradictions": [],
            "confidence": 0.35 if not cands else min(0.8, 0.45 + 0.1 * len(cands)),
            "decision": "insufficient_evidence" if not cands else ("supported" if len(cands) >= 1 and not suspicious
                                                                    else "partially_supported"),
            "suspicious_instructions_detected": suspicious,
        }

    def _questionnaire(self, ctx: dict[str, Any]) -> dict[str, Any]:
        q = _words(ctx["question"])
        best, score = None, 0
        for c in ctx["chunks"]:
            s = len(q & _words(c["text"]))
            if s > score:
                best, score = c, s
        if not best or score < 2:
            return {"answer": "", "answer_type": "insufficient_evidence", "citations": [], "confidence": 0.2,
                    "follow_up_question": f"Please provide documentation that answers: {ctx['question']}"}
        sentence = next((x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", best["text"])
                         if len(q & _words(x)) >= 1 and len(x.strip()) > 15), best["text"].strip()[:180])[:200]
        return {"answer": f"Yes. {sentence}", "answer_type": "yes",
                "citations": [{"chunk_id": best["handle"], "quote": sentence}],
                "confidence": min(0.85, 0.4 + 0.1 * score), "follow_up_question": ""}


_override: Provider | None = None


def set_provider_override(p: Provider | None) -> None:
    """Test/eval hook to inject a scripted provider."""
    global _override
    _override = p


def get_provider() -> Provider:
    if _override is not None:
        return _override
    s = get_settings()
    if s.ai_provider == "anthropic":
        return AnthropicProvider(s)
    return MockProvider()
