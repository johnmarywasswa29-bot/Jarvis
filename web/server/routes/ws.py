"""WebSocket routes for Jarvis Web UI."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import ValidationError

from modules.config import JarvisConfig
from web.schemas.messages import (
    BaseMessage, ChatRequest, PingRequest, CancelRequest,
    ChatStarted, ChatToken, ChatDone, PongResponse, ErrorResponse,
    CancelledResponse,
    MessageTypes, ErrorCodes, ErrorPayload,
    ChatStartedPayload, ChatTokenPayload, ChatDonePayload,
    ToolStartedEvent, ToolCompletedEvent, ToolFailedEvent,
    AssistantResponseEvent, WorkspaceChangedEvent,
    ConfirmationRequiredEvent, ConfirmationResultEvent, StatusEvent,
    ToolStartedPayload, ToolCompletedPayload, ToolFailedPayload,
    AssistantResponsePayload, WorkspaceChangedPayload,
    ConfirmationRequiredPayload, ConfirmationResultPayload, StatusPayload,
)

from web.server.auth import require_ws_auth
from web.server.confirmation import (
    ConfirmationManager,
    WebUserDecider,
    DEFAULT_EXPIRY_S,
)

logger = logging.getLogger("web.server.ws")

# Module-level confirmation gate shared across all WebSocket connections.
confirmation_manager = ConfirmationManager(expiry_s=DEFAULT_EXPIRY_S)


def _confirmation_event(payload: dict) -> "BaseMessage":
    """Wrap a proposal payload into a ConfirmationRequiredEvent."""
    from web.schemas.messages import ConfirmationRequiredEvent
    req_id = payload.get("request_id", "")
    return ConfirmationRequiredEvent(
        request_id=req_id,
        payload={
            "request_id": req_id,
            "message": payload.get("objective", "Proposed plan requires your approval"),
            "type": "warning",
            "confirmation_id": payload.get("confirmation_id", ""),
            "objective": payload.get("objective", ""),
            "overall_risk": payload.get("overall_risk", ""),
            "requires_confirmation": payload.get("requires_confirmation", False),
            "sources": payload.get("sources", []),
            "steps": payload.get("steps", []),
            "expires_at": payload.get("expires_at"),
        },
    )

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    """Manages active WebSocket connections."""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_requests: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        async with self._lock:
            self.active_connections[client_id] = websocket
        logger.info("Client connected: %s", client_id)
    
    async def disconnect(self, client_id: str):
        async with self._lock:
            self.active_connections.pop(client_id, None)
            self.connection_requests.pop(client_id, None)
        logger.info("Client disconnected: %s", client_id)
    
    async def send(self, client_id: str, message: BaseMessage):
        websocket = self.active_connections.get(client_id)
        if websocket:
            try:
                await websocket.send_text(message.model_dump_json())
            except Exception as exc:
                logger.error("Failed to send to %s: %s", client_id, exc)
    
    async def broadcast(self, message: BaseMessage, exclude: Optional[str] = None):
        """Broadcast message to all connected clients except excluded."""
        async with self._lock:
            for client_id, ws in self.active_connections.items():
                if client_id != exclude:
                    try:
                        await ws.send_text(message.model_dump_json())
                    except Exception as exc:
                        logger.error("Failed to broadcast to %s: %s", client_id, exc)


manager = ConnectionManager()


def create_error(code: str, message: str, request_id: Optional[str] = None) -> ErrorResponse:
    """Create an error response message."""
    return ErrorResponse(
        request_id=request_id or str(uuid.uuid4()),
        payload=ErrorPayload(code=code, message=message, request_id=request_id)
    )


async def handle_ping(websocket: WebSocket, client_id: str, request_id: str):
    """Handle ping message."""
    pong = PongResponse(request_id=request_id)
    await manager.send(client_id, pong)


async def handle_cancel(websocket: WebSocket, client_id: str, request_id: str, cancel_request_id: str):
    """Handle cancellation request."""
    # For Phase 3, we attempt cancellation if there's an active request
    async with manager._lock:
        req_info = manager.connection_requests.get(client_id, {})
        if req_info.get("request_id") == cancel_request_id:
            req_info["cancelled"] = True
            cancelled = True
        else:
            cancelled = False
    
    cancelled_resp = CancelledResponse(
        request_id=request_id,
        payload={"request_id": cancel_request_id}
    )
    await manager.send(client_id, cancelled_resp)
    
    if not cancelled:
        error = create_error(
            ErrorCodes.CANCELLATION_NOT_SUPPORTED,
            "Cancellation not supported for this request or no active generation",
            cancel_request_id
        )
        await manager.send(client_id, error)


async def handle_confirmation_response(websocket: WebSocket, client_id: str, request_id: str,
                                      confirmation_id: str, decision: str):
    """Handle the browser's decision on a pending confirmation (Phase 9H)."""
    mapping = {"accept": "accept", "deny": "deny", "abort": "abort"}
    norm = mapping.get((decision or "").lower())
    if norm is None:
        error = create_error(ErrorCodes.CONFIRMATION_INVALID,
                             f"Invalid decision: {decision!r}", request_id)
        await manager.send(client_id, error)
        return

    from research.orchestrator import Decision
    ok = confirmation_manager.resolve(confirmation_id, client_id, Decision(norm))
    if not ok:
        # Distinguish rejection reasons for a clear security signal.
        pending = confirmation_manager._pending.get(confirmation_id)  # noqa: SLF001
        if pending is None and confirmation_id in confirmation_manager._consumed:  # noqa: SLF001
            code = ErrorCodes.CONFIRMATION_REPLAY
            msg = "Confirmation already answered (replay/duplicate rejected)"
        elif pending is not None and pending.client_id != client_id:
            code = ErrorCodes.CONFIRMATION_SESSION_MISMATCH
            msg = "Confirmation does not belong to this session"
        else:
            code = ErrorCodes.CONFIRMATION_UNKNOWN
            msg = "Unknown or expired confirmation"
        error = create_error(code, msg, request_id)
        await manager.send(client_id, error)


