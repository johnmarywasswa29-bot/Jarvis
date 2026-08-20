"""WebSocket message schemas for Jarvis Web UI."""
from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class BaseMessage(BaseModel):
    """Base envelope for all WebSocket messages."""
    type: str
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    payload: Any = None


# Client → Server messages
class ChatRequest(BaseMessage):
    type: Literal["chat"] = "chat"
    payload: "ChatPayload"


class ChatPayload(BaseModel):
    content: str
    stream: bool = True
    provider: Optional[str] = None


class PingRequest(BaseMessage):
    type: Literal["ping"] = "ping"
    payload: Optional[dict] = None


class CancelRequest(BaseMessage):
    type: Literal["cancel"] = "cancel"
    payload: "CancelPayload"


class CancelPayload(BaseModel):
    request_id: str


# Server → Client messages
class ChatStarted(BaseMessage):
    type: Literal["chat_started"] = "chat_started"
    payload: "ChatStartedPayload"


class ChatStartedPayload(BaseModel):
    pass


class ChatToken(BaseMessage):
    type: Literal["chat_token"] = "chat_token"
    payload: "ChatTokenPayload"


class ChatTokenPayload(BaseModel):
    token: str
    done: bool = False


class ChatDone(BaseMessage):
    type: Literal["chat_done"] = "chat_done"
    payload: "ChatDonePayload"


class ChatDonePayload(BaseModel):
    content: str
    provider: Optional[str] = None


class PongResponse(BaseMessage):
    type: Literal["pong"] = "pong"
    payload: Optional[dict] = None


class ErrorResponse(BaseMessage):
    type: Literal["error"] = "error"
    payload: "ErrorPayload"


class ErrorPayload(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None


class CancelledResponse(BaseMessage):
    type: Literal["cancelled"] = "cancelled"
    payload: "CancelledPayload"


class CancelledPayload(BaseModel):
    request_id: str


# Event messages (bridged from EventBus)
class ToolStartedEvent(BaseMessage):
    type: Literal["tool_started"] = "tool_started"
    payload: "ToolStartedPayload"


class ToolStartedPayload(BaseModel):
    tool: str
    args: dict


class ToolCompletedEvent(BaseMessage):
    type: Literal["tool_completed"] = "tool_completed"
    payload: "ToolCompletedPayload"


class ToolCompletedPayload(BaseModel):
    tool: str
    result: str


class ToolFailedEvent(BaseMessage):
    type: Literal["tool_failed"] = "tool_failed"
    payload: "ToolFailedPayload"


class ToolFailedPayload(BaseModel):
    tool: str
    error: str


class AssistantResponseEvent(BaseMessage):
    type: Literal["assistant_response"] = "assistant_response"
    payload: "AssistantResponsePayload"


class AssistantResponsePayload(BaseModel):
    content: str


class WorkspaceChangedEvent(BaseMessage):
    type: Literal["workspace_changed"] = "workspace_changed"
    payload: "WorkspaceChangedPayload"


class WorkspaceChangedPayload(BaseModel):
    working_directory: str
    active_project: Optional[str] = None
    git_repository: Optional[str] = None


class ConfirmationRequiredEvent(BaseMessage):
    type: Literal["confirmation_required"] = "confirmation_required"
    payload: "ConfirmationRequiredPayload"


class ConfirmationRequiredPayload(BaseModel):
    request_id: str
    message: str
    type: str  # "danger" | "warning" | "info"
    # Phase 9H structured proposal fields
    confirmation_id: str = ""
    objective: str = ""
    overall_risk: str = ""
    requires_confirmation: bool = False
    sources: list = []
    steps: list = []
    expires_at: Optional[str] = None


class ConfirmationResponse(BaseMessage):
    """Client -> Server: the user's decision on a pending confirmation."""

    type: Literal["confirmation_response"] = "confirmation_response"
    payload: "ConfirmationResponsePayload"


class ConfirmationResponsePayload(BaseModel):
    confirmation_id: str
    decision: str  # "accept" | "deny" | "abort"


class ResearchProgressEvent(BaseMessage):
    """Server -> Client: research workflow progress / phase updates (9H)."""

    type: Literal["research_progress"] = "research_progress"
    payload: "ResearchProgressPayload"


class ResearchProgressPayload(BaseModel):
    request_id: str = ""
    phase: str = ""  # research | plan | proposal | confirmation | execution | complete | error
    message: str = ""
    sources: list = []
    steps: list = []
    status: str = ""  # success | failed | denied | aborted | partial
    detail: Optional[dict] = None


class ConfirmationResultEvent(BaseMessage):
    type: Literal["confirmation_result"] = "confirmation_result"
    payload: "ConfirmationResultPayload"


class ConfirmationResultPayload(BaseModel):
    request_id: str
    approved: bool


class StatusEvent(BaseMessage):
    type: Literal["status"] = "status"
    payload: "StatusPayload"


class StatusPayload(BaseModel):
    ollama: Optional[str] = None
    nvidia: Optional[str] = None
    voice: Optional[str] = None


ServerMessage = (
    ChatStarted | ChatToken | ChatDone | PongResponse | ErrorResponse | CancelledResponse |
    ToolStartedEvent, ToolCompletedEvent, ToolFailedEvent,
    AssistantResponseEvent, WorkspaceChangedEvent,
    ConfirmationRequiredEvent, ConfirmationResultEvent, StatusEvent,
    ResearchProgressEvent,
)


# Message type constants
class MessageTypes:
    # Client → Server
    CHAT = "chat"
    PING = "ping"
    CANCEL = "cancel"
    CONFIRMATION_RESPONSE = "confirmation_response"
    
    # Server → Client
    CHAT_STARTED = "chat_started"
    CHAT_TOKEN = "chat_token"
    CHAT_DONE = "chat_done"
    PONG = "pong"
    ERROR = "error"
    CANCELLED = "cancelled"
    
    # Events (bridged from EventBus)
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    ASSISTANT_RESPONSE = "assistant_response"
    WORKSPACE_CHANGED = "workspace_changed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_RESULT = "confirmation_result"
    RESEARCH_PROGRESS = "research_progress"
    STATUS = "status"


# Error codes
class ErrorCodes:
    INVALID_JSON = "INVALID_JSON"
    UNKNOWN_MESSAGE_TYPE = "UNKNOWN_MESSAGE_TYPE"
    EMPTY_MESSAGE = "EMPTY_MESSAGE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    CANCELLATION_NOT_SUPPORTED = "CANCELLATION_NOT_SUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CONFIRMATION_UNKNOWN = "CONFIRMATION_UNKNOWN"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_INVALID = "CONFIRMATION_INVALID"
    CONFIRMATION_REPLAY = "CONFIRMATION_REPLAY"
    CONFIRMATION_SESSION_MISMATCH = "CONFIRMATION_SESSION_MISMATCH"
    UNAUTHENTICATED = "UNAUTHENTICATED"


ClientMessage = ChatRequest | PingRequest | CancelRequest | ConfirmationResponse