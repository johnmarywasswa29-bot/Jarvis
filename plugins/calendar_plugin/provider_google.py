"""Google Calendar provider with OAuth support."""
from __future__ import annotations

import logging
import os
from typing import Optional

from plugins.calendar_plugin.state import CalendarEvent
from plugins.calendar_plugin.secrets import create_store

logger = logging.getLogger(__name__)


class GoogleProvider:
    def __init__(self) -> None:
        self.creds = None
        self.service = None
        self._client_secrets_path = os.environ.get("GOOGLE_CLIENT_SECRETS", "")
        self._token_path = os.environ.get("GOOGLE_TOKEN_PATH", "")
        self._store = create_store(os.path.join(os.getcwd(), "data", "calendar"))

    def _migrate_legacy_token(self, token_path: str) -> None:
        if not token_path or not os.path.exists(token_path):
            return
        try:
            text = open(token_path, "r", encoding="utf-8").read()
            if text.strip():
                self._store.set_secret(token_path, text)
                os.remove(token_path)
        except Exception as exc:
            logger.debug("Google legacy token migration skipped: %s", exc)

    def _load_token(self, token_path: str):
        if not token_path:
            return None
        if os.path.exists(token_path):
            try:
                text = open(token_path, "r", encoding="utf-8").read()
                if text.strip():
                    self._migrate_legacy_token(token_path)
                    return None
            except Exception:
                return None
        value = self._store.get_secret(token_path)
        if value is not None:
            try:
                import json

                from google.oauth2.credentials import Credentials

                return Credentials.from_authorized_user_info(
                    json.loads(value),
                    ["https://www.googleapis.com/auth/calendar.readonly"],
                )
            except Exception:
                return None
        return None

    def _ensure_authed(self) -> bool:
        if self.service is not None:
            return True
        if not self._client_secrets_path or not os.path.exists(self._client_secrets_path):
            return False
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            creds = self._load_token(self._token_path)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self._client_secrets_path,
                        ["https://www.googleapis.com/auth/calendar.readonly"],
                    )
                    creds = flow.run_local_server(port=0)
                if self._token_path and self._store is not None:
                    try:
                        self._store.set_secret(self._token_path, creds.to_json())
                    except Exception as exc:
                        logger.debug("Google secure token store failed: %s", exc)
            self.creds = creds
            self.service = build("calendar", "v3", credentials=creds)
            return True
        except Exception as exc:
            logger.debug("Google auth failed: %s", exc)
            return False

    def get_events(self, start: str, end: str) -> list[CalendarEvent]:
        if not self._ensure_authed():
            return []
        try:
            items = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=f"{start}T00:00:00Z" if len(start) == 10 else start,
                    timeMax=f"{end}T23:59:59Z" if len(end) == 10 else end,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
                .get("items", [])
            )
            results: list[CalendarEvent] = []
            for item in items:
                start_info = item.get("start", {})
                end_info = item.get("end", {})
                event = CalendarEvent(
                    event_id=item.get("id", ""),
                    title=item.get("summary", ""),
                    start=start_info.get("dateTime") or start_info.get("date", ""),
                    end=end_info.get("dateTime") or end_info.get("date", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    provider="google",
                    status=item.get("status", "confirmed"),
                )
                attendees = item.get("attendees", [])
                event.attendees = [
                    a.get("email", "") for a in attendees if isinstance(a, dict)
                ]
                results.append(event)
            return results
        except Exception as exc:
            logger.debug("Google get_events failed: %s", exc)
            return []

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        if not self._ensure_authed():
            event.provider = "google"
            return event
        try:
            body = {
                "summary": event.title,
                "location": event.location,
                "description": event.description,
                "start": {"dateTime": event.start},
                "end": {"dateTime": event.end},
                "attendees": [{"email": a} for a in event.attendees],
                "status": event.status,
            }
            created = (
                self.service.events()
                .insert(calendarId="primary", body=body)
                .execute()
            )
            event.event_id = created.get("id", event.event_id)
            event.provider = "google"
        except Exception as exc:
            logger.debug("Google create_event failed: %s", exc)
        return event

    def edit_event(self, event_id: str, updates: dict) -> CalendarEvent:
        event = CalendarEvent(event_id=event_id, provider="google")
        if not self._ensure_authed():
            for key, value in updates.items():
                if hasattr(event, key):
                    setattr(event, key, value)
            return event
        try:
            body = {}
            mapping = {
                "title": "summary",
                "location": "location",
                "description": "description",
                "start": "start",
                "end": "end",
                "status": "status",
            }
            for key, value in updates.items():
                if key in mapping:
                    if key in {"start", "end"}:
                        body[mapping[key]] = {"dateTime": value}
                    else:
                        body[mapping[key]] = value
            updated = (
                self.service.events()
                .patch(calendarId="primary", eventId=event_id, body=body)
                .execute()
            )
            event.title = updated.get("summary", event.title)
            event.start = updated.get("start", {}).get("dateTime", event.start)
            event.end = updated.get("end", {}).get("dateTime", event.end)
            event.provider = "google"
        except Exception as exc:
            logger.debug("Google edit_event failed: %s", exc)
        return event

    def delete_event(self, event_id: str) -> bool:
        if not self._ensure_authed():
            return False
        try:
            self.service.events().delete(calendarId="primary", eventId=event_id).execute()
            return True
        except Exception as exc:
            logger.debug("Google delete_event failed: %s", exc)
            return False
