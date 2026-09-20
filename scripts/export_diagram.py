"""Export the README's Mermaid architecture diagram to docs/architecture.svg.

Renders the first ```mermaid block of README.md with mermaid.js in headless Chromium
(Playwright, not a project dependency: any interpreter that has it). No mermaid-cli needed.

    python scripts/export_diagram.py            # README.md -> docs/architecture.svg
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"


def main() -> int:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"```mermaid\n(.*?)\n```", readme, re.S)
    if not m:
        print("no ```mermaid block in README.md", file=sys.stderr)
        return 1
    diagram = m.group(1)
    html = (f'<html><body><script src="{MERMAID_JS}"></script>'
            f'<pre class="mermaid">{diagram}</pre>'
            '<script>mermaid.initialize({startOnLoad: true, theme: "neutral"});</script>'
            "</body></html>")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.wait_for_selector("pre.mermaid svg", timeout=60_000)
        # XMLSerializer, not innerHTML: HTML labels contain <br>, which innerHTML leaves
        # unclosed (fine in a page, invalid as a standalone .svg / on GitHub).
        svg = page.evaluate(
            "new XMLSerializer().serializeToString(document.querySelector('pre.mermaid svg'))"
        )
        browser.close()
    out = ROOT / "docs" / "architecture.svg"
    out.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(svg) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
