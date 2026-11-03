"""Citation integrity: every policy reference in an answer must have been retrieved.

A model can cite a section it never saw (hallucinated provenance). This check
runs after every turn: any ``POL-001`` / ``POL-001 §2`` style reference in the
answer that does not correspond to a passage actually returned by
``search_policy_documents`` in THIS turn is reported as unverified.

It checks provenance, not truth: a retrieved section can still be misquoted.
"""

from __future__ import annotations

import re

_CITE = re.compile(r"\b((?:POL|FAQ)-[A-Z0-9]+)(?:\s*§\s*(\d+))?")
_SOURCE = re.compile(r"^((?:POL|FAQ)-[A-Z0-9]+)\s*§\s*(\d+)")


def _retrieved_index(retrieved_sources: list[str]) -> tuple[set[str], set[tuple[str, str]]]:
    docs: set[str] = set()
    sections: set[tuple[str, str]] = set()
    for s in retrieved_sources:
        m = _SOURCE.match(s)
        if m:
            docs.add(m.group(1))
            sections.add((m.group(1), m.group(2)))
        else:
            doc = s.split(" ")[0].split("§")[0].strip()
            if doc:
                docs.add(doc)
    return docs, sections


def unverified_citations(
    answer: str, retrieved_sources: list[str], retrieved_texts: list[str] | tuple[str, ...] = ()
) -> list[str]:
    """Return citations in ``answer`` that were not retrieved this turn (sorted, de-duplicated).

    - ``POL-001 §2`` (section level) must match a retrieved section exactly.
    - ``POL-004`` (document level) is accepted if that document was retrieved OR is
      cross-referenced inside retrieved text (e.g. POL-001 says "see POL-004"), since
      quoting retrieved policy text legitimately repeats its cross-references.
    """
    docs, sections = _retrieved_index(retrieved_sources)
    for text in retrieved_texts:
        docs.update(m.group(1) for m in _CITE.finditer(text))
    bad: set[str] = set()
    for m in _CITE.finditer(answer):
        doc, num = m.group(1), m.group(2)
        if num is None:
            if doc not in docs:
                bad.add(doc)
        elif (doc, num) not in sections:
            bad.add(f"{doc} §{num}")
    return sorted(bad)
