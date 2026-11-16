"""Parent side of sandboxed extraction plus chunking with provenance."""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
from dataclasses import dataclass

from ..config import get_settings


class ExtractionError(RuntimeError):
    pass


@dataclass
class Chunk:
    ordinal: int
    page: int
    char_start: int
    char_end: int
    text: str


def _limits(mem_mb: int, cpu_s: int):  # type: ignore[no-untyped-def]
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # child may not write files
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    return apply


def run_extraction(path: str, kind: str) -> dict:
    s = get_settings()
    child = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_child.py")
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        # Isolated interpreter (-I): no user site, no env-driven imports, script dir not on sys.path.
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", child, path, kind],
            capture_output=True, timeout=s.extract_timeout_s, env=env, cwd="/",
            preexec_fn=_limits(s.extract_memory_mb, s.extract_timeout_s),
        )
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("parser timeout") from e
    try:
        out = json.loads(proc.stdout or b"{}")
    except json.JSONDecodeError as e:
        raise ExtractionError(f"parser crashed (exit {proc.returncode})") from e
    if proc.returncode != 0 or "error" in out:
        raise ExtractionError(f"parser failed: {out.get('error', proc.returncode)}")
    return out


def chunk_pages(pages: list[dict], target: int = 1200) -> list[Chunk]:
    chunks: list[Chunk] = []
    n = 0
    for p in pages:
        text: str = p["text"]
        if not text.strip():
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + target)
            if end < len(text):
                cut = text.rfind("\n", start + target // 2, end)
                if cut == -1:
                    cut = text.rfind(" ", start + target // 2, end)
                if cut != -1:
                    end = cut + 1
            piece = text[start:end]
            if piece.strip():
                chunks.append(Chunk(ordinal=n, page=p["page"], char_start=start, char_end=end, text=piece))
                n += 1
            start = end
    return chunks
