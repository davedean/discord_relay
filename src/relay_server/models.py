"""Database models for the relay queue."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    return str(uuid.uuid4())


class DiscordMessage(Base):
    __tablename__ = "discord_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    discord_bot_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    discord_message_id: Mapped[str] = mapped_column(String, nullable=False)
    author_id: Mapped[str] = mapped_column(String, nullable=False)
    author_name: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    guild_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_dm: Mapped[bool] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    deliveries: Mapped[list["Delivery"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class DeliveryState(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    discord_message_id: Mapped[str] = mapped_column(
        String, ForeignKey("discord_messages.id"), nullable=False
    )
    backend_bot_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    state: Mapped[DeliveryState] = mapped_column(
        Enum(DeliveryState), nullable=False, default=DeliveryState.PENDING
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    message: Mapped[DiscordMessage] = relationship(back_populates="deliveries")


Index(
    "idx_discord_messages_bot_message",
    DiscordMessage.discord_bot_id,
    DiscordMessage.discord_message_id,
    unique=True,
)


def create_session_factory(database_url: str) -> sessionmaker:
    """Create the SQLAlchemy session factory."""
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
