import re
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.database import get_db
from backend.database.tables import ConversationMessageRow, ConversationRow
from backend.models.conversation import RequirementDraft


DEFAULT_CONVERSATION_TITLE = "新的旅行计划"


def _title_from_message(content: str) -> str:
    first_sentence = re.split(r"[\r\n。！？!?；;]", content.strip(), maxsplit=1)[0]
    compact = re.sub(r"\s+", " ", first_sentence).strip(" ，,、:：")
    if not compact:
        return DEFAULT_CONVERSATION_TITLE
    return f"{compact[:24]}…" if len(compact) > 24 else compact


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self) -> ConversationRow:
        row = ConversationRow(
            id=str(uuid4()),
            title=DEFAULT_CONVERSATION_TITLE,
            requirement_draft=RequirementDraft().model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(
        self,
        conversation_id: str,
        *,
        with_messages: bool = False,
        for_update: bool = False,
    ) -> ConversationRow | None:
        query = select(ConversationRow).where(ConversationRow.id == conversation_id)
        if with_messages:
            query = query.options(selectinload(ConversationRow.messages))
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_recent(self, *, limit: int = 30) -> list[ConversationRow]:
        has_messages = select(ConversationMessageRow.id).where(
            ConversationMessageRow.conversation_id == ConversationRow.id
        ).exists()
        query = (
            select(ConversationRow)
            .options(selectinload(ConversationRow.messages))
            .where(or_(has_messages, ConversationRow.status != "draft"))
            .order_by(ConversationRow.updated_at.desc(), ConversationRow.created_at.desc())
            .limit(limit)
        )
        rows = list(self.session.scalars(query))
        for row in rows:
            if row.title != DEFAULT_CONVERSATION_TITLE:
                continue
            first_user_message = next((message for message in row.messages if message.role == "user"), None)
            if first_user_message is None:
                continue
            # Keep legacy records readable without changing their stored activity time.
            row.title = _title_from_message(first_user_message.content)
        return rows

    def delete(self, conversation_id: str) -> bool:
        row = self.get(conversation_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def save_requirement(
        self,
        row: ConversationRow,
        requirement: RequirementDraft,
        *,
        confirmed: bool | None = None,
        commit: bool = True,
    ) -> None:
        row.requirement_draft = requirement.model_dump(mode="json")
        if confirmed is not None:
            row.requirement_confirmed = confirmed
            row.status = "confirmed" if confirmed else "draft"
        destination = requirement.cities[0] if requirement.cities else requirement.province
        if destination:
            destination_name = destination.removesuffix("省").removesuffix("市")
            row.title = f"{destination_name}之旅"
        if commit:
            self.session.commit()
            self.session.refresh(row)
        else:
            self.session.flush()

    def set_status(self, row: ConversationRow, status: str, *, commit: bool = True) -> None:
        row.status = status
        if commit:
            self.session.commit()
            self.session.refresh(row)
        else:
            self.session.flush()

    def add_message(self, row: ConversationRow, *, role: str, content: str) -> ConversationMessageRow:
        # Serialize sequence allocation per conversation. The unique constraint
        # remains the final guard, while this lock prevents two simultaneous
        # requests from calculating the same next sequence number on MySQL.
        self.session.scalar(
            select(ConversationRow.id)
            .where(ConversationRow.id == row.id)
            .with_for_update()
        )
        next_number = self.session.scalar(
            select(func.coalesce(func.max(ConversationMessageRow.sequence_number), 0) + 1).where(
                ConversationMessageRow.conversation_id == row.id
            )
        )
        message = ConversationMessageRow(
            conversation_id=row.id,
            sequence_number=int(next_number or 1),
            role=role,
            content=content,
        )
        if role == "user" and row.title == DEFAULT_CONVERSATION_TITLE:
            row.title = _title_from_message(content)
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message


def get_conversation_repository(session: Session = Depends(get_db)) -> ConversationRepository:
    return ConversationRepository(session)
