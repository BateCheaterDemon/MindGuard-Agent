#!/usr/bin/env python
"""把 knowledge/raw/ 下的 markdown 文档切分并建入 Chroma 向量库(可重复执行,幂等 upsert)。

用法:
  python knowledge/build_chroma.py [--docs-dir knowledge/raw] \
      [--persist-dir data/chroma] [--embed bge-m3|bge-small-zh-v1.5|fake] [--device auto]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根:knowledge 包与 backend 均可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.rag import FakeEmbedder, RagIndex, TransformersEmbedder  # noqa: E402
from chunker import chunk_markdown, iter_md_files  # noqa: E402


def _strip_frontmatter(text: str) -> str:
    end = text.find("\n---", 3)
    return text[end + 4:] if end != -1 else text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs-dir", default="knowledge/raw")
    ap.add_argument("--persist-dir", default="data/chroma")
    ap.add_argument("--embed", default="bge-m3",
                    choices=["bge-m3", "bge-small-zh-v1.5", "fake"])
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    t0 = time.time()
    print(f"embedding 模型: {args.embed}(device={args.device})")
    if args.embed == "fake":
        embedder = FakeEmbedder(dim=128)
    else:
        embedder = TransformersEmbedder(model_name=args.embed, device=args.device)
    index = RagIndex(persist_dir=Path(args.persist_dir), embedder=embedder)

    total = 0
    for path, meta in iter_md_files(Path(args.docs_dir)):
        body = _strip_frontmatter(path.read_text(encoding="utf-8"))
        chunks = chunk_markdown(body, source=path.name, category=meta["category"])
        total += index.add_documents(chunks)
        print(f"  {path.name}: {len(chunks)} 块 · {meta.get('title', '')}")

    print(f"共入库 {total} 块,耗时 {time.time() - t0:.1f}s → {args.persist_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
