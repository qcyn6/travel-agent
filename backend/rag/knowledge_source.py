"""Markdown-backed knowledge sources for ordinary-chat topic RAG."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import quote

from backend.rag.guide_files import guide_paths


class KnowledgeSourceError(RuntimeError):
    """Raised when the local RAG source cannot be loaded."""


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One independently retrievable knowledge block."""

    doc_id: str
    entity_type: str
    name: str
    province: str
    region_level: str
    source_file: str
    content: str
    keywords: tuple[str, ...] = ()

    def index_text(self) -> str:
        return "\n".join(
            part
            for part in (self.name, self.province, self.content)
            if part and part.strip()
        )


_GUIDE_SECTION_HEADING = re.compile(r"^## (?P<name>.+?)\s*$")
_KEYWORD_METADATA = re.compile(r"^关键词\s*[:：]\s*(?P<value>.+?)\s*$")
_OTHER_METADATA = re.compile(r"^(主题|适用范围)\s*[:：]")


class TravelGuideKnowledgeSource:
    """Load topic guides as independently retrievable Markdown sections."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def list_documents(self) -> list[KnowledgeChunk]:
        if not self.directory.is_dir():
            raise KnowledgeSourceError(f"主题知识库目录不存在：{self.directory}")

        documents: list[KnowledgeChunk] = []
        for path in guide_paths(self.directory):
            documents.extend(self._parse_file(path))
        return documents

    def retrieval_keywords(self) -> tuple[str, ...]:
        """Return the unique retrieval triggers declared by guide documents."""

        keywords: list[str] = []
        for document in self.list_documents():
            keywords.extend(document.keywords)
        return _unique(keywords)

    @staticmethod
    def _parse_file(path: Path) -> list[KnowledgeChunk]:
        lines = path.read_text(encoding="utf-8").splitlines()
        title = next(
            (line[2:].strip() for line in lines if line.startswith("# ")),
            path.stem,
        )
        metadata: list[str] = []
        keywords: list[str] = []
        body_start = 0
        for index, line in enumerate(lines):
            if _GUIDE_SECTION_HEADING.match(line):
                body_start = index
                break
            stripped = line.strip()
            keyword_match = _KEYWORD_METADATA.match(stripped)
            if keyword_match:
                metadata.append(stripped)
                keywords.extend(_split_keywords(keyword_match.group("value")))
            elif _OTHER_METADATA.match(stripped):
                metadata.append(stripped)

        sections: list[tuple[str, list[str]]] = []
        current_name: str | None = None
        current_lines: list[str] = []
        for line in lines[body_start:]:
            match = _GUIDE_SECTION_HEADING.match(line)
            if match:
                if current_name is not None:
                    sections.append((current_name, current_lines))
                current_name = match.group("name").strip()
                current_lines = []
                continue
            if current_name is not None:
                current_lines.append(line)
        if current_name is not None:
            sections.append((current_name, current_lines))
        # Imported guides need not use our section template. Keep their full
        # text retrievable instead of silently producing zero chunks.
        if not sections and any(line.strip() for line in lines):
            sections.append((title, lines))

        legacy_slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", path.stem).strip("-")
        # Retain IDs for ordinary existing filenames. Names that lost any
        # characters under the old normalization use a reversible, versioned
        # full filename, so e.g. "a b.md" can never collide with "a-b.md".
        document_key = (
            path.stem
            if legacy_slug and legacy_slug == path.stem and path.suffix == ".md"
            else f"v2:{quote(path.name, safe='')}"
        )
        documents: list[KnowledgeChunk] = []
        for index, (section_name, section_lines) in enumerate(sections, start=1):
            content_parts = [
                *metadata,
                f"章节：{section_name}",
                "\n".join(section_lines).strip(),
            ]
            content = "\n".join(part for part in content_parts if part).strip()
            if not content:
                continue
            documents.append(
                KnowledgeChunk(
                    doc_id=f"guide:{document_key}:{index:02d}",
                    entity_type="guide",
                    name=title,
                    province="全国",
                    region_level="guide",
                    source_file=path.name,
                    content=content,
                    keywords=_unique(keywords),
                )
            )
        return documents


def _split_keywords(value: str) -> tuple[str, ...]:
    return _unique(re.split(r"[、,，；;|/]+", value))


def _unique(values) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return tuple(result)


__all__ = [
    "KnowledgeChunk",
    "KnowledgeSourceError",
    "TravelGuideKnowledgeSource",
]