def _send_research_progress(client_id: str, request_id: str, **fields):
    """Send a research_progress event to the browser (thread-safe via sender)."""
    from web.schemas.messages import ResearchProgressEvent
    payload = {"request_id": request_id}
    payload.update(fields)
    try:
        # Schedule on the event loop so it is safe from the worker thread.
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            manager.send(client_id, ResearchProgressEvent(request_id=request_id, payload=payload)),
            loop,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("research_progress send failed: %s", exc)


async def handle_research_chat(websocket: WebSocket, client_id: str, request_id: str, content: str):
    """Run a research/plan/execute request through the 9A-9G backend (Phase 9H).

    Sends research_progress events (sources, plan, confirmation, per-step
    execution) and a final chat_done with the rendered result. The interactive
    confirmation is gated by WebUserDecider via the shared confirmation_manager.
    """
    runtime = getattr(websocket.app.state, "runtime", None)
    config = websocket.app.state.config

    # Build the brain with a session-bound WebUserDecider for confirmation.
    try:
        from modules.brain import JarvisBrain
        from modules.tools import ToolRegistry
        from modules.memory import JarvisMemory

        tool_registry = runtime.tool_registry if runtime and hasattr(runtime, "tool_registry") else None
        memory = runtime.chat_memory if runtime and hasattr(runtime, "chat_memory") and runtime.chat_memory else None
        if memory is None:
            from modules.memory_v2 import JarvisMemoryV2
            memory = JarvisMemoryV2(config, use_chroma=False)

        decider = WebUserDecider(confirmation_manager, client_id)
        brain = JarvisBrain(config=config, tools=tool_registry, memory=memory, research_decider=decider)

        _send_research_progress(client_id, request_id, phase="research", message="Researching…")
        # Run the (potentially blocking) workflow in a worker thread so the
        # event loop keeps receiving the user's confirmation response.
        result = await asyncio.to_thread(brain.run, content)
        _send_research_progress(client_id, request_id, phase="complete",
                                message=result, status="complete")
        done = ChatDone(request_id=request_id, payload={"content": result or "", "provider": config.llm_provider})
        await manager.send(client_id, done)
    except Exception as exc:
        logger.error("Research chat failed: %s", exc)
        _send_research_progress(client_id, request_id, phase="error", message=str(exc), status="failed")
        error = create_error(ErrorCodes.RUNTIME_ERROR, f"Research failed: {exc}", request_id)
        await manager.send(client_id, error)


