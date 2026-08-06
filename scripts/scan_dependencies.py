"""
Dependency scanner: discovers third-party imports and classifies them.
This is the single canonical module used by:
  - scripts/verify_dependencies.py
  - scripts/scan_dependencies.py
  - scripts/audit_dependencies.py
"""

from __future__ import annotations

import ast
import os as _os
import sys
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


REPO = Path(__file__).resolve().parents[1]

SCAN_ROOTS: List[Path] = [
    REPO / "agent",
    REPO / "goal_manager",
    REPO / "installer",
    REPO / "installer" / "hooks",
    REPO / "knowledge",
    REPO / "modules",
    REPO / "scripts",
    REPO / "task_queue",
    REPO / "tests",
    REPO / "ui",
    REPO / "plugins",
    REPO / "memory",
    REPO / "data",
    REPO / "build_test",
    REPO / "dist_test",
    REPO / "installer_test",
    REPO / "debug_launcher.py",
    REPO / "jarvis.py",
]

SCAN_EXCLUDE_DIRS = frozenset({
    ".venv",
    "build",
    "dist",
    "__pycache__",
    ".git",
    "cache",
    "node_modules",
    "logs",
    "Release",
    ".next",
    "site-packages",
    "external",
})


@dataclass(frozen=True)
class ImportRecord:
    pkg: str
    file: str
    alias: Optional[str]
    stdlib: bool
    top_pkg: str


@dataclass(frozen=True)
class DependencyMeta:
    import_name: str
    pip_name: str
    classification: str = "REQUIRED"
    reason: str = ""


REQUIRED_DEPS: Tuple[DependencyMeta, ...] = (
    DependencyMeta("yaml", "PyYAML>=6.0", "REQUIRED", "YAML configuration parsing"),
    DependencyMeta("ollama", "ollama>=0.4.7", "REQUIRED", "Local LLM runtime client"),
    DependencyMeta("numpy", "numpy>=1.24", "REQUIRED", "Audio and numeric utilities"),
    DependencyMeta("sounddevice", "sounddevice>=0.5.1", "REQUIRED", "Audio capture"),
    DependencyMeta("soundfile", "soundfile>=0.12.1", "REQUIRED", "Wave file I/O"),
    DependencyMeta("pvporcupine", "pvporcupine>=3.0.5", "REQUIRED", "Wake-word detection"),
    DependencyMeta("pvrecorder", "pvrecorder>=1.2.7", "REQUIRED", "Audio recorder"),
    DependencyMeta("vosk", "vosk>=0.3.45", "REQUIRED", "Offline speech recognition"),
    DependencyMeta("requests", "requests>=2.31", "REQUIRED", "HTTP requests"),
    DependencyMeta("bs4", "beautifulsoup4>=4.12", "REQUIRED", "HTML/web content parsing"),
    DependencyMeta("dotenv", "python-dotenv>=1.0", "REQUIRED", "Environment variable loading"),
    DependencyMeta("pyttsx3", "pyttsx3>=2.90", "REQUIRED", "Text-to-speech"),
    DependencyMeta("pyautogui", "pyautogui>=0.9.54", "REQUIRED", "Desktop automation"),
    DependencyMeta("pygetwindow", "pygetwindow>=0.0.9", "REQUIRED", "Window enumeration"),
    DependencyMeta("ddgs", "duckduckgo-search>=6.0", "REQUIRED", "Web search"),
    DependencyMeta("duckduckgo_search", "duckduckgo-search>=6.0", "REQUIRED", "Web search"),
    DependencyMeta("psutil", "psutil>=5.9", "REQUIRED", "System monitoring"),
)

OPTIONAL_DEPS: Tuple[DependencyMeta, ...] = (
    DependencyMeta("PIL", "Pillow>=10.0", "OPTIONAL", "Image handling"),
    DependencyMeta("PySide6", "PySide6>=6.6", "OPTIONAL", "Desktop GUI framework"),
    DependencyMeta("chromadb", "chromadb>=0.5", "OPTIONAL", "Vector memory backend"),
    DependencyMeta("sentence_transformers", "sentence-transformers>=3.0", "OPTIONAL", "Embeddings"),
    DependencyMeta("fitz", "PyMuPDF", "OPTIONAL", "PDF parsing"),
    DependencyMeta("torch", "torch", "OPTIONAL", "Optional ML runtime"),
    DependencyMeta("weaviate", "weaviate", "OPTIONAL", "Vector backend"),
    DependencyMeta("pandas", "pandas", "OPTIONAL", "Tabular data"),
    DependencyMeta("markdown", "markdown", "OPTIONAL", "Markdown rendering"),
    DependencyMeta("gunicorn", "gunicorn", "OPTIONAL", "Deployment server"),
    DependencyMeta("uvicorn", "uvicorn", "OPTIONAL", "Deployment server"),
    DependencyMeta("asyncio_mqtt", "asyncio-mqtt", "OPTIONAL", "IoT integration"),
    DependencyMeta("prometheus_client", "prometheus-client", "OPTIONAL", "Metrics export"),
    DependencyMeta("openai", "openai>=1.0", "OPTIONAL", "Cloud backend"),
    DependencyMeta("ncclient", "ncclient", "OPTIONAL", "Network tooling"),
    DependencyMeta("fastmcp", "fastmcp", "OPTIONAL", "MCP tooling"),
)

