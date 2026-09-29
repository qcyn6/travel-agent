from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.auth.repository import UserRepository
from backend.auth.security import decode_access_token
from backend.config import Settings, get_settings
from backend.database import get_db


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser | None:
    header = request.headers.get("Authorization", "")
    if not header:
        auth_required = (
            not getattr(request.app.state, "auth_bypass", False)
            and (settings.auth_required or settings.app_env.lower() == "production")
        )
        if auth_required:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证格式不正确")
    payload = decode_access_token(token.strip(), secret=settings.auth_secret)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证已失效")
    row = UserRepository(session).get(str(payload["sub"]))
    if row is None or not row.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用")
    return CurrentUser(
        id=row.id,
        username=row.email,
        display_name=row.display_name,
        role=row.role,
    )


def require_admin(user: CurrentUser | None = Depends(get_current_user)) -> CurrentUser:
    if user is None or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


__all__ = ["CurrentUser", "get_current_user", "require_admin"]
