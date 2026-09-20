import re
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.database.tables import GeneralConversationRow
from backend.models.conversation import (
    ConversationMessage,
    GeneralConversation,
)


DEFAULT_GENERAL_CONVERSATION_TITLE = "新的对话"


def _title_from_message(content: str) -> str:
    first_sentence = re.split(r"[\r\n。！？!?；;]", content.strip(), maxsplit=1)[0]
    compact = re.sub(r"\s+", " ", first_sentence).strip(" ，,、:：")
    if not compact:
        return DEFAULT_GENERAL_CONVERSATION_TITLE
    return f"{compact[:24]}…" if len(compact) > 24 else compact


class GeneralConversationRepository:
    """Persistence boundary for ordinary chat sessions only."""

    def __init__(self, session: Session):
        self.session = session

    def create(self) -> GeneralConversationRow:
        row = GeneralConversationRow(
            id=str(uuid4()),
            title=DEFAULT_GENERAL_CONVERSATION_TITLE,
            status="active",
            messages=[],
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(self, conversation_id: str) -> GeneralConversationRow | None:
        return self.session.scalar(
            select(GeneralConversationRow).where(GeneralConversationRow.id == conversation_id)
        )

    def list_recent(self, *, limit: int = 30) -> list[GeneralConversationRow]:
        rows = list(
            self.session.scalars(
                select(GeneralConversationRow).order_by(
                    GeneralConversationRow.updated_at.desc(),
                    GeneralConversationRow.created_at.desc(),
                )
            )
        )
        result = [row for row in rows if row.messages]
        for row in result:
            if row.title == DEFAULT_GENERAL_CONVERSATION_TITLE:
                first_user_message = next(
                    (
                        item
                        for item in row.messages
                        if item.get("role") == "user"
                    ),
                    None,
                )
                if first_user_message:
                    row.title = _title_from_message(first_user_message.get("content", ""))
        return result[:limit]

    def delete(self, conversation_id: str) -> bool:
        row = self.get(conversation_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def add_message(self, row: GeneralConversationRow, *, role: str, content: str) -> None:
        # Lock the single JSON row before replacing the message list so two
        # simultaneous ordinary replies cannot overwrite one another.
        locked = self.session.scalar(
            select(GeneralConversationRow)
            .where(GeneralConversationRow.id == row.id)
            .with_for_update()
        )
        if locked is None:
            return
        messages = list(locked.messages or [])
        messages.append({"role": role, "content": content})
        locked.messages = messages
        if role == "user" and locked.title == DEFAULT_GENERAL_CONVERSATION_TITLE:
            locked.title = _title_from_message(content)
        self.session.commit()

    @staticmethod
    def to_model(row: GeneralConversationRow) -> GeneralConversation:
        timestamp = row.updated_at or row.created_at
        messages = [
            ConversationMessage(
                id=index,
                sequence_number=index,
                role=item["role"],
                content=item["content"],
                created_at=timestamp,
            )
            for index, item in enumerate(row.messages or [], start=1)
        ]
        return GeneralConversation(
            id=row.id,
            title=row.title,
            status="active",
            messages=messages,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def get_general_conversation_repository(
    session: Session = Depends(get_db),
) -> GeneralConversationRepository:
    return GeneralConversationRepository(session)


__all__ = [
    "DEFAULT_GENERAL_CONVERSATION_TITLE",
    "GeneralConversationRepository",
    "get_general_conversation_repository",
]
