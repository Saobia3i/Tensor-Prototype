"""Connector secret storage. Local/dev: Fernet-encrypted rows (RLS-protected). Production: replace with a cloud
secret manager by implementing the same three methods. Secrets never appear in connector rows, logs or prompts."""
from __future__ import annotations

import base64
import hashlib
import uuid

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import SecretRecord


def _fernet() -> Fernet:
    s = get_settings()
    key = s.secrets_key
    if not key:
        if not s.is_local:
            raise RuntimeError("TENSOR_SECRETS_KEY required")
        key = base64.urlsafe_b64encode(hashlib.sha256(b"tensor-local-dev-only").digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


class SecretStore:
    def __init__(self, session: Session) -> None:
        self.s = session

    def put(self, tenant_id: uuid.UUID, value: str) -> str:
        ref = f"sec_{uuid.uuid4().hex}"
        self.s.add(SecretRecord(tenant_id=tenant_id, ref=ref, ciphertext=_fernet().encrypt(value.encode()).decode()))
        return ref

    def get(self, tenant_id: uuid.UUID, ref: str) -> str:
        rec = self.s.execute(select(SecretRecord).where(SecretRecord.ref == ref,
                                                        SecretRecord.tenant_id == tenant_id)).scalar_one_or_none()
        if rec is None:
            raise KeyError("secret not found for this tenant")
        return _fernet().decrypt(rec.ciphertext.encode()).decode()

    def delete(self, tenant_id: uuid.UUID, ref: str) -> None:
        self.s.execute(delete(SecretRecord).where(SecretRecord.ref == ref, SecretRecord.tenant_id == tenant_id))
