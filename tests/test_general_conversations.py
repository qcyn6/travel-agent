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
    repository.add_message(
        row,
        role="assistant",
        content="云南有高原风光、古城和多民族文化。",
        card={"type": "weather", "weather": []},
    )

    saved = repository.get(row.id)
    assert saved is not None
    model = repository.to_model(saved)
    assert model.title == "介绍一下云南"
    assert [(message.role, message.content) for message in model.messages] == [
        ("user", "介绍一下云南"),
        ("assistant", "云南有高原风光、古城和多民族文化。"),
    ]
    assert model.messages[-1].card == {"type": "weather", "weather": []}
    assert database_session.scalar(select(ConversationRow).where(ConversationRow.id == row.id)) is None
    assert [item.id for item in repository.list_recent()] == [row.id]


def test_general_route_card_survives_history_round_trip(database_session):
    repository = GeneralConversationRepository(database_session)
    row = repository.create()
    card = {
        "type": "route",
        "route": {
            "origin_name": "杭州东站",
            "destination_name": "西湖",
            "mode": "transit",
            "distance_m": 12000,
            "duration_min": 32,
        },
    }

    repository.add_message(row, role="user", content="杭州东站到西湖怎么走")
    repository.add_message(row, role="assistant", content="建议公共交通。", card=card)

    saved = repository.to_model(repository.get(row.id))
    assert saved.messages[-1].card == card


@pytest.mark.anyio
async def test_general_service_leaves_planning_decisions_to_the_agent(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "北京玩3天，喜欢历史文化")

    assert prepared.intent == "general"
    assert prepared.boundary_reply is None
    assert prepared.tool_reply is None
    assert prepared.conversation.messages[-1].content == "北京玩3天，喜欢历史文化"
    assert prepared.conversation.messages[-1].role == "user"
    assert database_session.query(ConversationRow).count() == 0


@pytest.mark.anyio
async def test_general_service_does_not_preempt_agent_for_capability_question(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "你能做什么？")

    assert prepared.boundary_reply is None
    assert prepared.quick_reply is None
    assert prepared.intent == "general"
    assert prepared.conversation.messages[-1].content == "你能做什么？"


@pytest.mark.anyio
async def test_general_service_leaves_trip_length_validation_to_planning_mode(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "北京玩365天，喜欢历史文化")

    assert prepared.intent == "general"
    assert prepared.boundary_reply is None
    assert prepared.conversation.messages[-1].content == "北京玩365天，喜欢历史文化"


@pytest.mark.anyio
async def test_general_service_keeps_destination_knowledge_question_out_of_planning(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "大理古城你知道吗")

    assert prepared.intent == "general"
    assert prepared.boundary_reply is None
    assert prepared.quick_reply is None


@pytest.mark.anyio
async def test_general_service_leaves_scope_decision_to_the_agent(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "介绍一下量子力学")

    assert prepared.intent == "general"
    assert prepared.boundary_reply is None
    assert prepared.conversation.messages[-1].content == "介绍一下量子力学"


@pytest.mark.anyio
async def test_general_service_leaves_route_tool_selection_to_the_agent(database_session):
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

    assert prepared.intent == "general"
    assert prepared.tool_reply is None
    assert prepared.boundary_reply is None
    assert tools.decisions == []
    assert database_session.query(ConversationRow).count() == 0


@pytest.mark.anyio
async def test_general_service_preserves_route_clarification_context_for_the_agent(database_session):
    class TravelTools:
        async def execute(self, decision):
            return f"{decision.origin}到{decision.destination}：建议公共交通。"

    repository = GeneralConversationRepository(database_session)
    row = repository.create()
    repository.add_message(row, role="user", content="长城到天安门怎么走")
    repository.add_message(
        row,
        role="assistant",
        content="长城有好几段，去天安门的路线差别很大。你指的是哪一段，以及想坐公交/地铁还是自驾？",
    )
    service = GeneralConversationService(
        repository=repository,
        travel_tools=TravelTools(),
    )

    prepared = await service.prepare_message(
        row.id,
        "- 八达岭长城（最靠东，京藏高速方向）",
    )

    assert prepared.intent == "general"
    assert prepared.tool_reply is None
    assert prepared.recent_messages[-2:] == [
        {"role": "user", "content": "长城到天安门怎么走"},
        {
            "role": "assistant",
            "content": "长城有好几段，去天安门的路线差别很大。你指的是哪一段，以及想坐公交/地铁还是自驾？",
        },
    ]


@pytest.mark.anyio
async def test_general_service_leaves_weather_clarification_to_the_agent(database_session):
    repository = GeneralConversationRepository(database_session)
    conversation = repository.to_model(repository.create())
    service = GeneralConversationService(repository=repository)

    prepared = await service.prepare_message(conversation.id, "明天天气怎么样")

    assert prepared.intent == "general"
    assert prepared.tool_reply is None
    assert prepared.suggestions == ["你能做什么？", "介绍一个目的地"]


@pytest.mark.anyio
async def test_destination_knowledge_is_left_for_the_agent_without_pre_retrieval(database_session):
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

    assert prepared.intent == "general"
    assert prepared.destination_context is None
    assert prepared.retrieval_sufficient is None
    assert prepared.retrieval_attempts == 0
