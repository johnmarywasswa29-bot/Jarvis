"""Developer advisor: read-only suggestions for tests, TODOs, and duplicates."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Suggestion:
    confidence: float
    explanation: str
    files: list[str] = field(default_factory=list)
    recommended_action: Optional[str] = None


class DeveloperAdvisor:
    def __init__(self, project_root: Optional[Path] = None, max_files: int = 300) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.max_files = max_files
        self._todo_re = re.compile(r"\bTODO\b", re.IGNORECASE)
        self._fn_hash_cache: dict[str, str] = {}
        self._duplicate_candidates: list[tuple[str, str, str]] = []

    def suggest(self) -> list[Suggestion]:
        suggestions: list[Suggestion] = []
        py_files = self._python_files()
        suggestions.extend(self._todo_suggestions(py_files))
        suggestions.extend(self._test_suggestions(py_files))
        suggestions.extend(self._duplicate_suggestions(py_files))
        return suggestions

    def _python_files(self) -> list[Path]:
        out: list[Path] = []
        root = self.project_root
        count = 0
        for dirpath, _, filenames in os.walk(root):
            if count >= self.max_files:
                break
            for name in filenames:
                if count >= self.max_files:
                    break
                if not name.endswith(".py"):
                    continue
                p = Path(dirpath) / name
                out.append(p)
                count += 1
        return out

    def _todo_suggestions(self, files: list[Path]) -> list[Suggestion]:
        matched: list[str] = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if self._todo_re.search(text):
                matched.append(str(p))
        if not matched:
            return []
        return [
            Suggestion(
                confidence=0.9,
                explanation=f"I found {len(matched)} file(s) with TODO comments.",
                files=matched[:20],
                recommended_action="notify_user",
            )
        ]

    def _test_suggestions(self, files: list[Path]) -> list[Suggestion]:
        tested: set[str] = set()
        untested: list[str] = []
        for p in files:
            base = p.name
            if base.startswith("test_") or base.endswith("_test.py"):
                tested.add(self._module_key(p))
            else:
                untested.append(p)
        missing = [str(p) for p in untested if self._module_key(p) not in tested]
        if not missing:
            return []
        return [
            Suggestion(
                confidence=0.75,
                explanation=f"{len(missing)} module(s) have no obvious test file.",
                files=missing[:20],
                recommended_action="notify_user",
            )
        ]

    def _duplicate_suggestions(self, files: list[Path]) -> list[Suggestion]:
        self._fn_hash_cache = {}
        for p in files:
            text = self._safe_read(p)
            if not text:
                continue
            try:
                tree = ast.parse(text)
            except Exception:
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and len(ast.unparse(node).splitlines()) >= 15:
                    key = self._function_fingerprint(node)
                    self._fn_hash_cache.setdefault(key, []).append(str(p))
        dupes = {k: v for k, v in self._fn_hash_cache.items() if len(v) > 1}
        if not dupes:
            return []
        files_affected = sorted({f for v in dupes.values() for f in v})
        return [
            Suggestion(
                confidence=0.6,
                explanation=f"Found {len(dupes)} potentially duplicated function signature(s).",
                files=files_affected[:20],
                recommended_action="notify_user",
            )
        ]

    def _module_key(self, path: Path) -> str:
        return path.stem.lower().replace("test_", "").replace("_test", "")

    def _safe_read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _function_fingerprint(self, node: ast.FunctionDef) -> str:
        try:
            body = ast.unparse(node)
        except Exception:
            body = ""
        return hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()
