from __future__ import annotations

from pathlib import Path
from typing import Any
from modules.config import JarvisConfig
from modules.logger import get_logger

logger = get_logger("vision")


class VisionModule:
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self.logger = get_logger("vision")
    
    def capture_screenshot(self) -> Path:
        """Take a screenshot using desktop automation via the tools layer without importing it."""
        # Import here to avoid circulars
        from modules.tools import DesktopControlTool
        from modules.voice import VoiceModule
        # Safe construction: we only need config
        tool = DesktopControlTool(self.config)
        res = tool.execute(action="screenshot")
        p = Path("logs") / "screenshot.png"
        if not p.exists():
            raise RuntimeError(f"Screenshot capture failed: {res.error or res.output}")
        self.logger.info("Screenshot saved: %s", p)
        return p
    
    def analyze_screenshot(self) -> str:
        path = self.capture_screenshot()
        try:
            return self._describe_image(path)
        except Exception as exc:
            return f"Screenshot saved at {path}. Vision description unavailable: {exc}"
    
    def _describe_image(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            return f"(screenshot {path.name}; unsupported format for vision)"
        try:
            import urllib.request, json
            data = urllib.request.urlopen("http://localhost:11434/api/tags").read().decode("utf-8")
            models = [m.get("name","") for m in json.loads(data).get("models", [])]
            vision_model = next((n for n in models if "llava" in n.lower()), None)
            if not vision_model:
                size = path.stat().st_size if path.exists() else -1
                return (
                    f"Screenshot ready at {path}. Size: {size} bytes. "
                    "Local vision model not available; install Ollama vision model later."
                )
            payload = json.dumps({
                "model": vision_model,
                "prompt": "Describe this image briefly and concretely.",
                "images": [self._b64(path)]
            }).encode("utf-8")
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            out = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
            text = json.loads(out).get("response", "").strip()
            if text:
                return f"Screenshot description: {text}"
            size = path.stat().st_size if path.exists() else -1
            return f"Screenshot ready at {path}. Size: {size} bytes. Model returned no description."
        except Exception as exc:
            size = path.stat().st_size if path.exists() else -1
            return f"Screenshot saved at {path}. Size: {size} bytes. Local vision unavailable: {exc}"
    
    @staticmethod
    def _b64(path: Path) -> str:
        import base64
        return base64.b64encode(path.read_bytes()).decode("utf-8")
