"""Tests for modules/tools.py RC1 security fixes."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(r"C:\Users\User NA\Desktop\jarvis")
sys.path.insert(0, str(REPO))

def test_calculator_rejects_dangerous_eval():
    from modules.tools import CalculatorTool
    tool = CalculatorTool()
    bad_exprs = [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "exec('print(1)')",
        "compile('1','<x>','eval')",
    ]
    for expr in bad_exprs:
        r = tool.execute(expr)
        assert not r.success, f"Calculator allowed dangerous expression: {expr}"


def test_calculator_accepts_safe_math():
    from modules.tools import CalculatorTool
    tool = CalculatorTool()
    cases = [
        ("2+2", "4"),
        ("3 * 4", "12"),
        ("2 ** 8", "256"),
        ("pow(2, 3)", "8"),
    ]
    for expr, expected in cases:
        r = tool.execute(expr)
        assert r.success, f"Calculator rejected safe expression: {expr} -> {r.error}"
        assert expected in r.output, f"unexpected output: {r.output}"


def test_filesystem_blocks_path_traversal():
    from modules.tools import FileSystemTool
    tool = FileSystemTool()
    outside = "C:\\Windows\\win.ini"
    r = tool.execute(action="read", source=outside)
    assert not r.success, f"FileSystemTool allowed outside path: {r}"


def test_desktop_control_blocks_shell_injection_fallback():
    from modules.tools import DesktopControlTool
    from modules.config import JarvisConfig
    cfg = JarvisConfig(project_root=REPO)
    tool = DesktopControlTool(cfg)
    r = tool.execute(action="open_app", target="del /q C:\\")
    assert not r.success, f"DesktopControlTool allowed dangerous target: {r}"