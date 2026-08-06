"""Git helper plugin."""
from __future__ import annotations

import os
import subprocess
from typing import Any


class GitHelper:
    name = "git_helper"
    version = "1.0.0"

    def __init__(self, api: Any = None) -> None:
        self.api = api

    @staticmethod
    def _run(cmd):
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _find_git():
        checks = ["git", r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"]
        for git in checks:
            if GitHelper._run([git, "--version"]):
                return git
        return None

    def commit_all(self, message: str) -> str:
        git = self._find_git()
        if not git:
            return "Git not found"
        try:
            subprocess.run([git, "add", "-A"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run([git, "commit", "-m", message], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return "Committed"
        except Exception as e:
            return f"Commit failed: {e}"
