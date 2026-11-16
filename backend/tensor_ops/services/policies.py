"""Policy drafting from Tensor-authored templates ("templates for policies, never for conclusions")."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "content" / "policies"
_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

DEFAULTS = {
    "effective_date": lambda: date.today().isoformat(),
    "rto": lambda: "24 hours", "rpo": lambda: "24 hours", "backup_retention": lambda: "35 days",
    "customer_data_retention": lambda: "90 days", "incident_channel": lambda: "the #security-incidents channel",
}


def list_templates() -> list[dict[str, Any]]:
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.md")):
        meta, body = _split(p.read_text())
        out.append({"key": p.stem, "title": meta.get("title", p.stem), "controls": meta.get("controls", ""),
                    "parameters": sorted(set(_VAR.findall(body)))})
    return out


def _split(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---\n")
        for line in head.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        return meta, body.lstrip()
    return meta, text


def render(key: str, params: dict[str, str]) -> tuple[str, str, list[str]]:
    if not re.fullmatch(r"[a-z0-9\-]+", key):
        raise ValueError("bad template key")
    path = TEMPLATE_DIR / f"{key}.md"
    if not path.exists():
        raise ValueError("unknown template")
    meta, body = _split(path.read_text())
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        k = m.group(1)
        v = (params.get(k) or "").strip()
        if not v and k in DEFAULTS:
            v = DEFAULTS[k]()
        if not v:
            missing.append(k)
            return f"[{k.replace('_', ' ').upper()} — TO CONFIRM]"
        return re.sub(r"[<>{}]", "", v)[:200]

    rendered = _VAR.sub(sub, body)
    return meta.get("title", key), rendered, sorted(set(missing))
