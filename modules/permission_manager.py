"""Permission manager for Jarvis tool execution."""
from __future__ import annotations

import asyncio
from typing import Optional


class PermissionManager:
    """Gates destructive tool actions behind configurable confirmation."""

    LEVELS = ("SAFE", "CAUTION", "DANGEROUS")

    _DEFAULTS = {
        # SAFE
        "calculator": "SAFE",
        "read_clipboard": "SAFE",
        "screenshot": "SAFE",
        # Phase A capability foundation — read-only / observation (SAFE)
        "git": "SAFE",                 # GitTool is read-only inspection only
        "test_execution": "SAFE",      # observation of code behavior
        "workspace_observe": "SAFE",    # project-state observation
        # CAUTION
        "search": "CAUTION",
        "web_search": "CAUTION",
        "web_browsing": "CAUTION",
        "open_application": "CAUTION",
        "open_app": "CAUTION",
        "read_files": "CAUTION",
        "filesystem:read": "CAUTION",
        "desktop_control": "CAUTION",
        # Phase A — source modification (CAUTION, requires confirmation)
        "file_edit": "CAUTION",        # in-place edit / apply patch
        # Phase B — controlled execution (DANGEROUS, explicit confirmation)
        "shell": "DANGEROUS",
        "build": "DANGEROUS",
        "dependency": "DANGEROUS",
        # DANGEROUS
        "terminal": "DANGEROUS",
        "execute_python": "DANGEROUS",
        "execute_code": "DANGEROUS",
        "code_execution": "DANGEROUS",
        "delete_files": "DANGEROUS",
        "filesystem:delete": "DANGEROUS",
        "filesystem:write": "DANGEROUS",
        "move_files": "DANGEROUS",
        "install_software": "DANGEROUS",
        "registry_edits": "DANGEROUS",
    }

    def __init__(self) -> None:
        self._caution_remembered: set[str] = set()
        self._dangerous_whitelisted: set[str] = set()
        self._permissions = dict(self._DEFAULTS)

    def set_permission(self, action: str, level: str) -> None:
        level = level.upper()
        if level not in self.LEVELS:
            raise ValueError(f"Unknown permission level: {level}")
        self._permissions[action] = level

    def get_level(self, action: str) -> str:
        return self._permissions.get(action, "CAUTION")

    def is_safe(self, action: str) -> bool:
        return self.get_level(action) == "SAFE"

    def requires_confirmation(self, action: str) -> bool:
        level = self.get_level(action)
        if level == "SAFE":
            return False
        if level == "CAUTION":
            return action not in self._caution_remembered
        if level == "DANGEROUS":
            return action not in self._dangerous_whitelisted
        return True

    def approve_once(self, action: str) -> None:
        self._caution_remembered.add(action)

    def approve_permanently(self, action: str) -> None:
        self._dangerous_whitelisted.add(action)

    def confirm(self, action: str, details: str = "") -> bool:
        level = self.get_level(action)
        if level == "SAFE":
            return True
        if level == "CAUTION" and action in self._caution_remembered:
            return True
        if level == "DANGEROUS" and action in self._dangerous_whitelisted:
            return True

        prompt = f"[{level}] Allow {action}? {details} (y/N): "
        try:
            loop = asyncio.get_running_loop()
            answer = loop.run_in_executor(None, lambda: input(prompt)).strip().lower()
        except RuntimeError:
            try:
                answer = input(prompt).strip().lower()
            except EOFError:
                return False

        if answer.startswith(("y", "yes")):
            if level == "CAUTION":
                self.approve_once(action)
            elif level == "DANGEROUS":
                if "always" in answer or "permanent" in answer or "whitelist" in answer:
                    self.approve_permanently(action)
            return True
        return False
