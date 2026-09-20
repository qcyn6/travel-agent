import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.conversation.general_repository import GeneralConversationRepository
from backend.conversation.general_service import GeneralConversationService
from backend.database.session import Base
from backend.database.tables import ConversationRow


@pytest.fixture
def database_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_general_messages_are_stored_in_their_own_table(database_session):
    repository = GeneralConversationRepository(database_session)
    row = repository.create()

    repository.add_message(row, role="user", content="介绍一下云南")
    repository.add_message(row, role="assistant", content="云南有高原风光、古城和多民族文化。")

    saved = repository.get(row.id)
    assert saved is not None
    model = repository.to_model(saved)
    assert model.title == "介绍一下云南"
    assert [(message.role, message.content) for message in model.messages] == [
        ("user", "介绍一下云南"),
        ("assistant", "云南有高原风光、古城和多民族文化。"),
    ]
    assert database_session.scalar(select(ConversationRow).where(ConversationRow.id == row.id)) is None
    assert [item.id for item in repository.list_recent()] == [row.id]


@pytest.mark.anyio
async def test_general_service_blocks_planning_without_creating_a_planning_requirement(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "北京玩3天，喜欢历史文化")

    assert prepared.boundary_reply == "可以，具体行程需要在规划模式完成。请先切换到规划模式，切换后我会从目的地开始收集信息。"
    assert prepared.conversation.messages[-1].content == "北京玩3天，喜欢历史文化"
    assert prepared.conversation.messages[-1].role == "user"
    service.finish_message(prepared, prepared.boundary_reply)
    assert database_session.query(ConversationRow).count() == 0


@pytest.mark.anyio
async def test_general_service_uses_a_mode_safe_reply_for_capability_question(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "你能做什么？")

    assert prepared.boundary_reply is None
    assert prepared.quick_reply is not None
    assert "普通模式" not in prepared.quick_reply
    assert "需要制定具体行程时，请切换到规划模式" in prepared.quick_reply
    assert "陪你聊目的地" in prepared.quick_reply


@pytest.mark.anyio
async def test_general_service_keeps_out_of_range_trip_length_inside_general_boundary(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "北京玩365天，喜欢历史文化")

    assert prepared.boundary_reply is not None
    assert "规划模式" in prepared.boundary_reply
