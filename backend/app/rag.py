"""Chroma 向量检索:embedding + 入库 + 检索(spec §5.3)。

embedding 用 transformers 直接加载 bge 系列(mean pooling + L2 归一化),
不引入 sentence-transformers,避免与 transformers 5.10.4 的 pin 冲突;
模型经 ModelScope 下载/缓存。测试用 FakeEmbedder(字符计数向量),
不依赖 GPU 与模型下载。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import chromadb

from knowledge.chunker import Chunk

MODELSCOPE_IDS = {
    "bge-m3": "BAAI/bge-m3",
    "bge-small-zh-v1.5": "AI-ModelScope/bge-small-zh-v1.5",
}


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """测试用确定性向量:字符计数 + L2 归一化。

    同文本 → 余弦 1.0;共享字符多的相关文本 → 中等相似度;
    无关文本 → 接近 0。让阈值过滤逻辑可被真实地测出来。
    """

    def __init__(self, dim: int = 128):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            counts = [0.0] * self.dim
            for ch in text:
                counts[ord(ch) % self.dim] += 1.0
            norm = math.sqrt(sum(c * c for c in counts)) or 1.0
            vectors.append([c / norm for c in counts])
        return vectors


class TransformersEmbedder:
    """bge 系列中文向量模型(经 ModelScope 下载),mean pooling + L2 归一化。"""

    def __init__(self, model_name: str = "bge-m3", device: str = "auto"):
        import torch  # 延迟导入:仅真实建库/服务时才有 GPU 需求
        from modelscope import snapshot_download
        from transformers import AutoModel, AutoTokenizer

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        model_dir = snapshot_download(MODELSCOPE_IDS[model_name])
        self._tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self._model = AutoModel.from_pretrained(model_dir).to(device).eval()
        if device.startswith("cuda"):
            self._model = self._model.half()  # fp16,显存减半(16GB 账本)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import torch

        inputs = self._tokenizer(
            texts, padding=True, truncation=True, max_length=512,
            return_tensors="pt").to(self._device)
        with torch.no_grad():
            hidden = self._model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, dim=1)
        return pooled.cpu().tolist()


@dataclass
class RetrievedChunk:
    source: str
    section: str
    category: str
    text: str
    score: float


class RagIndex:
    """Chroma 索引:persist_dir=None 时用内存库(测试),否则落盘。"""

    def __init__(self, persist_dir: Path | None, embedder: Embedder,
                 collection: str = "psych_kb"):
        self._embedder = embedder
        if persist_dir is None:
            self._client = chromadb.EphemeralClient()
        else:
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"})

    def add_documents(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        ids, docs, metas = [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(f"{chunk.source}#{chunk.section}#{i}")
            docs.append(chunk.text)
            metas.append({
                "source": chunk.source,
                "section": chunk.section,
                "category": chunk.category,
            })
        self._collection.upsert(
            ids=ids, documents=docs, metadatas=metas,
            embeddings=self._embedder.embed(docs))
        return len(chunks)

    def search(self, query: str, k: int = 4,
               threshold: float = 0.35) -> list[RetrievedChunk]:
        """检索 top-k;score = 1 - cosine 距离;低于阈值过滤;同 source 去重。"""
        if k <= 0:
            return []
        res = self._collection.query(
            query_embeddings=self._embedder.embed([query]), n_results=k)
        results: list[RetrievedChunk] = []
        seen_sources: set[str] = set()
        for doc_id, doc, meta, dist in zip(
                res["ids"][0], res["documents"][0],
                res["metadatas"][0], res["distances"][0]):
            score = 1.0 - float(dist)
            if score < threshold:
                continue
            meta = meta or {}
            source = meta.get("source", "")
            if source in seen_sources:
                continue
            seen_sources.add(source)
            results.append(RetrievedChunk(
                source=source,
                section=meta.get("section", ""),
                category=meta.get("category", ""),
                text=doc or "",
                score=score,
            ))
        return results
