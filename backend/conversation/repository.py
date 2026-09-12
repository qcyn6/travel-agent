from uuid import uuid4

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.database import get_db
from backend.database.tables import ConversationMessageRow, ConversationRow
from backend.models.conversation import RequirementDraft


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self) -> ConversationRow:
        row = ConversationRow(
            id=str(uuid4()),
            title="新的旅行计划",
            requirement_draft=RequirementDraft().model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(self, conversation_id: str, *, with_messages: bool = False) -> ConversationRow | None:
        query = select(ConversationRow).where(ConversationRow.id == conversation_id)
        if with_messages:
            query = query.options(selectinload(ConversationRow.messages))
        return self.session.scalar(query)

    def save_requirement(
        self,
        row: ConversationRow,
        requirement: RequirementDraft,
        *,
        confirmed: bool | None = None,
    ) -> None:
        row.requirement_draft = requirement.model_dump(mode="json")
        if confirmed is not None:
            row.requirement_confirmed = confirmed
            row.status = "confirmed" if confirmed else "draft"
        destination = requirement.cities[0] if requirement.cities else requirement.province
        if destination:
            row.title = f"{destination}旅行计划"
        self.session.commit()
        self.session.refresh(row)

    def set_status(self, row: ConversationRow, status: str) -> None:
        row.status = status
        self.session.commit()
        self.session.refresh(row)

    def add_message(self, row: ConversationRow, *, role: str, content: str) -> ConversationMessageRow:
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
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message


def get_conversation_repository(session: Session = Depends(get_db)) -> ConversationRepository:
    return ConversationRepository(session)
