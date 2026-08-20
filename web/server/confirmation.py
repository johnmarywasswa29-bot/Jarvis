"""Web UI confirmation gate for the research workflow (Phase 9H).

This module is the bridge between the synchronous ``UserDecider`` contract used
by ResearchWorkflow (9F) and the asynchronous WebSocket confirmation flow:

  * When the workflow reaches the confirmation gate, ``WebUserDecider.decide``
    registers a *pending confirmation* and asks the ConfirmationManager to push
    a structured proposal to the bound browser session, then BLOCKS until the
    user answers (or it expires / the socket drops).
  * The WebSocket layer calls ``ConfirmationManager.resolve(confirmation_id,
    client_id, decision)`` when the browser sends its choice.

Security properties (per Phase 9H requirements):
  * Session-bound: a confirmation can only be answered by the client_id it was
    created for. Another session's answer is rejected.
  * Expiry: every confirmation has a TTL; on expiry the decision is ABORT
    (fail-safe) and never ACCEPT.
  * Replay / duplicate: once resolved, the same confirmation_id is consumed; a
    second answer is rejected.
  * Disconnect: if the owning session disconnects while pending, it resolves as
    ABORT (nothing executes).
  * Never auto-confirms: the only ACCEPT path is an explicit client message.

No research/planning/execution logic lives here; this is purely the
confirmation coordination seam.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Callable, Optional

from research.orchestrator import Decision, UserDecider, summarize_proposal


DEFAULT_EXPIRY_S = 300.0  # 5 minutes


@dataclass
class PendingConfirmation:
    confirmation_id: str
    client_id: str
    summary: dict[str, Any]
    created_at: float
    expires_at: float
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[Decision] = None
    resolved: bool = False
    reason: str = ""


class ConfirmationManager:
    """Thread-safe registry of in-flight confirmation requests."""

    def __init__(self, expiry_s: float = DEFAULT_EXPIRY_S) -> None:
        self.expiry_s = expiry_s
        self._lock = threading.Lock()
        self._pending: dict[str, PendingConfirmation] = {}
        self._consumed: set[str] = set()  # confirmation_ids already resolved
        self._send: Optional[Callable[[str, dict], None]] = None

    # ----------------------------------------------------------------- setter
    def set_sender(self, cb: Callable[[str, dict], None]) -> None:
        """Register a callback used to push a message to a client.

        ``cb(client_id, message_dict)`` must schedule the send thread-safely
        (e.g. via asyncio.run_coroutine_threadsafe).
        """
        self._send = cb

    # -------------------------------------------------------------- lifecycle
    def create(self, client_id: str, summary: dict[str, Any]) -> PendingConfirmation:
        """Register a pending confirmation and ask the UI to display it."""
        now = time.time()
        cid = f"conf_{uuid.uuid4().hex}"
        pending = PendingConfirmation(
            confirmation_id=cid,
            client_id=client_id,
            summary=summary,
            created_at=now,
            expires_at=now + self.expiry_s,
        )
        with self._lock:
            self._pending[cid] = pending

        if self._send:
            payload = dict(summary)
            payload["confirmation_id"] = cid
            payload["expires_at"] = datetime.fromtimestamp(pending.expires_at, UTC).replace(tzinfo=None).isoformat()
            try:
                self._send(client_id, payload)
            except Exception as exc:  # pragma: no cover - defensive
                import logging
                logging.getLogger("web.confirmation").error("Failed to send proposal: %s", exc)
        return pending

    def await_result(self, confirmation_id: str, timeout: Optional[float] = None) -> Decision:
        """Block until the user answers, or resolve as ABORT on expiry/timeout.

        Runs inside the workflow worker thread (ResearchWorkflow.run is invoked
        via asyncio.to_thread). The browser answer arrives on the event loop and
        calls ``resolve``, releasing the Event.
        """
        pending = self._pending.get(confirmation_id)
        if pending is None:
            return Decision.ABORT  # unknown -> fail safe
        effective_timeout = timeout if timeout is not None else self.expiry_s
        answered = pending.event.wait(effective_timeout)
        if not answered:
            # Timed out -> mark expired and abort.
            with self._lock:
                if not pending.resolved:
                    pending.resolved = True
                    pending.result = Decision.ABORT
                    pending.reason = "expired"
                    self._consumed.add(confirmation_id)
                    self._pending.pop(confirmation_id, None)
            self._emit_expired(confirmation_id)
            return Decision.ABORT
        return pending.result or Decision.ABORT

    def resolve(self, confirmation_id: str, client_id: str, decision: Decision) -> bool:
        """Record a user's decision. Returns True if accepted, False otherwise.

        Rejects when: unknown id, already consumed, wrong session, or expired.
        """
        with self._lock:
            pending = self._pending.get(confirmation_id)
            if pending is None:
                return False  # unknown or already consumed (replay)
            if confirmation_id in self._consumed:
                return False  # replay / duplicate
            if pending.client_id != client_id:
                return False  # session mismatch -> reject (security)
            if time.time() > pending.expires_at:
                pending.resolved = True
                pending.result = Decision.ABORT
                pending.reason = "expired"
                self._consumed.add(confirmation_id)
                self._pending.pop(confirmation_id, None)
                return False  # expired

            pending.resolved = True
            pending.result = decision
            pending.reason = decision.value
            self._consumed.add(confirmation_id)
            self._pending.pop(confirmation_id, None)

        pending.event.set()
        return True

    def on_disconnect(self, client_id: str) -> None:
        """Abort any pending confirmations owned by a disconnected session."""
        with self._lock:
            owned = [p for p in self._pending.values() if p.client_id == client_id]
        for pending in owned:
            with self._lock:
                if not pending.resolved:
                    pending.resolved = True
                    pending.result = Decision.ABORT
                    pending.reason = "disconnected"
                    self._consumed.add(pending.confirmation_id)
                    self._pending.pop(pending.confirmation_id, None)
            pending.event.set()

    def pending_for(self, client_id: str) -> list[PendingConfirmation]:
        with self._lock:
            return [p for p in self._pending.values() if p.client_id == client_id]

    def _emit_expired(self, confirmation_id: str) -> None:
        if self._send:
            try:
                # Best-effort: the owning client_id is unknown here; skip send.
                pass
            except Exception:
                pass


class WebUserDecider(UserDecider):
    """UserDecider that gates execution on an explicit WebSocket confirmation."""

    def __init__(
        self,
        manager: ConfirmationManager,
        client_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        self.manager = manager
        self.client_id = client_id
        self.timeout = timeout

    def decide(self, objective: str, plan: Any, proposal: Any) -> Decision:
        # Build the structured proposal the browser will display.
        summary = summarize_proposal(plan, proposal)
        summary["objective"] = objective
        pending = self.manager.create(self.client_id, summary)
        # Wait using the manager's TTL (so an override in tests / config shortens
        # the wait); fall back to the decider's own timeout if set.
        wait = self.timeout if self.timeout is not None else self.manager.expiry_s
        return self.manager.await_result(pending.confirmation_id, timeout=wait)
