import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.conversation.general_repository import GeneralConversationRepository
from backend.conversation.general_service import GeneralConversationService
from backend.database.session import Base
from backend.database.tables import AttractionRow, CityRow, ConversationRow, ProvinceRow
from backend.destination import DestinationResolver
from backend.destination.repository import CatalogRepository
from backend.knowledge import KnowledgeRepository


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


@pytest.mark.anyio
async def test_general_service_executes_route_tool_without_calling_planning(database_session):
    class TravelTools:
        decisions = []

        async def execute(self, decision):
            self.decisions.append(decision)
            return "杭州东站到西湖：建议公共交通，预计32分钟。"

    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    tools = TravelTools()
    service = GeneralConversationService(repository=repository, travel_tools=tools)

    prepared = await service.prepare_message(conversation.id, "从杭州东站到西湖怎么走")

    assert prepared.intent == "route"
    assert prepared.tool_reply == "杭州东站到西湖：建议公共交通，预计32分钟。"
    assert prepared.boundary_reply is None
    assert tools.decisions[0].origin == "杭州东站"
    assert tools.decisions[0].destination == "西湖"
    assert database_session.query(ConversationRow).count() == 0


@pytest.mark.anyio
async def test_general_service_asks_only_for_missing_weather_city(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "明天天气怎么样")

    assert prepared.intent == "weather"
    assert "还需要查询天气的城市" in prepared.tool_reply
    assert prepared.suggestions == ["杭州", "北京", "成都"]


@pytest.mark.anyio
async def test_general_knowledge_context_includes_existing_attraction_rows(database_session):
    province = ProvinceRow(
        id=1,
        name="浙江省",
        adcode="330000",
        tags=["自然"],
        description="浙江山水与人文兼具。",
    )
    city = CityRow(
        id=1,
        province_id=1,
        name="杭州市",
        adcode="330100",
        tags=["自然"],
        description="杭州以西湖闻名。",
    )
    attraction = AttractionRow(
        poi_id="A1",
        city_id=1,
        name="西湖",
        category="自然风光",
        tags=["自然", "摄影"],
        description="西湖是杭州代表性的湖泊景观。",
        longitude=120.14,
        latitude=30.25,
        visit_duration_minutes=180,
    )
    database_session.add_all([province, city, attraction])
    database_session.commit()

    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    catalog = CatalogRepository(database_session)
    service = GeneralConversationService(
        repository=repository,
        resolver=DestinationResolver(catalog),
        knowledge_repository=KnowledgeRepository(database_session),
    )

    prepared = await service.prepare_message(conversation.id, "杭州有哪些自然景点")

    assert prepared.intent == "knowledge"
    assert "【景点资料：西湖（杭州市）】" in prepared.destination_context
    assert "西湖是杭州代表性的湖泊景观" in prepared.destination_context
