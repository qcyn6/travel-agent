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
_ROUTE_SUMMARY_PATTERN = re.compile(
    r"^.+：建议(?:步行|公共交通|驾车)，距离约.+，预计\d+分钟。?$"
)


def _title_from_message(content: str) -> str:
    first_sentence = re.split(r"[\r\n。！？!?；;]", content.strip(), maxsplit=1)[0]
    compact = re.sub(r"\s+", " ", first_sentence).strip(" ，,、:：")
    if not compact:
        return DEFAULT_GENERAL_CONVERSATION_TITLE
    return f"{compact[:24]}…" if len(compact) > 24 else compact


class GeneralConversationRepository:
    """Persistence boundary for ordinary chat sessions only."""

    def __init__(self, session: Session, *, owner_id: str | None = None):
        self.session = session
        self.owner_id = owner_id

    def scoped(self, owner_id: str) -> "GeneralConversationRepository":
        return GeneralConversationRepository(self.session, owner_id=owner_id)

    def create(self) -> GeneralConversationRow:
        row = GeneralConversationRow(
            id=str(uuid4()),
            user_id=self.owner_id,
            title=DEFAULT_GENERAL_CONVERSATION_TITLE,
            status="active",
            messages=[],
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get(self, conversation_id: str) -> GeneralConversationRow | None:
        statement = select(GeneralConversationRow).where(
            GeneralConversationRow.id == conversation_id
        )
        if self.owner_id is not None:
            statement = statement.where(GeneralConversationRow.user_id == self.owner_id)
        return self.session.scalar(statement)

    def list_recent(self, *, limit: int = 30) -> list[GeneralConversationRow]:
        statement = select(GeneralConversationRow)
        if self.owner_id is not None:
            statement = statement.where(GeneralConversationRow.user_id == self.owner_id)
        rows = list(self.session.scalars(statement.order_by(
            GeneralConversationRow.updated_at.desc(),
            GeneralConversationRow.created_at.desc(),
        )))
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

    def add_message(
        self,
        row: GeneralConversationRow,
        *,
        role: str,
        content: str,
        card: dict[str, object] | None = None,
    ) -> int:
        # Lock the single JSON row before replacing the message list so two
        # simultaneous ordinary replies cannot overwrite one another.
        locked = self.session.scalar(
            select(GeneralConversationRow)
            .where(GeneralConversationRow.id == row.id)
            .with_for_update()
        )
        if locked is None:
            return 0
        messages = list(locked.messages or [])
        message = {"role": role, "content": content}
        if card is not None:
            message["card"] = card
        messages.append(message)
        locked.messages = messages
        if role == "user" and locked.title == DEFAULT_GENERAL_CONVERSATION_TITLE:
            locked.title = _title_from_message(content)
        self.session.commit()
        return len(messages)

    def update_message_card(
        self,
        row: GeneralConversationRow,
        *,
        sequence_number: int,
        card: dict[str, object],
    ) -> bool:
        """Replace the tool card attached to one persisted assistant message."""

        if sequence_number < 1:
            return False
        locked = self.session.scalar(
            select(GeneralConversationRow)
            .where(GeneralConversationRow.id == row.id)
            .with_for_update()
        )
        if locked is None:
            return False
        messages = list(locked.messages or [])
        index = sequence_number - 1
        existing_card = messages[index].get("card") if index < len(messages) else None
        if index >= len(messages) or messages[index].get("role") != "assistant":
            return False
        if not isinstance(existing_card, dict):
            return False
        updated_card: dict[str, object] | None = None
        if existing_card.get("type") == "route":
            updated_card = card
        elif existing_card.get("type") == "collection":
            cards = existing_card.get("cards")
            new_route_card = card.get("route")
            if not isinstance(cards, list) or not isinstance(new_route_card, dict):
                return False
            candidates = [
                (position, item)
                for position, item in enumerate(cards)
                if isinstance(item, dict) and item.get("type") == "route"
            ]
            new_origin = new_route_card.get("origin_poi_id")
            new_destination = new_route_card.get("destination_poi_id")
            matched_position = next(
                (
                    position
                    for position, item in candidates
                    if isinstance(item.get("route"), dict)
                    and item["route"].get("origin_poi_id") == new_origin
                    and item["route"].get("destination_poi_id") == new_destination
                ),
                candidates[0][0] if len(candidates) == 1 else None,
            )
            if matched_position is None:
                return False
            updated_cards = list(cards)
            updated_cards[matched_position] = card
            updated_card = {**existing_card, "cards": updated_cards}
        else:
            return False
        updated_message = dict(messages[index])
        updated_message["card"] = updated_card
        if _ROUTE_SUMMARY_PATTERN.fullmatch(str(updated_message.get("content", ""))):
            updated_message["content"] = _route_card_summary(card)
        messages[index] = updated_message
        locked.messages = messages
        self.session.commit()
        return True

    def update_memory_summary(
        self,
        conversation_id: str,
        *,
        summary: str,
        summarized_through: int,
    ) -> tuple[str | None, int] | None:
        """Save monotonically advancing, conversation-local Agent memory."""

        statement = select(GeneralConversationRow).where(
            GeneralConversationRow.id == conversation_id
        )
        if self.owner_id is not None:
            statement = statement.where(GeneralConversationRow.user_id == self.owner_id)
        locked = self.session.scalar(statement.with_for_update())
        if locked is None:
            return None
        current_through = max(0, int(locked.memory_summary_through or 0))
        if summarized_through > current_through:
            locked.memory_summary = summary or None
            locked.memory_summary_through = summarized_through
            self.session.commit()
            return locked.memory_summary, summarized_through
        return locked.memory_summary, current_through

    @staticmethod
    def to_model(row: GeneralConversationRow) -> GeneralConversation:
        timestamp = row.updated_at or row.created_at
        messages = [
            ConversationMessage(
                id=index,
                sequence_number=index,
                role=item["role"],
                content=item["content"],
                card=item.get("card"),
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


def _route_card_summary(card: dict[str, object]) -> str:
    route = card.get("route")
    if not isinstance(route, dict):
        return "路线方式已更新，详细路线见路线卡片。"
    origin = str(route.get("origin_name") or "出发地")
    destination = str(route.get("destination_name") or "目的地")
    mode = str(route.get("mode") or "transit")
    mode_label = {"walking": "步行", "transit": "公共交通", "driving": "驾车"}.get(mode, "路线")
    source = route.get("source")
    distance = route.get("distance_m")
    duration = route.get("duration_min")
    if source == "unavailable" or distance is None or duration is None:
        return f"{origin}到{destination}：暂时没有查到{mode_label}路线，可换一种方式或打开高德导航。"
    try:
        distance_value = float(distance)
        distance_label = f"{round(distance_value)}米" if distance_value < 1000 else f"{distance_value / 1000:.1f}公里"
        duration_label = f"{int(duration)}分钟"
    except (TypeError, ValueError):
        return "路线方式已更新，详细路线见路线卡片。"
    return f"{origin}到{destination}：建议{mode_label}，距离约{distance_label}，预计{duration_label}。"


__all__ = [
    "DEFAULT_GENERAL_CONVERSATION_TITLE",
    "GeneralConversationRepository",
    "get_general_conversation_repository",
]
