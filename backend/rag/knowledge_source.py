"""Markdown-backed province and city knowledge source for ordinary-chat RAG."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


class KnowledgeSourceError(RuntimeError):
    """Raised when the local RAG source cannot be loaded."""


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One independently retrievable province or city knowledge block."""

    doc_id: str
    entity_type: str
    name: str
    province: str
    region_level: str
    source_file: str
    content: str

    def index_text(self) -> str:
        return "\n".join(
            part
            for part in (self.name, self.province, self.content)
            if part and part.strip()
        )


_PROVINCE_HEADING = re.compile(r"^## (?P<code>\d{2})｜(?P<name>.+?)\s*$")
_CITY_HEADING = re.compile(r"^### (?P<code>\d{2}-\d{2})｜(?P<name>.+?)\s*$")


class MarkdownKnowledgeSource:
    """Load the project's province and city Markdown knowledge files.

    The parser deliberately uses the numbered headings as stable IDs. This
    keeps the vector IDs unchanged when a paragraph is edited and lets Chroma
    upsert changed content without creating duplicates.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def list_documents(self) -> list[KnowledgeChunk]:
        province_path = self.directory / "province_travel_knowledge.md"
        city_path = self.directory / "city_travel_knowledge.md"
        if not province_path.is_file() or not city_path.is_file():
            missing = [
                str(path)
                for path in (province_path, city_path)
                if not path.is_file()
            ]
            raise KnowledgeSourceError("知识库文件不存在：" + "、".join(missing))

        documents = self._parse_provinces(province_path)
        documents.extend(self._parse_cities(city_path))
        if not documents:
            raise KnowledgeSourceError("知识库文件中没有找到省份或城市知识块")
        return documents

    @staticmethod
    def _parse_provinces(path: Path) -> list[KnowledgeChunk]:
        lines = path.read_text(encoding="utf-8").splitlines()
        documents: list[KnowledgeChunk] = []
        for index, line in enumerate(lines):
            match = _PROVINCE_HEADING.match(line)
            if not match:
                continue
            end = _next_section_start(lines, index + 1, level=2)
            content = _body(lines[index + 1 : end])
            if not content:
                continue
            name = match.group("name").strip()
            documents.append(
                KnowledgeChunk(
                    doc_id=f"province:{match.group('code')}",
                    entity_type="province",
                    name=name,
                    province=name,
                    region_level="province",
                    source_file=path.name,
                    content=content,
                )
            )
        return documents

    @staticmethod
    def _parse_cities(path: Path) -> list[KnowledgeChunk]:
        lines = path.read_text(encoding="utf-8").splitlines()
        documents: list[KnowledgeChunk] = []
        current_province: str | None = None
        for index, line in enumerate(lines):
            province_match = _PROVINCE_HEADING.match(line)
            if province_match:
                current_province = province_match.group("name").strip()
                continue

            city_match = _CITY_HEADING.match(line)
            if not city_match or current_province is None:
                continue
            end = _next_section_start(lines, index + 1, level=3)
            content = _body(lines[index + 1 : end])
            if not content:
                continue
            documents.append(
                KnowledgeChunk(
                    doc_id=f"city:{city_match.group('code')}",
                    entity_type="city",
                    name=city_match.group("name").strip(),
                    province=current_province,
                    region_level="city",
                    source_file=path.name,
                    content=content,
                )
            )
        return documents


def _next_section_start(lines: list[str], start: int, *, level: int) -> int:
    prefix = "#" * level + " "
    for index in range(start, len(lines)):
        if lines[index].startswith(prefix):
            return index
    return len(lines)


def _body(lines: list[str]) -> str:
    content = "\n".join(lines).strip()
    return content.strip("- ").strip() if content == "-" else content


__all__ = ["KnowledgeChunk", "KnowledgeSourceError", "MarkdownKnowledgeSource"]
