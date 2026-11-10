"""Task 3:RagIndex 检索测试(用 FakeEmbedder + 内存 Chroma,不依赖 GPU/模型下载)。"""
import pytest

from app.rag import FakeEmbedder, RagIndex
from knowledge.chunker import Chunk


@pytest.fixture()
def index():
    rag = RagIndex(persist_dir=None, embedder=FakeEmbedder(dim=128))
    rag.add_documents([
        Chunk(text="睡前少看手机,可以做深呼吸放松,帮助入睡。",
              section="睡眠", source="sleep.md", category="情绪管理"),
        Chunk(text="每天散步半小时有助于稳定情绪,缓解压力。",
              section="运动", source="exercise.md", category="情绪管理"),
        Chunk(text="考试前把大任务拆成小步骤,可以减少焦虑。",
              section="考试焦虑", source="study.md", category="学业压力"),
        Chunk(text="与室友发生矛盾时,先表达感受再提请求,比指责更有效。",
              section="宿舍关系", source="relations.md", category="人际交往"),
        Chunk(text="当出现伤害自己的念头时,请立即拨打心理援助热线 12356。",
              section="紧急求助", source="crisis.md", category="危机干预与求助"),
    ])
    return rag


def test_add_and_search_returns_chunk(index):
    """查询与库内文本一致的内容时,返回带元数据且 score ≥ 阈值的块。

    (FakeEmbedder 是字符计数向量,只保证同文本高分;释义级召回
    由真实 bge-m3 的建库冒烟测试覆盖。)
    """
    results = index.search("睡前少看手机,可以做深呼吸放松,帮助入睡。", k=4, threshold=0.35)
    assert results, "应有命中"
    top = results[0]
    assert top.score >= 0.35
    assert top.section == "睡眠"
    assert top.source == "sleep.md"
    assert isinstance(top.text, str) and top.text


def test_search_below_threshold_returns_empty(index):
    """查询与库内容无关的文本时,低于阈值不引用,返回空列表。"""
    results = index.search("量子力学薛定谔方程推导过程", k=4, threshold=0.35)
    assert results == []


def test_dedup_by_source(index):
    """同一 source 命中多块时,每个 source 只保留最高分的一条。"""
    index.add_documents([
        Chunk(text="睡前少看手机,可以做深呼吸放松,帮助入睡。",
              section="睡眠A", source="sleep.md", category="情绪管理"),
        Chunk(text="睡前少看手机,可以做深呼吸放松,帮助入睡。",
              section="睡眠B", source="sleep.md", category="情绪管理"),
    ])
    results = index.search("睡前少看手机,可以做深呼吸放松,帮助入睡。", k=4, threshold=0.35)
    sources = [r.source for r in results]
    assert "sleep.md" in sources
    assert len(sources) == len(set(sources)), "每个 source 至多一条"
