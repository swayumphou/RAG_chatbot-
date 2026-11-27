"""Check the citations in an LLM answer. Deliberately simple and strict.

An answer is valid only if:
- it has at least one citation like [Project: <title>, p.<n>], and
- every (title, page) it cites was really retrieved.

A model never checks another model's citations in this system.
"""

from __future__ import annotations

import re

CITE_RE = re.compile(r"\[Project:\s*(.+?),\s*p\.(\d+)\]")


def validate_citations(text: str, citations) -> bool:
    """True when the text cites at least one project and every cited (title, page) was retrieved."""
    allowed = {(c.title, str(c.page_number)) for c in citations}
    found = {(title.strip(), page) for title, page in CITE_RE.findall(text or "")}
    if not found:
        return False  # no citation at all is invalid
    return found <= allowed
