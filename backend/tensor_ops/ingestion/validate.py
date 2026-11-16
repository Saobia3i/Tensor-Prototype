"""File type detection from content (magic bytes), not from the client-supplied name or MIME type."""
from __future__ import annotations

import io
import zipfile

ALLOWED_EXT = {
    "pdf": {"pdf"}, "docx": {"docx"}, "xlsx": {"xlsx"}, "text": {"txt", "md", "csv", "json", "log", "yaml", "yml"},
    "png": {"png"}, "jpeg": {"jpg", "jpeg"},
}


class ValidationError(ValueError):
    pass


def detect_type(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if len(names) > 2000:
                    raise ValidationError("archive has too many entries")
                total = sum(i.file_size for i in z.infolist())
                if total > 200 * 1024 * 1024:
                    raise ValidationError("archive expands too much (possible zip bomb)")
                if "[Content_Types].xml" in names:
                    if any(n.startswith("word/") for n in names):
                        return "docx"
                    if any(n.startswith("xl/") for n in names):
                        return "xlsx"
        except zipfile.BadZipFile as e:
            raise ValidationError("corrupt zip container") from e
        raise ValidationError("unsupported zip-based file")
    if b"\x00" in data[:8192]:
        raise ValidationError("binary file type not supported")
    try:
        data[:65536].decode("utf-8")
    except UnicodeDecodeError:
        try:
            data[:65535].decode("utf-8")  # tolerate a multi-byte char cut at the boundary
        except UnicodeDecodeError as e:
            raise ValidationError("text file is not valid UTF-8") from e
    return "text"


def validate_file(filename: str, data: bytes, max_bytes: int) -> str:
    if len(data) == 0:
        raise ValidationError("empty file")
    if len(data) > max_bytes:
        raise ValidationError(f"file exceeds {max_bytes} bytes")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = detect_type(data)
    if ext not in ALLOWED_EXT.get(kind, set()):
        raise ValidationError(f"extension .{ext} does not match detected content type {kind}")
    return kind
