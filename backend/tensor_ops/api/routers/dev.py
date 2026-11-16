"""Local-only developer login for seeded synthetic users. Disabled (404) outside local/test."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...models import User
from ...security.auth import issue_dev_token
from ..deps import get_db

router = APIRouter(tags=["dev"], prefix="/dev")


def _local_only(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.is_local:
        raise HTTPException(status_code=404, detail="Not found")
    return settings


class LoginIn(BaseModel):
    user_id: uuid.UUID
    mfa: bool = True


@router.get("/users")
def users(_: Settings = Depends(_local_only), s: Session = Depends(get_db)) -> list[dict]:
    return [{"id": str(u.id), "email": u.email, "name": u.name, "is_staff": u.is_staff}
            for u in s.execute(select(User).where(User.active.is_(True)).order_by(User.is_staff.desc(), User.email)).scalars()]


@router.post("/login")
def login(body: LoginIn, settings: Settings = Depends(_local_only), s: Session = Depends(get_db)) -> dict:
    u = s.get(User, body.user_id)
    if u is None or not u.active:
        raise HTTPException(status_code=404, detail="Not found")
    return {"access_token": issue_dev_token(settings, u.id, mfa=body.mfa), "token_type": "bearer"}