async def handle_chat(websocket: WebSocket, client_id: str, request_id: str, 
                      content: str, stream: bool, provider: Optional[str]):
    """Handle chat request and stream response using native async brain."""
    # Get runtime context from app state
    runtime = getattr(websocket.app.state, "runtime", None)
    config = websocket.app.state.config
    
    if runtime is None:
        error = create_error(ErrorCodes.PROVIDER_UNAVAILABLE, "Runtime not initialized", request_id)
        await manager.send(client_id, error)
        return
    
    # Use specified provider or current config provider
    if provider:
        original_provider = config.llm_provider
        config.llm_provider = provider
        restore_provider = True
    else:
        restore_provider = False
    
    try:
        from modules.llm_providers import get_llm_provider
        from modules.tools import ToolRegistry
        from modules.memory import JarvisMemory
        
        # Check provider availability
        llm_provider = get_llm_provider(config)
        if not llm_provider.is_available():
            error = create_error(ErrorCodes.PROVIDER_UNAVAILABLE, 
                               f"Provider {config.llm_provider} is not available", request_id)
            await manager.send(client_id, error)
            return
        
        # Get memory from runtime or create a lightweight one
        memory = runtime.chat_memory if hasattr(runtime, 'chat_memory') and runtime.chat_memory else None
        if memory is None:
            from modules.memory_v2 import JarvisMemoryV2
            memory = JarvisMemoryV2(config, use_chroma=False)
        
        tool_registry = runtime.tool_registry if hasattr(runtime, 'tool_registry') and runtime.tool_registry else None
        
        # Import JarvisBrain
        from modules.brain import JarvisBrain
        
        # Create brain instance
        brain = JarvisBrain(config=config, tools=tool_registry, memory=memory)
        
        # Send chat_started event
        started = ChatStarted(request_id=request_id)
        await manager.send(client_id, started)
        
        # Track active generation for potential cancellation
        async with manager._lock:
            manager.connection_requests[client_id] = {
                "request_id": request_id,
                "brain": brain,
                "cancelled": False
            }
        
        if stream:
            # Streaming response using native async brain
            messages = brain._build_messages(content)
            
            try:
                async for token in brain.run_stream_async(content, on_chunk=None, extra_context=None):
                    # Check for cancellation
                    async with manager._lock:
                        if manager.connection_requests.get(client_id, {}).get("cancelled", False):
                            break
                    
                    # Only send normal content, not reasoning
                    if token:
                        chat_token = ChatToken(
                            request_id=request_id,
                            payload={"token": token, "done": False}
                        )
                        await manager.send(client_id, chat_token)
                
                # Send completion
                done = ChatDone(
                    request_id=request_id,
                    payload={"content": "", "provider": config.llm_provider}
                )
                await manager.send(client_id, done)
                
            except Exception as exc:
                logger.error("Streaming error: %s", exc)
                error = create_error(ErrorCodes.RUNTIME_ERROR, f"Streaming failed: {exc}", request_id)
                await manager.send(client_id, error)
        else:
            # Non-streaming response
            try:
                from modules.llm_providers import get_llm_provider
                llm_provider = get_llm_provider(config)
                messages = brain._build_messages(content)
                response = llm_provider.chat(messages, stream=False)
                done = ChatDone(
                    request_id=request_id,
                    payload={"content": response or "", "provider": config.llm_provider}
                )
                await manager.send(client_id, done)
            except Exception as exc:
                logger.error("Chat error: %s", exc)
                error = create_error(ErrorCodes.RUNTIME_ERROR, f"Chat failed: {exc}", request_id)
                await manager.send(client_id, error)
    
    except Exception as exc:
        logger.error("Chat handling error: %s", exc)
        error = create_error(ErrorCodes.INTERNAL_ERROR, f"Internal error: {exc}", request_id)
        await manager.send(client_id, error)
    
    finally:
        # Restore original provider
        if restore_provider:
            config.llm_provider = original_provider
        
        # Clean up
        async with manager._lock:
            manager.connection_requests.pop(client_id, None)


