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


class PendingMessage(BaseModel):
    delivery_id: str
    discord_bot_id: str
    discord_message: DiscordMessagePayload


class PendingMessagesResponse(BaseModel):
    messages: list[PendingMessage]


class Destination(BaseModel):
    type: Literal["dm", "channel"]
    user_id: Optional[str] = Field(default=None, description="Required when type=dm")
    channel_id: Optional[str] = Field(default=None, description="Required when type=channel")

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
