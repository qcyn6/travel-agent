from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.session import Base


class ProvinceRow(Base):
    __tablename__ = "provinces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    adcode: Mapped[str] = mapped_column(String(6), unique=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text)

    cities: Mapped[list["CityRow"]] = relationship(back_populates="province")


class CityRow(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    province_id: Mapped[int] = mapped_column(ForeignKey("provinces.id"), index=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    adcode: Mapped[str] = mapped_column(String(6), unique=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text)

    province: Mapped[ProvinceRow] = relationship(back_populates="cities")
    attractions: Mapped[list["AttractionRow"]] = relationship(back_populates="city")


class AttractionRow(Base):
    __tablename__ = "attractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poi_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    visit_duration_minutes: Mapped[int] = mapped_column(
        Integer,
        default=90,
        server_default="90",
        nullable=False,
    )
    # Keep the attraction table intentionally small and planning-focused.
    # Do not add provenance, quality, lifecycle, or parent/child columns here
    # without an explicit product decision and a corresponding schema change.

    city: Mapped[CityRow] = relationship(back_populates="attractions")


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(64), default="用户")
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def username(self) -> str:
        """User-facing username kept in the legacy email column."""

        return self.email


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(128), default="新的旅行计划")
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    requirement_draft: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list["ConversationMessageRow"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessageRow.sequence_number",
    )
    plans: Mapped[list["TravelPlanRow"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="TravelPlanRow.version",
    )
    planning_jobs: Mapped[list["PlanningJobRow"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="PlanningJobRow.created_at",
    )


class GeneralConversationRow(Base):
    """Standalone ordinary-chat session; it never carries planning fields."""

    __tablename__ = "general_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(128), default="新的对话")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    # Keep ordinary messages in this table so they cannot enter the planning
    # conversation/message tables. The JSON shape is {role, content, card?}.
    messages: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    # Conversation-scoped long-term context for ordinary chat only. The raw
    # messages remain intact; this summary is internal Agent context.
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_summary_through: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_number", name="uq_conversation_message_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped[ConversationRow] = relationship(back_populates="messages")


class PlanningJobRow(Base):
    """Durable execution state for one conversation plan generation."""

    __tablename__ = "planning_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        default="queued",
        server_default="queued",
        index=True,
    )
    requirement_json: Mapped[dict] = mapped_column(JSON)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversation: Mapped[ConversationRow] = relationship(back_populates="planning_jobs")
    events: Mapped[list["PlanningEventRow"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="PlanningEventRow.sequence_number",
    )


class PlanningEventRow(Base):
    """Append-only progress/event history used for SSE replay."""

    __tablename__ = "planning_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence_number", name="uq_planning_event_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("planning_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job: Mapped[PlanningJobRow] = relationship(back_populates="events")


class TravelPlanRow(Base):
    __tablename__ = "travel_plans"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_travel_plan_session_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    requirement_json: Mapped[dict] = mapped_column(JSON)
    plan_json: Mapped[dict] = mapped_column(JSON)
    evaluation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped[ConversationRow] = relationship(back_populates="plans")