TEST_DEV_DEPS: Tuple[DependencyMeta, ...] = (
    DependencyMeta("_pytest", "pytest", "TEST/DEV", "Test runner"),
    DependencyMeta("coverage", "coverage", "TEST/DEV", "Coverage tooling"),
    DependencyMeta("hypothesis", "hypothesis", "TEST/DEV", "Property-based testing"),
)

_CATALOG: Dict[str, DependencyMeta] = {m.import_name: m for m in (*REQUIRED_DEPS, *OPTIONAL_DEPS, *TEST_DEV_DEPS)}


def _is_stdlib(name: str, *, parent: Optional[str] = None) -> bool:
    module = parent or name
    return module in sys.stdlib_module_names


def _dedupe(paths: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _iter_py_files() -> List[Path]:
    out: List[Path] = []
    seen: Set[Path] = set()
    stack = [root for root in SCAN_ROOTS if root.exists()]
    while stack:
        current = stack.pop()
        try:
            entries = [e for e in current.iterdir()]
        except Exception:
            continue
        dirs = [e for e in entries if e.is_dir() and e.name not in SCAN_EXCLUDE_DIRS]
        pyfiles = [e for e in entries if e.is_file() and e.name.endswith(".py")]
        stack.extend(dirs)
        for path in pyfiles:
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    out.sort(key=lambda p: p.as_posix())
    return out


def scan_imports() -> List["ImportRecord"]:
    records: List["ImportRecord"] = []
    for file in _iter_py_files():
        rel = file.relative_to(REPO).as_posix()
        try:
            source = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module
                if module:
                    top = module.split(".")[0]
                    records.append(
                        ImportRecord(pkg=top, file=rel, alias=(node.names[0].name if node.names else top), stdlib=_is_stdlib(top, parent=module) or _is_stdlib(module), top_pkg=top)
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    records.append(
                        ImportRecord(pkg=pkg, file=rel, alias=alias.asname or pkg, stdlib=_is_stdlib(pkg), top_pkg=pkg)
                    )
    return records


def build_catalog(records: List["ImportRecord"]) -> Dict[str, Dict[str, "DependencyMeta"]]:
    catalog: Dict[str, Dict[str, "DependencyMeta"]] = {}
    for rec in records:
        if rec.stdlib:
            continue
        meta = _CATALOG.get(rec.pkg)
        if not meta:
            continue
        entry = catalog.setdefault(meta.import_name, {"meta": meta, "files": []})
        if rec.file not in entry["files"]:  # type: ignore[arg-type]
            entry["files"].append(rec.file)  # type: ignore[arg-type]
    for entry in catalog.values():
        entry["files"] = _dedupe(entry["files"])  # type: ignore[arg-type]
    return catalog


def render_markdown_report(catalog: Dict[str, Dict[str, "DependencyMeta"]]) -> str:
    lines: List[str] = [
        "# Dependency Audit Report",
        "",
        "| Package | Classification | Reason | Installed | Locations |",
        "| - | - | - | - |",
    ]
    missing_required: List[str] = []
    for pkg in sorted(catalog):
        meta = catalog[pkg]["meta"]
        files = catalog[pkg]["files"]
        try:
            spec = importlib.util.find_spec(pkg)
            installed = "yes" if spec else "no"
        except Exception:
            installed = "unknown"
        locs = "; ".join(files[:5])
        if len(files) > 5:
            locs += "; ..."
        lines.append(
            f"| {meta.import_name} | {meta.classification} | {meta.reason} | {installed} | {locs} |"
        )
        if installed == "no" and meta.classification == "REQUIRED":
            missing_required.append(meta.pip_name)
    lines.extend(["", "## Health", ""])
    if missing_required:
        lines.append(f"Missing required packages: {', '.join(missing_required)}")
    else:
        lines.append("All listed packages are present.")
    return "\n".join(lines)


def write_requirements(path: Optional[Path] = None) -> Path:
    if path is None:
        path = REPO / "requirements.txt"
    required = sorted(REQUIRED_DEPS, key=lambda m: m.import_name)
    optional = sorted(OPTIONAL_DEPS, key=lambda m: m.import_name)
    out_lines: List[str] = [
        "# Auto-generated minimum versions from scripts/scan_dependencies.py",
        "# Re-generate with: py scripts/scan_dependencies.py --write-requirements",
        "",
    ]
    for meta in required:
        out_lines.append(meta.pip_name)
    if optional:
        out_lines.append("# Optional extras")
        for meta in optional:
            out_lines.append(f"# {meta.pip_name}")
    body = "\n".join(out_lines) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def required_pip_args() -> List[str]:
    return [m.pip_name for m in REQUIRED_DEPS]


if __name__ == "__main__":
    records = scan_imports()
    catalog = build_catalog(records)
    out_path = None
    if len(sys.argv) > 1 and "--write-requirements" in sys.argv:
        # Optional arbritrary output path:
        #   py scan_dependencies.py --write-requirements [path]
        if len(sys.argv) > 2 and not sys.argv[2].startswith("-"):
            out_path = Path(sys.argv[2])
        elif "REQUIREMENTS_PATH" in os.environ:
            out_path = Path(os.environ["REQUIREMENTS_PATH"])
    if out_path:
        print(write_requirements(out_path))
    elif len(sys.argv) > 1 and "--write-requirements" in sys.argv:
        print(write_requirements())
    else:
        print(render_markdown_report(catalog))
