"""知识库 markdown 切分器。

策略(spec §5.3):
- 标题感知:按 `##` 行切成小节,节标题入元数据 section;
- 节内超长按句边界(。!?;!?;及换行)续切,块目标 400~800 字(默认 600);
- 同节相邻块重叠 80~120 字(默认 100),跨节不重叠;
- 每块文本带小节标题前缀,便于检索时自包含。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_SENTENCE_ENDERS = "。!?;!?;\n"
_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)


@dataclass
class Chunk:
    text: str
    section: str
    source: str
    category: str


def _split_sentences(body: str) -> list[str]:
    """按句末符切分,句末符保留在句尾。"""
    pieces: list[str] = []
    current = ""
    for ch in body:
        current += ch
        if ch in _SENTENCE_ENDERS:
            if current.strip():
                pieces.append(current)
            current = ""
    if current.strip():
        pieces.append(current)
    return pieces


def _pack(pieces: list[str], budget: int, overlap_chars: int) -> list[str]:
    """把句子片段贪婪装进 ≤budget 的块;块间重叠取前块结尾 overlap_chars 字。

    单个片段超过 budget 时按字符硬切(罕见:一句超过块上限)。
    """
    units: list[str] = []
    current = ""

    def drain() -> None:
        nonlocal current
        while len(current) > budget:
            units.append(current[:budget])
            current = current[budget:]

    for piece in pieces:
        if not current:
            current = piece
            drain()
            continue
        if len(current) + len(piece) <= budget:
            current += piece
        else:
            units.append(current)
            current = current[-overlap_chars:] + piece
            drain()
    if current:
        units.append(current)
    return units


def chunk_markdown(
    text: str,
    *,
    source: str,
    category: str,
    max_chars: int = 600,
    overlap_chars: int = 100,
) -> list[Chunk]:
    """把一篇 markdown 正文切成 Chunk 列表(不含 frontmatter)。"""
    parts = _HEADING_RE.split(text)
    # parts 交替:[正文, 标题, 正文, 标题, ...];首段为第一个标题前的正文
    chunks: list[Chunk] = []
    preamble = parts[0]
    sections: list[tuple[str | None, str]] = [(None, preamble)]
    for i in range(1, len(parts), 2):
        title = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append((title, body))

    for title, body in sections:
        if not body.strip():
            continue
        section = title or "开头"
        heading_prefix = f"## {title}\n" if title else ""
        budget = max_chars - len(heading_prefix)
        if budget < 100:
            budget = max_chars  # 标题异常长时兜底,不产生零容量块
        for unit in _pack(_split_sentences(body), budget, overlap_chars):
            chunks.append(Chunk(
                text=heading_prefix + unit,
                section=section,
                source=source,
                category=category,
            ))
    return chunks


def parse_frontmatter(text: str) -> dict:
    """解析 `---` 包围的 frontmatter;缺失或未闭合抛 ValueError。"""
    text = text.lstrip("﻿").lstrip()
    if not text.startswith("---"):
        raise ValueError("文档缺少 frontmatter(需以 --- 开头)")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("frontmatter 未闭合")
    data: dict = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip("'\"")
    return data


def iter_md_files(docs_dir: Path) -> Iterator[tuple[Path, dict]]:
    """遍历目录下所有 .md,产出 (路径, frontmatter dict);缺失 frontmatter 抛 ValueError。"""
    for path in sorted(docs_dir.glob("*.md")):
        yield path, parse_frontmatter(path.read_text(encoding="utf-8"))
