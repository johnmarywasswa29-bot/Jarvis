"""Calculator+ plugin."""
from __future__ import annotations

from typing import Any


class CalculatorPlus:
    name = "calculator_plus"
    version = "1.0.0"

    def __init__(self, api: Any = None) -> None:
        self.api = api
        self.history: list[str] = []

    def evaluate(self, expression: str) -> str:
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            self.history.append(f"{expression} = {result}")
            return str(result)
        except Exception as e:
            return f"Error: {e}"

    def last(self) -> list[str]:
        return list(self.history)
