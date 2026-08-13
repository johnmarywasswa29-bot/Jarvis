"""Project detection heuristics."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from workspace.state import ProjectContext


IDE_MARKERS = {
    "code.exe": "VS Code",
    "Code.exe": "VS Code",
    "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm",
    "idea64.exe": "IntelliJ",
    "Cursor.exe": "Cursor",
    "notepad++.exe": "Notepad++",
}

LANG_MARKERS = {
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "Pipfile": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "package.json": "JavaScript",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "*.csproj": "C#",
    "*.sln": "C#",
    "CMakeLists.txt": "C++",
    "Makefile": "C/C++",
}


class ProjectDetector:
    def detect(self, path: Optional[str]) -> ProjectContext:
        ctx = ProjectContext()
        if not path:
            return ctx
        root = Path(path)
        if not root.exists() or not root.is_dir():
            return ctx
        ctx.path = str(root)
        ctx.name = root.name
        # Detect git repo
        git = root / ".git"
        if git.exists():
            ctx.git_repo = self._git_remote(root) or str(root)
        # Detect language by markers
        for marker, lang in LANG_MARKERS.items():
            if marker.startswith("*"):
                if any(root.glob(marker)):
                    ctx.language = lang
                    break
            elif (root / marker).exists():
                ctx.language = lang
                break
        # Detect IDE from markers in cwd is not reliable without window title; keep empty
        return ctx

    @staticmethod
    def _git_remote(root: Path) -> Optional[str]:
        try:
            if not (root / ".git" / "config").exists():
                return None
            import subprocess
            out = subprocess.check_output(
                ["git", "remote", "get-url", "origin", "--git-dir", str(root / ".git")],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return out.strip() or None
        except Exception:
            return None