# Event bridge: forward allowed EventBus events to WebSocket clients
async def event_bridge_handler(event):
    """Bridge EventBus events to WebSocket clients."""
    from core.events import EventType
    
    # Allowlist of events safe for browser
    allowed_events = {
        EventType.TOOL_STARTED: ("tool_started", lambda e: ToolStartedEvent(
            payload={"tool": e.payload.get("tool", ""), "args": e.payload.get("args", {})}
        )),
        EventType.TOOL_COMPLETED: ("tool_completed", lambda e: ToolCompletedEvent(
            payload={"tool": e.payload.get("tool", ""), "result": str(e.payload.get("result", ""))}
        )),
        EventType.TOOL_FAILED: ("tool_failed", lambda e: ToolFailedEvent(
            payload={"tool": e.payload.get("tool", ""), "error": str(e.payload.get("error", ""))}
        )),
        EventType.ASSISTANT_RESPONSE: ("assistant_response", lambda e: AssistantResponseEvent(
            payload={"content": str(e.payload.get("content", ""))}
        )),
        EventType.WORKSPACE_CHANGED: ("workspace_changed", lambda e: WorkspaceChangedEvent(
            payload={"working_directory": e.payload.get("working_directory", ""),
                     "active_project": e.payload.get("active_project"),
                     "git_repository": e.payload.get("git_repository")}
        )),
        EventType.CONFIRMATION_REQUIRED: ("confirmation_required", lambda e: ConfirmationRequiredEvent(
            payload={"request_id": e.payload.get("request_id", ""),
                     "message": e.payload.get("message", ""),
                     "type": e.payload.get("type", "info")}
        )),
        EventType.CONFIRMATION_APPROVED: ("confirmation_result", lambda e: ConfirmationResultEvent(
            payload={"request_id": e.payload.get("request_id", ""), "approved": True}
        )),
        EventType.CONFIRMATION_REJECTED: ("confirmation_result", lambda e: ConfirmationResultEvent(
            payload={"request_id": e.payload.get("request_id", ""), "approved": False}
        )),
    }
    
    if event.event_type in allowed_events:
        msg_type, factory = allowed_events[event.event_type]
        try:
            event_msg = factory(event)
            event_msg.request_id = str(uuid.uuid4())
            await manager.broadcast(event_msg)
        except Exception as exc:
            logger.error("Failed to bridge event %s: %s", event.event_type, exc)


# Setup event bridge when runtime is available
def setup_event_bridge(runtime_ctx):
    """Subscribe to EventBus events and forward to WebSocket clients."""
    if runtime_ctx and runtime_ctx.event_bus:
        from core.events import EventType
        for event_type in [
            EventType.TOOL_STARTED,
            EventType.TOOL_COMPLETED,
            EventType.TOOL_FAILED,
            EventType.ASSISTANT_RESPONSE,
            EventType.WORKSPACE_CHANGED,
            EventType.CONFIRMATION_REQUIRED,
            EventType.CONFIRMATION_APPROVED,
            EventType.CONFIRMATION_REJECTED,
        ]:
            try:
                runtime_ctx.event_bus.subscribe(event_type, event_bridge_handler)
            except Exception as exc:
                logger.error("Failed to subscribe to %s: %s", event_type, exc)


