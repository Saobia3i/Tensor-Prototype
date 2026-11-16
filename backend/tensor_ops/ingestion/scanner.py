"""Malware scanning adapters. Files are never parsed until a scan returns clean."""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from ..config import get_settings

# The standard harmless antivirus test string, assembled at import time so this source file
# itself does not contain the signature (desktop antivirus would otherwise flag the repo).
EICAR = b"".join([b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$", b"EICAR-STANDARD-", b"ANTIVIRUS-TEST-FILE!$H+H*"])


@dataclass
class ScanResult:
    clean: bool
    detail: str


class ScanError(RuntimeError):
    pass


class Scanner(Protocol):
    def scan(self, data: bytes) -> ScanResult: ...


class DevScanner:
    """Local development only (refused by config outside local): detects the EICAR test signature."""

    def scan(self, data: bytes) -> ScanResult:
        if EICAR in data:
            return ScanResult(False, "Eicar-Test-Signature FOUND (dev scanner)")
        return ScanResult(True, "clean (dev scanner: EICAR check only)")


class ClamAVScanner:
    """clamd INSTREAM protocol over TCP."""

    def __init__(self, host: str, port: int, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), 1 << 16):
                    chunk = data[i:i + (1 << 16)]
                    s.sendall(struct.pack("!L", len(chunk)) + chunk)
                s.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = s.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError as e:
            raise ScanError(f"clamd unavailable: {e}") from e
        text = reply.rstrip(b"\0").decode(errors="replace")
        if text.endswith("OK"):
            return ScanResult(True, "clean (clamav)")
        if "FOUND" in text:
            return ScanResult(False, text.split(":", 1)[-1].strip())
        raise ScanError(f"unexpected clamd reply: {text[:200]}")


def get_scanner() -> Scanner:
    s = get_settings()
    if s.scanner == "clamav":
        return ClamAVScanner(s.clamd_host, s.clamd_port)
    return DevScanner()
