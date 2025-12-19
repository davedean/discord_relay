"""Pydantic schemas for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class MessageSource(BaseModel):
    is_dm: bool
    guild_id: Optional[str]
    channel_id: Optional[str]
    author_id: str
    author_name: str


class DiscordMessagePayload(BaseModel):
    discord_message_id: str
    discord_bot_id: str
    timestamp: datetime
    content: str
    source: MessageSource


class Destination(BaseModel):
    type: Literal["dm", "channel"]
    user_id: Optional[str] = Field(default=None, description="Required when type=dm")
    channel_id: Optional[str] = Field(
        default=None, description="Required when type=channel"
    )

    @model_validator(mode="after")
    def _validate_destination(self) -> "Destination":
        if self.type == "dm" and not self.user_id:
            raise ValueError("user_id is required for dm destinations")
        if self.type == "channel" and not self.channel_id:
            raise ValueError("channel_id is required for channel destinations")
        return self


class SendMessageRequest(BaseModel):
    discord_bot_id: str
    destination: Destination
    content: str = Field(..., min_length=1)
    reply_to_discord_message_id: Optional[str] = None


class SendMessageResponse(BaseModel):
    discord_message_id: str
    channel_id: Optional[str]


class LeasedMessage(BaseModel):
    delivery_id: str
    lease_id: str
    discord_bot_id: str
    discord_message: DiscordMessagePayload
    lease_expires_at: datetime


class LeaseMessagesResponse(BaseModel):
    messages: list[LeasedMessage]
    conversation_history: Optional[list[DiscordMessagePayload]] = None


class LeaseMessagesRequest(BaseModel):
    limit: int = Field(default=50, gt=0, le=100)
    lease_seconds: int = Field(
        default=300, gt=0, le=3600
    )  # 5 minutes default, max 1 hour
    include_conversation_history: bool = Field(default=True)
    conversation_history_limit: int = Field(default=20, gt=0, le=100)


class AckRequest(BaseModel):
    delivery_ids: list[str] = Field(..., min_length=1)
    lease_id: str


class NackRequest(BaseModel):
    delivery_ids: list[str] = Field(..., min_length=1)
    lease_id: str
    reason: Optional[str] = None
