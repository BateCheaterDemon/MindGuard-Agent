#!/usr/bin/env python
"""校验 knowledge/raw/ 下的知识文档:frontmatter、类别、字数、小节结构。

用法:python knowledge/validate_docs.py [docs_dir]
全部通过退出码 0,否则打印问题并退出码 1。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chunker import iter_md_files, parse_frontmatter  # noqa: E402

ALLOWED_CATEGORIES = {"情绪管理", "人际交往", "学业压力", "常见心理困扰", "危机干预与求助"}
MIN_BODY_CHARS = 300
MAX_BODY_CHARS = 1500


def main() -> int:
    docs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "raw"
    problems: list[str] = []
    count = 0
    for path, meta in iter_md_files(docs_dir):
        count += 1
        body = path.read_text(encoding="utf-8")
        for key in ("title", "source", "category"):
            if not meta.get(key):
                problems.append(f"{path.name}: frontmatter 缺少 {key}")
        if meta.get("category") not in ALLOWED_CATEGORIES:
            problems.append(
                f"{path.name}: 类别 {meta.get('category')!r} 不在 {sorted(ALLOWED_CATEGORIES)}")
        # 字数按去掉 frontmatter 后的正文计
        try:
            raw = body.lstrip("﻿").lstrip()
            end = raw.find("\n---", 3)
            body_text = raw[end + 4:] if end != -1 else ""
        except ValueError:
            body_text = ""
        chars = len(body_text.strip())
        if chars < MIN_BODY_CHARS:
            problems.append(f"{path.name}: 正文 {chars} 字,少于 {MIN_BODY_CHARS}")
        if chars > MAX_BODY_CHARS:
            problems.append(f"{path.name}: 正文 {chars} 字,超过 {MAX_BODY_CHARS}")
        if "## " not in body_text:
            problems.append(f"{path.name}: 正文缺少 ## 小节")

    print(f"检查 {count} 篇文档")
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
