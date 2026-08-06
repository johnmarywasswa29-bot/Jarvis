"""Jarvis debug launcher for exposing startup exceptions.
Writes tracebacks to logs/startup_debug.log and reprints to stderr.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent if '__file__' in globals() else Path(sys.executable).parent
LOG_PATH = REPO / "logs" / "startup_debug.log"

def main() -> int:
    try:
        sys.path.insert(0, str(REPO))
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(LOG_PATH),
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)s %(name)s %(message)s',
        )
        from modules.config import JarvisConfig
        setup_logger = __import__('modules.logger', fromlist=['setup_logger']).setup_logger
        setup_logger(REPO / "logs")
        logging.getLogger("jarvis.debuglauncher").info("Debug launcher started")
        from modules.voice import VoiceModule
        from modules.brain_graph import JarvisBrain
        from modules.memory import JarvisMemory
        from modules.tools import ToolRegistry
        from modules.vision import VisionModule
        from modules.permission_manager import PermissionManager
        config = JarvisConfig.from_yaml(REPO / "config.yaml")
        perms = PermissionManager()
        tools = ToolRegistry(config)
        tools.set_permissions(perms)
        voice = VoiceModule(config)
        memory = JarvisMemory(config)
        brain = JarvisBrain(config, tools, memory)
        vision = VisionModule(config)
        logging.getLogger("jarvis.debuglauncher").info("Init complete; starting UI")
        from PySide6.QtWidgets import QApplication
        from ui.main_window import JarvisWindow
        app = QApplication(sys.argv)
        logging.getLogger("jarvis.debuglauncher").info("before win.show()")
        win = JarvisWindow(config=config, voice=voice, brain=brain, memory=memory, vision=vision, permissions=perms, tools=tools)
        win.show()
        logging.getLogger("jarvis.debuglauncher").info("after win.show() visible=%s", win.isVisible())
        logging.getLogger("jarvis.debuglauncher").info("before app.exec()")
        code = app.exec()
        logging.getLogger("jarvis.debuglauncher").info("after app.exec() code=%s", code)
        logging.getLogger("jarvis.debuglauncher").info("UI closed with code=%s", code)
        return code
    except Exception as exc:
        msg = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(msg, encoding='utf-8', errors='replace')
        except Exception:
            pass
        sys.stderr.write(msg)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
