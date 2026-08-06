"""Document parsing helpers for common text kinds."""
from __future__ import annotations

import re
from typing import Optional


class DocumentParser:
    """Basic structured-text parser: headings, code blocks, tables."""

    def parse(self, text: str, *, kind_hint: Optional[str] = None) -> dict:
        return {
            "kind": kind_hint or _guess_kind(text),
            "heading_blocks": _split_headings(text),
            "paragraphs": [p.strip() for p in text.split("\n\n") if p.strip()],
        }


def _guess_kind(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith("{") or lowered.startswith("["):
        return "json"
    if "```" in lowered or lowered.startswith("import ") or lowered.startswith("def "):
        return "code"
    if re.search(r"^(#+|-{3,}|\|.+\|)", lowered, re.M):
        return "markup"
    return "plain"


def _split_headings(text: str) -> list[str]:
    return re.findall(r"^(#{1,6})\s+(.+)$", text, flags=re.M)
