"""PatternMiner: discovers frequent app/intent/search sequences from event history."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional


class PatternMiner:
    def __init__(self, max_sequence_len: int = 4, min_support: int = 2) -> None:
        self.max_sequence_len = max_sequence_len
        self.min_support = min_support

    def analyze(self, events: list[dict[str, Any]], now: Optional[datetime] = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        app_events = [e for e in events if e.get("kind") == "app_launch"]
        intent_events = [e for e in events if e.get("kind") == "intent"]
        search_events = [e for e in events if e.get("kind") == "search"]
        file_events = [e for e in events if e.get("kind") == "file_open"]

        app_sequence = self._extract_sequence(app_events, "app")
        intent_sequence = self._extract_sequence(intent_events, "intent")
        search_sequence = self._extract_sequence(search_events, "query")
        file_sequence = self._extract_sequence(file_events, "path")

        patterns: list[dict[str, Any]] = []
        patterns.extend(self._rank(app_sequence, now, pattern_type="app_sequence", entity_key="apps"))
        patterns.extend(self._rank(intent_sequence, now, pattern_type="intent_sequence", entity_key="intents"))
        patterns.extend(self._rank(search_sequence, now, pattern_type="search_sequence", entity_key="queries"))
        patterns.extend(self._rank(file_sequence, now, pattern_type="file_sequence", entity_key="paths"))

        return patterns

    def _extract_sequence(self, events: list[dict[str, Any]], key: str) -> list[tuple[str, ...]]:
        sequences: list[tuple[str, ...]] = []
        items = [e.get("payload", {}).get(key) for e in events if e.get("payload", {}).get(key)]
        for length in range(2, self.max_sequence_len + 1):
            for i in range(len(items) - length + 1):
                seq = tuple(items[i : i + length])
                sequences.append(seq)
        return sequences

    def _rank(
        self,
        sequences: list[tuple[str, ...]],
        now: datetime,
        *,
        pattern_type: str,
        entity_key: str,
    ) -> list[dict[str, Any]]:
        counts = Counter(sequences)
        out = []
        for seq, count in counts.items():
            if count < self.min_support:
                continue
            confidence = min(1.0, count / 10.0)
            last_seen = now
            out.append(
                {
                    "pattern_type": pattern_type,
                    "sequence": list(seq),
                    entity_key: list(seq),
                    "frequency": count,
                    "confidence": confidence,
                    "last_seen": last_seen.isoformat(),
                    "created_at": now.isoformat(),
                    "metadata": {},
                }
            )
        return out

    def detect_time_habits(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            ts = e.get("ts")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts)
            h = dt.hour
            if 5 <= h < 12:
                bucket = "morning"
            elif 12 <= h < 17:
                bucket = "afternoon"
            elif 17 <= h < 21:
                bucket = "evening"
            else:
                bucket = "night"
            buckets[bucket].append(e)

        out = []
        for bucket, evts in buckets.items():
            app_counts = Counter(e.get("payload", {}).get("app") for e in evts if e.get("payload", {}).get("app"))
            top_apps = [app for app, _ in app_counts.most_common(5) if app]
            out.append(
                {
                    "pattern_type": "time_habit",
                    "name": f"{bucket}_routine",
                    "apps": top_apps,
                    "frequency": len(evts),
                    "confidence": min(1.0, len(evts) / 20.0),
                    "last_seen": max((e.get("ts") for e in evts if e.get("ts")), default=datetime.now(UTC).replace(tzinfo=None).isoformat()),
                    "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "metadata": {"time_bucket": bucket},
                }
            )
        return out

    def detect_day_habits(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            ts = e.get("ts")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts)
            day = dt.strftime("%A")
            buckets[day].append(e)

        out = []
        for day, evts in buckets.items():
            app_counts = Counter(e.get("payload", {}).get("app") for e in evts if e.get("payload", {}).get("app"))
            top_apps = [app for app, _ in app_counts.most_common(5) if app]
            out.append(
                {
                    "pattern_type": "day_habit",
                    "name": f"{day.lower()}_routine",
                    "apps": top_apps,
                    "frequency": len(evts),
                    "confidence": min(1.0, len(evts) / 10.0),
                    "last_seen": max((e.get("ts") for e in evts if e.get("ts")), default=datetime.now(UTC).replace(tzinfo=None).isoformat()),
                    "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "metadata": {"day": day},
                }
            )
        return out

    def detect_project_habits(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        folder_counts = Counter()
        folder_apps: dict[str, list[str]] = defaultdict(list)
        for e in events:
            if e.get("kind") == "file_open":
                path = e.get("payload", {}).get("path")
                app = e.get("payload", {}).get("app")
                if path:
                    folder = str(Path(path).parent)
                    folder_counts[folder] += 1
                    if app:
                        folder_apps[folder].append(app)

        out = []
        for folder, count in folder_counts.most_common(20):
            if count < 2:
                continue
            apps = list({a for a in folder_apps.get(folder, []) if a})[:8]
            out.append(
                {
                    "pattern_type": "project_habit",
                    "name": Path(folder).name,
                    "apps": apps,
                    "folders": [folder],
                    "frequency": count,
                    "confidence": min(1.0, count / 15.0),
                    "last_seen": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "metadata": {"folder": folder},
                }
            )
        return out
