"""Sandboxed extraction child process. Invoked as: python -I extract_child.py <path> <kind> (stdlib + parsers only)

Runs with CPU/memory/file rlimits set by the parent. Prints JSON {"pages": [{"page": n, "text": "..."}], "method": ...}.
"""
from __future__ import annotations

import json
import sys

MAX_CHARS = 2_000_000


def extract(path: str, kind: str) -> dict:
    pages: list[dict] = []
    if kind == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("encrypted PDF")
        for i, p in enumerate(reader.pages[:500], start=1):
            pages.append({"page": i, "text": p.extract_text() or ""})
        method = "pypdf"
    elif kind == "docx":
        import io

        import docx
        with open(path, "rb") as fh:
            d = docx.Document(io.BytesIO(fh.read()))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        pages.append({"page": 1, "text": "\n".join(parts)})
        method = "python-docx"
    elif kind == "xlsx":
        import io

        import openpyxl
        with open(path, "rb") as fh:  # stored objects have no extension; openpyxl needs a file object then
            wb = openpyxl.load_workbook(io.BytesIO(fh.read()), read_only=True, data_only=True)
        for i, ws in enumerate(wb.worksheets[:50], start=1):
            rows = []
            for r in ws.iter_rows(max_row=5000, values_only=True):
                rows.append(" | ".join("" if v is None else str(v) for v in r))
            pages.append({"page": i, "text": f"[sheet: {ws.title}]\n" + "\n".join(rows)})
        method = "openpyxl"
    elif kind == "text":
        with open(path, "rb") as f:
            pages.append({"page": 1, "text": f.read().decode("utf-8", errors="replace")})
        method = "utf8"
    elif kind in ("png", "jpeg"):
        pages.append({"page": 1, "text": ""})
        method = "image-no-ocr"
    else:
        raise ValueError(f"unsupported kind {kind}")
    total = 0
    for p in pages:
        p["text"] = p["text"][: max(0, MAX_CHARS - total)]
        total += len(p["text"])
    return {"pages": pages, "method": method}


if __name__ == "__main__":
    try:
        out = extract(sys.argv[1], sys.argv[2])
        sys.stdout.write(json.dumps(out))
    except Exception as e:  # report the error class only; never echo document content
        sys.stdout.write(json.dumps({"error": type(e).__name__}))
        sys.exit(2)
