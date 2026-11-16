"""Object storage adapter. Local filesystem for dev; production swaps in S3 (SSE-KMS, private, versioned)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from ..config import get_settings

_KEY_RE = re.compile(r"^[0-9a-f\-]{36}/(quarantine|evidence|reports|exports)/[0-9a-zA-Z\-_.]{1,200}$")


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def move(self, src: str, dst: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> str: ...


class LocalStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise ValueError("invalid object key")
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("object key escapes storage root")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def move(self, src: str, dst: str) -> None:
        d = self._path(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        self._path(src).replace(d)

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> str:
        return str(self._path(key))


@lru_cache
def get_store() -> ObjectStore:
    return LocalStore(get_settings().storage_dir)


def quarantine_key(tenant_id: object, evidence_id: object) -> str:
    return f"{tenant_id}/quarantine/{evidence_id}"


def evidence_key(tenant_id: object, evidence_id: object) -> str:
    return f"{tenant_id}/evidence/{evidence_id}"
