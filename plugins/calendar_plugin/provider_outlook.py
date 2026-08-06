"""Microsoft Outlook Calendar provider using MSAL."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from plugins.calendar_plugin.state import CalendarEvent

logger = logging.getLogger(__name__)


class OutlookProvider:
    def __init__(self) -> None:
        self.client_id = os.environ.get("OUTLOOK_CLIENT_ID", "")
        self.tenant_id = os.environ.get("OUTLOOK_TENANT_ID", "common")
        self.client_secret = os.environ.get("OUTLOOK_CLIENT_SECRET", "")
        self._token_path = os.environ.get("OUTLOOK_TOKEN_PATH", "")
        self._access_token = None
        self._expires_at = 0.0

    def _ensure_token(self) -> bool:
        import time

        if self._access_token and time.time() < self._expires_at - 60:
            return True
        if not self.client_id or not self.client_secret:
            return False
        try:
            import msal
            from urllib.request import Request, urlopen
            from urllib.error import URLError

            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                client_credential=self.client_secret,
            )
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            token = result.get("access_token")
            if not token:
                return False
            expires_in = int(result.get("expires_in", 3600))
            self._access_token = token
            self._expires_at = time.time() + max(expires_in, 0)
            if self._token_path:
                try:
                    open(self._token_path, "w", encoding="utf-8").write(token)
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.debug("Outlook auth failed: %s", exc)
            return False

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def get_events(self, start: str, end: str) -> list[CalendarEvent]:
        if not self._ensure_token():
            return []
        try:
            import urllib.parse
            import urllib.request
            import json

            start_param = urllib.parse.quote(f"{start}T00:00:00Z" if len(start) == 10 else start)
            end_param = urllib.parse.quote(f"{end}T23:59:59Z" if len(end) == 10 else end)
            url = (
                "https://graph.microsoft.com/v1.0/me/calendarView"
                f"?startDateTime={start_param}&endDateTime={end_param}"
                "&$top=50&$orderby=start/dateTime"
            )
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            results: list[CalendarEvent] = []
            for item in data.get("value", []):
                start_info = item.get("start", {})
                end_info = item.get("end", {})
                event = CalendarEvent(
                    event_id=item.get("id", ""),
                    title=item.get("subject", ""),
                    start=start_info.get("dateTime", ""),
                    end=end_info.get("dateTime", ""),
                    location=item.get("location", {}).get("displayName", "") if isinstance(item.get("location"), dict) else item.get("location", ""),
                    description=item.get("bodyPreview", ""),
                    provider="outlook",
                    status=item.get("responseStatus", {}).get("response", "confirmed") if isinstance(item.get("responseStatus"), dict) else "confirmed",
                )
                attendees = item.get("attendees", [])
                event.attendees = [
                    a.get("emailAddress", {}).get("address", "") if isinstance(a, dict) else ""
                    for a in attendees
                ]
                results.append(event)
            return results
        except Exception as exc:
            logger.debug("Outlook get_events failed: %s", exc)
            return []

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        event.provider = "outlook"
        if not self._ensure_token():
            return event
        try:
            import urllib.request
            import json

            body = {
                "subject": event.title,
                "body": {"contentType": "text", "content": event.description},
                "start": {"dateTime": event.start, "timeZone": "UTC"},
                "end": {"dateTime": event.end, "timeZone": "UTC"},
                "location": {"displayName": event.location},
                "attendees": [{"emailAddress": {"address": a, "name": a}} for a in event.attendees],
            }
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                "https://graph.microsoft.com/v1.0/me/events",
                data=data,
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                created = json.loads(response.read().decode("utf-8"))
            event.event_id = created.get("id", event.event_id)
            event.provider = "outlook"
        except Exception as exc:
            logger.debug("Outlook create_event failed: %s", exc)
        return event

    def edit_event(self, event_id: str, updates: dict) -> CalendarEvent:
        event = CalendarEvent(event_id=event_id, provider="outlook")
        if not self._ensure_token():
            for key, value in updates.items():
                if hasattr(event, key):
                    setattr(event, key, value)
            return event
        try:
            import urllib.request
            import json

            body: dict[str, object] = {}
            if "title" in updates:
                body["subject"] = updates["title"]
            if "location" in updates:
                body["location"] = {"displayName": updates["location"]}
            if "description" in updates:
                body["body"] = {"contentType": "text", "content": updates["description"]}
            if "start" in updates:
                body["start"] = {"dateTime": updates["start"], "timeZone": "UTC"}
            if "end" in updates:
                body["end"] = {"dateTime": updates["end"], "timeZone": "UTC"}
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                f"https://graph.microsoft.com/v1.0/me/events/{event_id}",
                data=data,
                method="PATCH",
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                updated = json.loads(response.read().decode("utf-8"))
            event.title = updated.get("subject", event.title)
            event.start = updated.get("start", {}).get("dateTime", event.start)
            event.end = updated.get("end", {}).get("dateTime", event.end)
            event.provider = "outlook"
        except Exception as exc:
            logger.debug("Outlook edit_event failed: %s", exc)
        return event

    def delete_event(self, event_id: str) -> bool:
        if not self._ensure_token():
            return False
        try:
            import urllib.request

            req = urllib.request.Request(
                f"https://graph.microsoft.com/v1.0/me/events/{event_id}",
                method="DELETE",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=20) as _response:
                pass
            return True
        except Exception as exc:
            logger.debug("Outlook delete_event failed: %s", exc)
            return False
