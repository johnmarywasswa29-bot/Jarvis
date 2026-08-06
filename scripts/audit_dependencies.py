"""Generate a dependency audit report from scans."""

from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from scan_dependencies import build_catalog, render_markdown_report, scan_imports  # noqa: E402


def main() -> int:
    records = scan_imports()
    catalog = build_catalog(records)
    print(render_markdown_report(catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