@router.websocket("/")
async def websocket_endpoint(
    websocket: WebSocket,
    _auth = Depends(require_ws_auth)
):
    """WebSocket endpoint for real-time chat with Jarvis."""
    # Get runtime and config from app state
    runtime = getattr(websocket.app.state, "runtime", None)
    config = websocket.app.state.config
    
    client_id = str(uuid.uuid4())
    
    await manager.connect(websocket, client_id)

    # Wire the confirmation manager's sender to push proposals to THIS client
    # from the event loop (so it is thread-safe even when the workflow runs in
    # a worker thread).
    loop = asyncio.get_event_loop()
    def _sender(cid, payload):
        try:
            asyncio.run_coroutine_threadsafe(manager.send(cid, _confirmation_event(payload)), loop)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("confirmation send scheduling failed: %s", exc)
    confirmation_manager.set_sender(_sender)
    
    # Send initial status
    from web.schemas.messages import StatusEvent, StatusPayload
    status = StatusEvent(
        payload={"ollama": "unknown", "nvidia": "unknown", "voice": "idle"}
    )
    await manager.send(client_id, status)
    
    try:
        while True:
            try:
                # Receive message
                data = await websocket.receive_text()
                
                # Parse and validate
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError:
                    error = create_error(ErrorCodes.INVALID_JSON, "Invalid JSON format")
                    await manager.send(client_id, error)
                    continue
                
                # Validate message type
                msg_type = message_data.get("type")
                request_id = message_data.get("request_id", str(uuid.uuid4()))
                
                if msg_type == MessageTypes.PING:
                    await handle_ping(websocket, client_id, request_id)
                
                elif msg_type == MessageTypes.CHAT:
                    payload = message_data.get("payload", {})
                    content = payload.get("content", "").strip()
                    stream = payload.get("stream", True)
                    provider = payload.get("provider")
                    
                    if not content:
                        error = create_error(ErrorCodes.EMPTY_MESSAGE, "Message content cannot be empty", request_id)
                        await manager.send(client_id, error)
                        continue
                    
                    # Phase 9H: route research/plan/execute requests through the
                    # backend; ordinary chat keeps the existing path unchanged.
                    # Run the research handler as a background task so the
                    # receive loop stays free to process the user's confirmation
                    # response (otherwise it would deadlock while the workflow
                    # blocks waiting for the decision).
                    from research.bridge import ResearchBridge, ResearchIntent
                    if ResearchBridge.classify(content) != ResearchIntent.NONE:
                        asyncio.create_task(
                            handle_research_chat(websocket, client_id, request_id, content)
                        )
                    else:
                        await handle_chat(websocket, client_id, request_id, content, stream, provider)
                
                elif msg_type == MessageTypes.CONFIRMATION_RESPONSE:
                    payload = message_data.get("payload", {})
                    cid = payload.get("confirmation_id", "")
                    decision = payload.get("decision", "")
                    await handle_confirmation_response(websocket, client_id, request_id, cid, decision)
                
                elif msg_type == MessageTypes.CANCEL:
                    payload = message_data.get("payload", {})
                    cancel_request_id = payload.get("request_id")
                    if not cancel_request_id:
                        error = create_error(ErrorCodes.INVALID_JSON, "Cancel request requires request_id", request_id)
                        await manager.send(client_id, error)
                        continue
                    await handle_cancel(websocket, client_id, request_id, cancel_request_id)
                
                elif msg_type == "stop_speaking":
                    # Handle stop speaking request
                    await handle_stop_speaking(client_id)
                
                else:
                    error = create_error(ErrorCodes.UNKNOWN_MESSAGE_TYPE, f"Unknown message type: {msg_type}", request_id)
                    await manager.send(client_id, error)
            
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.error("WebSocket error for %s: %s", client_id, exc)
                error = create_error(ErrorCodes.INTERNAL_ERROR, str(exc))
                await manager.send(client_id, error)
    
    finally:
        # Abort any pending confirmations owned by this session (fail safe:
        # nothing executes if the user disconnects during confirmation).
        try:
            confirmation_manager.on_disconnect(client_id)
        except Exception:
            pass
        await manager.disconnect(client_id)


async def handle_stop_speaking(client_id: str):
    """Handle stop speaking request."""
    # Send tts_stopped event to client to stop current audio
    from web.schemas.messages import BaseMessage, MessageTypes
    # We'll send a custom event - the client handles this
    # For now, we just acknowledge
    pass


# Export for lifespan setup
__all__ = ["router", "manager", "setup_event_bridge"]