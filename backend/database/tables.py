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
    suitable_days_min: Mapped[int] = mapped_column(Integer)
    suitable_days_max: Mapped[int] = mapped_column(Integer)

    province: Mapped[ProvinceRow] = relationship(back_populates="cities")
    attractions: Mapped[list["AttractionRow"]] = relationship(back_populates="city")


class AttractionRow(Base):
    __tablename__ = "attractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poi_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="amap")
    knowledge_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", index=True
    )
    knowledge_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    city: Mapped[CityRow] = relationship(back_populates="attractions")
    knowledge: Mapped["AttractionKnowledgeRow | None"] = relationship(
        back_populates="attraction",
        uselist=False,
        cascade="all, delete-orphan",
    )
    facts: Mapped["AttractionFactsRow | None"] = relationship(
        back_populates="attraction",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AttractionKnowledgeRow(Base):
    __tablename__ = "attraction_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("attractions.poi_id"), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    attraction: Mapped[AttractionRow] = relationship(back_populates="knowledge")


class AttractionFactsRow(Base):
    __tablename__ = "attraction_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poi_id: Mapped[str] = mapped_column(ForeignKey("attractions.poi_id"), unique=True, index=True)
    environment: Mapped[str] = mapped_column(String(16), default="unknown")
    estimated_visit_minutes: Mapped[int] = mapped_column(Integer)
    duration_source: Mapped[str] = mapped_column(String(32))
    opening_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="unknown")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    attraction: Mapped[AttractionRow] = relationship(back_populates="facts")


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
