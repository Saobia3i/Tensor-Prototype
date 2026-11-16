"""Connector contract (pack 06 §2). Read-only by default; write actions are not part of v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ConnectorAuthError(Exception):
    """Credentials invalid/revoked: stop syncing and alert the owner (no retry)."""


class ConnectorScopeError(Exception):
    """Credential works but lacks a needed permission: visible scope mismatch (no retry)."""


class ConnectorRateLimited(Exception):
    """Retry later with backoff."""


@dataclass
class SyncOutput:
    title: str
    filename: str
    document: dict[str, Any]
    source_ref: str
    checkpoint: dict[str, Any]


class ConnectorImpl(Protocol):
    provider: str
    read_scopes: list[str]

    def validate_connection(self) -> dict[str, Any]: ...
    def sync(self, checkpoint: dict[str, Any]) -> SyncOutput: ...
    def health_check(self) -> str: ...
