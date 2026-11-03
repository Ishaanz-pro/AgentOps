"""Split markdown policy documents into section-level chunks.

Each document has a small YAML-ish front-matter block (id, title, version...).
We split on level-2 headings so every chunk is one policy section with a
stable id like ``POL-001#2`` that the agent cites back as a source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H2 = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # "POL-001#1"
    doc_id: str        # "POL-001"
    doc_title: str
    section: str       # "1. Eligibility window"
    text: str
    meta: dict[str, str] = field(default_factory=dict)

    def as_source(self) -> str:
        return f"{self.doc_id} §{self.section}"


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, raw[m.end():]


def chunk_document(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    doc_id = meta.get("id", path.stem)
    title = meta.get("title", path.stem)

    matches = list(_H2.finditer(body))
    chunks: list[Chunk] = []
    if not matches:
        text = body.strip()
        if text:
            chunks.append(Chunk(f"{doc_id}#0", doc_id, title, "full", text, meta))
        return chunks

    # Preamble before first H2 (document title / intro) becomes chunk 0 if non-trivial.
    preamble = body[: matches[0].start()].strip()
    preamble = re.sub(r"^# .*$", "", preamble, flags=re.MULTILINE).strip()
    if len(preamble) > 40:
        chunks.append(Chunk(f"{doc_id}#0", doc_id, title, "intro", preamble, meta))

    for i, m in enumerate(matches, start=1):
        end = matches[i].start() if i < len(matches) else len(body)
        section_text = body[m.end():end].strip()
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{i}",
                doc_id=doc_id,
                doc_title=title,
                section=m.group(1).strip(),
                text=f"{title} — {m.group(1).strip()}\n{section_text}",
                meta=meta,
            )
        )
    return chunks


def load_policy_chunks(policy_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(policy_dir).glob("*.md")):
        chunks.extend(chunk_document(path))
    return chunks
