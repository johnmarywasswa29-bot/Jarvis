from __future__ import annotations

import ast
import operator as _operator
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Optional

import pyautogui
import pygetwindow as gw
from ddgs import DDGS

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.permission_manager import PermissionManager
from research.fetcher import fetch_url, FetchResult
from research.extractor import extract_content, ExtractionResult

logger = get_logger("tools")

try:
    from modules.execution_manager import ExecutionManager, ExecutionResult

    _HAS_EXEC_MANAGER = True
except Exception:
    _HAS_EXEC_MANAGER = False

try:
    from modules.mcp_client import MCPClient

    _HAS_MCP = True
except Exception:
    _HAS_MCP = False

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15
_WINDOWS = platform.system() == "Windows"


class ToolResult:
    def __init__(self, success: bool, output: str, error: str = "", duration_s: float = 0.0) -> None:
        self.success = success
        self.output = output
        self.error = error
        self.duration_s = duration_s

    def __str__(self) -> str:
        if self.success:
            return f"[OK] {self.output}"
        return f"[FAIL] {self.error}"


class BaseTool:
    name: str = "base"
    description: str = ""
    enabled: bool = True

    def can_handle(self, prompt: str) -> bool:
        return False

    def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate safe math expressions."

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        low = low.replace("what is", "").replace("calculate", "").replace("compute", "").strip()
        if not low:
            return False
        cleaned = low.replace("+", " ").replace("-", " ").replace("*", " ").replace("/", " ").replace("(", " ").replace(")", " ").replace(".", " ").replace("%", " ").replace("^", " ")
        return all(t.isdigit() or t in {"plus", "minus", "times", "divided", "by", "mod", "power"} for t in cleaned.split()) or bool(
            re.fullmatch(r"[0-9\s\+\-\*/\.\(\)\%\^]+", low)
        )

    def execute(self, expression: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        expression = (expression or kwargs.get("expression", "")).strip()
        if not expression:
            return ToolResult(False, "", error="Missing expression", duration_s=time.time() - t0)
        lowered = expression.lower()
        for prefix in ["calculate ", "compute ", "what is "]:
            if lowered.startswith(prefix):
                expression = expression[len(prefix):].strip()
                lowered = lowered[len(prefix):].strip()
                break
        expression = expression.replace("^", "**")
        try:
            tree = ast.parse(expression, mode="eval")
        except Exception as exc:
            return ToolResult(False, "", error=f"Calc error: {exc}", duration_s=time.time() - t0)

        def _eval(node):
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
                return node.value
            if isinstance(node, ast.BinOp):
                left = _eval(node.left)
                right = _eval(node.right)
                ops = {
                    ast.Add: _operator.add,
                    ast.Sub: _operator.sub,
                    ast.Mult: _operator.mul,
                    ast.Div: _operator.truediv,
                    ast.Mod: _operator.mod,
                    ast.Pow: _operator.pow,
                    ast.FloorDiv: _operator.floordiv,
                }
                fn = ops.get(type(node.op))
                if fn is None:
                    raise ValueError("unsupported operator")
                return fn(left, right)
            if isinstance(node, ast.UnaryOp):
                operand = _eval(node.operand)
                if isinstance(node.op, ast.USub):
                    return -operand
                if isinstance(node.op, ast.UAdd):
                    return +operand
                raise ValueError("unsupported unary operator")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "pow":
                    args = [_eval(arg) for arg in node.args]
                    return pow(*args)
                raise ValueError("unsupported function")
            raise ValueError("unsupported expression token")

        try:
            result = _eval(tree)
            return ToolResult(True, str(result), duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"Calc error: {exc}", duration_s=time.time() - t0)


class SystemControlTool(BaseTool):
    name = "system_control"
    description = "Windows system controls: volume, lock, mute, sleep, shutdown, restart."

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["volume up", "volume down", "mute", "unmute", "lock screen", "shutdown", "restart", "sleep", "wifi on", "wifi off", "bluetooth on", "bluetooth off"])

    def execute(self, action: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        action = (action or kwargs.get("action", "")).lower().strip()
        if not _WINDOWS:
            return ToolResult(False, "", error="System control only supported on Windows", duration_s=time.time() - t0)
        try:
            if action == "volume_up":
                for _ in range(2):
                    pyautogui.press("volumeup")
                return ToolResult(True, "Volume increased", duration_s=time.time() - t0)
            if action == "volume_down":
                for _ in range(2):
                    pyautogui.press("volumedown")
                return ToolResult(True, "Volume decreased", duration_s=time.time() - t0)
            if action == "mute":
                pyautogui.press("volumemute")
                return ToolResult(True, "Muted", duration_s=time.time() - t0)
            if action == "unmute":
                pyautogui.press("volumemute")
                return ToolResult(True, "Unmuted", duration_s=time.time() - t0)
            if action == "lock":
                os.system("rundll32.exe user32.dll,LockWorkStation")
                return ToolResult(True, "Locked", duration_s=time.time() - t0)
            if action == "sleep":
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
                return ToolResult(True, "Sleeping", duration_s=time.time() - t0)
            if action == "shutdown":
                os.system("shutdown /s /t 0")
                return ToolResult(True, "Shutting down", duration_s=time.time() - t0)
            if action == "restart":
                os.system("shutdown /r /t 0")
                return ToolResult(True, "Restarting", duration_s=time.time() - t0)
            return ToolResult(False, "", error=f"Unknown system action: {action}", duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"System control error: {exc}", duration_s=time.time() - t0)


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for current information. Use for news, facts, or lookups."

    def can_handle(self, prompt: str) -> bool:
        patterns = [r"\bsearch\b", r"\bfind\b", r"\blookup\b", r"\bwho\b", r"\bwhen\b", r"\bwhere\b", r"\bwhat is\b", r"\bhow\b", r"\bwhy\b"]
        return any(re.search(p, prompt, re.IGNORECASE) for p in patterns)

    def execute(self, query: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        if not query:
            return ToolResult(False, "", error="Missing query", duration_s=time.time() - t0)
        try:
            from research.fetcher import search_web

            # Bounded: DDGS has no built-in timeout, so enforce a hard ceiling.
            results = search_web(query, max_results=5)

            if not results:
                return ToolResult(True, f"No web results found for '{query}'", duration_s=time.time() - t0)

            summary = f"Search results for '{query}':\n"
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                url = r.get("href", r.get("url", ""))
                body = r.get("body", "")[:180]
                summary += f"{i}. {title}: {url}\n   {body}\n"

            return ToolResult(True, summary, duration_s=time.time() - t0)
        except Exception as exc:
            return ToolResult(False, "", error=f"Web search error: {exc}", duration_s=time.time() - t0)


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetch and extract content from a specific URL. Use after web_search to read a result."

    def can_handle(self, prompt: str) -> bool:
        low = prompt.lower()
        return any(x in low for x in ["fetch", "read url", "open url", "read page", "fetch page"])

    def execute(self, url: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        url = (url or kwargs.get("url", "")).strip()
        if not url:
            return ToolResult(False, "", error="Missing URL", duration_s=time.time() - t0)
        
        # Validate URL scheme
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(False, "", error=f"Unsupported scheme: {parsed.scheme}. Only http/https allowed.", duration_s=time.time() - t0)
        
        try:
            # Fetch the URL
            fetch_result = fetch_url(url)
            if not fetch_result.success:
                return ToolResult(False, "", error=f"Fetch failed: {fetch_result.error}", duration_s=time.time() - t0)
            
            # Extract content
            extraction_result = extract_content(fetch_result.content, url=fetch_result.final_url)
            if not extraction_result.success:
                return ToolResult(False, "", error=f"Extraction failed: {extraction_result.error}", duration_s=time.time() - t0)
            
            # Prepare structured result
            output = {
                "url": fetch_result.final_url,
                "original_url": url,
                "title": extraction_result.title or fetch_result.metadata.get("title", ""),
                "text": extraction_result.text[:5000],  # Limit for tool output
                "metadata": extraction_result.metadata,
                "extraction_method": extraction_result.method,
            }
            
            summary = f"Fetched: {output['url']}\nTitle: {output['title']}\nMethod: {output['extraction_method']}\nText preview: {output['text'][:500]}..."
            
            return ToolResult(True, summary, duration_s=time.time() - t0)
            
        except Exception as exc:
            return ToolResult(False, "", error=f"Web fetch error: {exc}", duration_s=time.time() - t0)


class DesktopControlTool(BaseTool):
    name = "desktop_control"
    description = "Control mouse, keyboard, and windows on the Windows desktop (Dell Latitude)."

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def can_handle(self, prompt: str) -> bool:
        patterns = [r"\bopen\b", r"\bclick\b", r"\btype\b", r"\bpress\b", r"\bclose\b", r"\bfull ?screen\b", r"\bwindow\b", r"\bscreenshot\b"]
        return any(re.search(p, prompt, re.IGNORECASE) for p in patterns)

    def execute(self, action: str = "", target: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        try:
            action = (action or kwargs.get("action", "")).lower().strip()

            if not action:
                return ToolResult(False, "", error="No action specified for desktop_control", duration_s=time.time() - t0)

            if action == "list_windows":
                wins = gw.getAllWindows()
                titles = [w.title for w in wins if w.title]
                return ToolResult(True, "Windows: " + ", ".join(titles[:20]), duration_s=time.time() - t0)

            if action == "activate":
                wins = [w for w in gw.getAllWindows() if target.lower() in w.title.lower()] if target else []
                if not wins:
                    return ToolResult(False, "", error=f"Window matching '{target}' not found", duration_s=time.time() - t0)
                wins[0].activate()
                return ToolResult(True, f"Activated window: {wins[0].title}", duration_s=time.time() - t0)

            if action == "open_app":
                return self._open_app(target, t0)

            if action == "screenshot":
                shot = pyautogui.screenshot()
                path = Path("logs/screenshot.png")
                path.parent.mkdir(parents=True, exist_ok=True)
                shot.save(str(path))
                return ToolResult(True, f"Screenshot saved to {path}", duration_s=time.time() - t0)

            if action == "click":
                x, y = kwargs.get("x", 0), kwargs.get("y", 0)
                pyautogui.click(x, y)
                return ToolResult(True, f"Clicked at ({x}, {y})", duration_s=time.time() - t0)

            if action == "type":
                text = kwargs.get("text", target)
                pyautogui.typewrite(text, interval=0.05)
                return ToolResult(True, f"Typed {len(text)} chars", duration_s=time.time() - t0)

            if action == "press":
                key = kwargs.get("key", target)
                pyautogui.press(key)
                return ToolResult(True, f"Pressed key: {key}", duration_s=time.time() - t0)

            return ToolResult(False, "", error=f"Unknown desktop action: {action}", duration_s=time.time() - t0)

        except Exception as exc:
            return ToolResult(False, "", error=f"Desktop error: {exc}", duration_s=time.time() - t0)

    def _open_app(self, target: str, t0: float) -> ToolResult:
        target = (target or "").strip()
        if not target:
            return ToolResult(False, "", error="Empty app target", duration_s=time.time() - t0)

        if not _WINDOWS:
            try:
                subprocess.Popen([target], close_fds=True)
                return ToolResult(True, f"Opened '{target}'", duration_s=time.time() - t0)
            except FileNotFoundError:
                return ToolResult(False, "", error=f"App not found: {target}", duration_s=time.time() - t0)

        # Fast path: direct extension/executable open without shell enumeration.
        try:
            os.startfile(target)
            return ToolResult(True, f"Opened '{target}'", duration_s=time.time() - t0)
        except FileNotFoundError:
            return ToolResult(False, "", error=f"App not found: {target}", duration_s=time.time() - t0)
        except Exception:
            pass

        # Fallback: non-shell subprocess against a small allow-list only.
        app_lower = target.lower()
        allowed_apps = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "mspaint": "mspaint.exe",
            "paint": "mspaint.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "explorer": "explorer.exe",
        }
        exe = allowed_apps.get(app_lower)
        if exe is not None:
            try:
                subprocess.Popen([exe], close_fds=True)
                return ToolResult(True, f"Opened '{target}' via {exe}", duration_s=time.time() - t0)
            except FileNotFoundError:
                return ToolResult(False, "", error=f"App not found: {exe}", duration_s=time.time() - t0)
            except Exception as exc:
                return ToolResult(False, "", error=f"App launch error: {exc}", duration_s=time.time() - t0)
        return ToolResult(False, "", error=f"Refused to launch untrusted target: {target}", duration_s=time.time() - t0)


class CodeExecutionTool(BaseTool):
    name = "code_execution"
    description = "Execute Python code in an isolated subprocess. For calculations, data processing, quick scripts."

    def __init__(self, config: Any = None, *, permissions: Any | None = None) -> None:
        self.config = config
        self._permissions = permissions
        self._execution_manager = None
        if _HAS_EXEC_MANAGER and config is not None:
            try:
                self._execution_manager = ExecutionManager(config, permissions=permissions)
            except Exception:
                pass

    def can_handle(self, prompt: str) -> bool:
        patterns = [r"\bcalculate\b", r"\bcompute\b", r"\bscript\b", r"\bpython\b", r"\brun\b", r"\bcode\b", r"\bsolve\b"]
        return any(re.search(p, prompt, re.IGNORECASE) for p in patterns)

    def execute(self, code: str = "", language: str = "python", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        if not code:
            return ToolResult(False, "", error="No code provided", duration_s=time.time() - t0)

        if language != "python":
            return ToolResult(False, "", error=f"Language {language} not supported", duration_s=time.time() - t0)

        if self._execution_manager is not None:
            result = self._execution_manager.execute(code)
            return result.to_tool_result()

        return ToolResult(False, "", error="Execution manager unavailable", duration_s=time.time() - t0)


class FileSystemTool(BaseTool):
    name = "filesystem"
    description = "Read, write, list, move files and folders. Use for organizing files."

    _allowed_roots = ("Downloads", "Desktop", "Documents")

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return Path.cwd() / p

    def _allowed_path(self, path: Path) -> bool:
        parts = [part.lower() for part in path.resolve().parts]
        return any(root.lower() in parts for root in self._allowed_roots)

    def _safe(self, path: Path) -> bool:
        return self._allowed_path(path)

    def can_handle(self, prompt: str) -> bool:
        patterns = [r"\bcreate\b", r"\bwrite\b", r"\bread\b", r"\bfile\b", r"\bfolder\b", r"\blist\b", r"\borganize\b", r"\bdelete\b", r"\bmove\b"]
        return any(re.search(p, prompt, re.IGNORECASE) for p in patterns)

    def execute(self, action: str = "", source: str = "", target: str = "", content: str = "", **kwargs: Any) -> ToolResult:
        t0 = time.time()
        try:
            action = (action or kwargs.get("action", "")).lower().strip()
            if not action:
                return ToolResult(False, "", error="No action given for filesystem", duration_s=time.time() - t0)

            if action == "list":
                path = self._resolve(source or ".")
                if not path.exists():
                    return ToolResult(False, "", error=f"Path not found: {path}", duration_s=time.time() - t0)
                if not self._allowed_path(path):
                    return ToolResult(False, "", error=f"Path not allowed: {path}", duration_s=time.time() - t0)
                items = [p.name for p in sorted(path.iterdir())]
                return ToolResult(True, f"Folder {path}:\n" + "\n".join(items[:80]), duration_s=time.time() - t0)

            if action == "write":
                path = self._resolve(source or kwargs.get("source", ""))
                if not self._allowed_path(path):
                    return ToolResult(False, "", error=f"Path not allowed: {path}", duration_s=time.time() - t0)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content or kwargs.get("content", ""), encoding="utf-8")
                return ToolResult(True, f"Wrote file: {path}", duration_s=time.time() - t0)

            if action == "read":
                path = self._resolve(source or kwargs.get("source", ""))
                if not path.exists():
                    return ToolResult(False, "", error=f"File not found: {path}", duration_s=time.time() - t0)
                if not self._allowed_path(path):
                    return ToolResult(False, "", error=f"Path not allowed: {path}", duration_s=time.time() - t0)
                txt = path.read_text(encoding="utf-8", errors="replace")[:4000]
                return ToolResult(True, txt, duration_s=time.time() - t0)

            if action == "delete":
                path = self._resolve(source or kwargs.get("source", ""))
                if not path.exists():
                    return ToolResult(False, "", error=f"Path not found: {path}", duration_s=time.time() - t0)
                if not self._allowed_path(path):
                    return ToolResult(False, "", error=f"Path not allowed: {path}", duration_s=time.time() - t0)
                target_file = self._resolve(target or kwargs.get("target", ""))
                if not self._allowed_path(target_file):
                    return ToolResult(False, "", error=f"Path not allowed: {target_file}", duration_s=time.time() - t0)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                path.replace(target_file)
                return ToolResult(True, f"Moved {path} -> {target_file}", duration_s=time.time() - t0)

            return ToolResult(False, "", error=f"Unknown filesystem action: {action}", duration_s=time.time() - t0)

        except Exception as exc:
            return ToolResult(False, "", error=f"Filesystem error: {exc}", duration_s=time.time() - t0)


class ToolRegistry:
    def __init__(self, config: JarvisConfig, **kwargs: Any) -> None:
        self.config = config
        self.permissions = kwargs.get("permissions")
        self._init_tools()

    def _init_tools(self) -> None:
        base_tools: list[BaseTool] = [
            WebSearchTool(),
            WebFetchTool(),
            DesktopControlTool(self.config),
            CodeExecutionTool(self.config, permissions=self.permissions),
            FileSystemTool(),
            CalculatorTool(),
            SystemControlTool(),
        ]
        plugin_tools: list[BaseTool] = []
        if _HAS_MCP:
            try:
                mcp = MCPClient(self.config)
                plugin_tools = [t for t in mcp.load_plugins() if isinstance(t, BaseTool)]
            except Exception as exc:
                self.logger = get_logger("tool_registry")
                self.logger.warning("MCP tools disabled: %s", exc)
        self.tools: list[BaseTool] = [t for t in base_tools + plugin_tools if t.enabled]
        self.logger = get_logger("tool_registry")
        self.logger.info("Tools loaded: %s", [t.name for t in self.tools])

    def set_permissions(self, permissions: Any) -> None:
        self.permissions = permissions
        for tool in self.tools:
            if tool.name == "code_execution" and hasattr(tool, "_permissions"):
                tool._permissions = permissions

    def has_tool(self, name: str) -> bool:
        """Return True if a registered tool with ``name`` exists."""
        return any(t.name == name for t in self.tools)

    def get_tool(self, name: str) -> BaseTool | None:
        """Return the registered tool with ``name`` or None if unknown.

        Used by the planner to validate that a plan step references a real,
        supported tool before the plan can become an executable proposal.
        """
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def tool_names(self) -> list[str]:
        """Names of all currently registered (enabled) tools."""
        return [t.name for t in self.tools]

    def select_tools(self, prompt: str) -> list[BaseTool]:
        matched = [t for t in self.tools if t.enabled and t.can_handle(prompt)]
        if not matched:
            return [WebSearchTool()]
        return matched

    def run_tool(self, tool: BaseTool, prompt: str = "", **override_kwargs: Any) -> ToolResult:
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {"prompt": prompt}
        if tool.name == "web_search":
            m = re.search(r"(?:search|find|lookup|look up)\s+(.+)", prompt, re.IGNORECASE)
            kwargs["query"] = m.group(1).strip() if m else prompt
        elif tool.name == "desktop_control":
            low = prompt.lower()
            if low.startswith("open ") or low.startswith("launch "):
                kwargs["action"] = "open_app"
                kwargs["target"] = prompt.split(" ", 1)[1].strip() if " " in prompt else prompt
            elif "list" in low or "windows" in low:
                kwargs["action"] = "list_windows"
            elif "screenshot" in low or "screen" in low:
                kwargs["action"] = "screenshot"
            elif low.startswith("click at "):
                kwargs["action"] = "click"
                m = re.search(r"at\s+(\d+)\s*[,/]\s*(\d+)", low, re.IGNORECASE)
                if m:
                    kwargs["x"], kwargs["y"] = int(m.group(1)), int(m.group(2))
            elif low.startswith("type "):
                kwargs["action"] = "type"
                kwargs["text"] = prompt.split(" ", 1)[1].strip() if " " in prompt else ""
            elif low.startswith("press "):
                kwargs["action"] = "press"
                kwargs["key"] = prompt.split(" ", 1)[1].strip() if " " in prompt else "enter"
            else:
                kwargs["action"] = low
        elif tool.name == "filesystem":
            low = prompt.lower()
            if any(x in low for x in ["list files", "list folder"]):
                kwargs["action"] = "list"
                m = re.search(r"(?:in|of|from)\s+(.+)", low, re.IGNORECASE)
                kwargs["source"] = m.group(1).strip() if m else "."
            elif any(x in low for x in ["write file", "create file"]):
                kwargs["action"] = "write"
                m = re.search(r"(?:file|to)\s+([^\s]+)", low, re.IGNORECASE)
                kwargs["source"] = m.group(1) if m else "output.txt"
                kwargs["content"] = ""
        elif tool.name == "code_execution":
            kwargs["code"] = kwargs.get("prompt", "")
        elif tool.name == "calculator":
            kwargs["expression"] = prompt
        elif tool.name == "system_control":
            low = prompt.lower()
            if "volume up" in low:
                kwargs["action"] = "volume_up"
            elif "volume down" in low:
                kwargs["action"] = "volume_down"
            elif "mute" in low:
                kwargs["action"] = "mute"
            elif "unmute" in low:
                kwargs["action"] = "unmute"
            elif "lock" in low:
                kwargs["action"] = "lock"
            elif "sleep" in low:
                kwargs["action"] = "sleep"
            elif "shutdown" in low:
                kwargs["action"] = "shutdown"
            elif "restart" in low:
                kwargs["action"] = "restart"
        kwargs.update(override_kwargs)
        self.logger.info("Running %s with %s", tool.name, kwargs)
        result = tool.execute(**kwargs)
        try:
            from modules.perf import record as perf_record
            perf_record("tool.run", start=t0, end=time.perf_counter(), stage="tool", tool=tool.name, success=result.success)
        except Exception:
            pass
        return result
