"""Task 2:知识切分器测试。"""
import pytest

from chunker import chunk_markdown, parse_frontmatter


def test_heading_split():
    """按 ## 小节切分,块的 section 等于各自小节标题。"""
    text = "前言一句话。\n\n## 睡眠\n睡前少看手机,可以听点轻音乐。\n\n## 运动\n每天半小时散步有助于情绪稳定。\n"
    chunks = chunk_markdown(text, source="s.md", category="情绪管理")
    sections = [c.section for c in chunks]
    assert "睡眠" in sections
    assert "运动" in sections
    assert len(chunks) >= 2


def test_long_block_splits_at_sentence_boundary():
    """超长小节按句边界续切,每块不超过 max_chars,断点在句末。"""
    sentences = "这是第{0:02d}句测试内容,长度合适正好五十个字符左右。"
    text = "".join(sentences.format(i) for i in range(40))  # 约 2000 字
    chunks = chunk_markdown(text, source="s.md", category="常见心理困扰", max_chars=600)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 600
    for c in chunks:
        assert c.text.rstrip().endswith(("。", "!", "?"))


def test_overlap():
    """相邻块按 overlap_chars 重叠:后一块开头是前一块结尾的 100 字。"""
    text = "句子内容一二三。" * 200
    chunks = chunk_markdown(text, source="s.md", category="情绪管理",
                            max_chars=500, overlap_chars=100)
    assert len(chunks) > 1
    c1, c2 = chunks[0], chunks[1]
    assert c2.text.startswith(c1.text[-100:])


def test_metadata():
    """每个块的 source/category 与传入值一致。"""
    text = "# 大标题\n## 小节\n一些内容。\n"
    chunks = chunk_markdown(text, source="doc1.md", category="情绪管理")
    assert chunks, "应有至少一个块"
    for c in chunks:
        assert c.source == "doc1.md"
        assert c.category == "情绪管理"


def test_frontmatter_parse():
    """标准 frontmatter 解析出 title/source/category;缺失抛 ValueError。"""
    md = "---\ntitle: 测试文档\nsource: 公开来源\ncategory: 情绪管理\n---\n正文。"
    data = parse_frontmatter(md)
    assert data == {"title": "测试文档", "source": "公开来源", "category": "情绪管理"}
    with pytest.raises(ValueError):
        parse_frontmatter("没有前言。")
