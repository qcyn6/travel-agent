from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.security import hash_password, normalize_username
from backend.database.tables import UserRow


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, user_id: str) -> UserRow | None:
        return self.session.scalar(select(UserRow).where(UserRow.id == user_id))

    def get_by_username(self, username: str) -> UserRow | None:
        return self.session.scalar(
            select(UserRow).where(UserRow.email == normalize_username(username))
        )

    def create(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        role: str = "user",
    ) -> UserRow:
        row = UserRow(
            id=str(uuid4()),
            # The existing column is retained for database compatibility; it
            # now stores the username rather than an email address.
            email=normalize_username(username),
            password_hash=hash_password(password),
            display_name=(display_name or username).strip()[:64],
            role=role,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_recent(self, *, limit: int = 100, role: str | None = None) -> list[UserRow]:
        statement = select(UserRow)
        if role:
            statement = statement.where(UserRow.role == role)
        return list(
            self.session.scalars(
                statement
                .order_by(UserRow.created_at.desc())
                .limit(limit)
            )
        )

    def ensure_bootstrap_admin(self, *, username: str, password: str) -> UserRow:
        existing = self.get_by_username(username)
        if existing is not None:
            if existing.role != "admin":
                existing.role = "admin"
                self.session.commit()
            return existing
        return self.create(username=username, password=password, role="admin", display_name="管理员")


__all__ = ["UserRepository"]
