"""test_chunking.py —— 文档分块算法（chunking）的单元测试。

覆盖递归切分（RECURSIVE）与按 Markdown 标题切分（MARKDOWN）两种策略，
验证分块是否完整保留元素、标题层级路径是否被正确承接、超长元素拆分后
Markdown 偏移是否连续准确等核心行为。
"""
from app.documents.chunking import ChunkElement, ChunkStrategy, chunk_elements


def test_recursive_chunking_preserves_every_source_element():
    """递归切分后每个源元素都应至少出现在一个分片中且内容非空。"""
    elements = [
        ChunkElement(id="e1", text="项目背景：推荐链路响应时间较长", page=1),
        ChunkElement(id="e2", text="本人职责：负责召回服务重构", page=1),
        ChunkElement(id="e3", text="项目结果：P95 延迟从 220ms 降至 95ms", page=1),
    ]

    chunks = chunk_elements(
        elements,
        strategy=ChunkStrategy.RECURSIVE,
        chunk_size=34,
        chunk_overlap=0,
    )

    # 收集所有分片包含的元素 id，验证三个元素都未被遗漏
    covered = {element_id for chunk in chunks for element_id in chunk.element_ids}
    assert covered == {"e1", "e2", "e3"}
    assert all(chunk.content for chunk in chunks)


def test_markdown_chunking_keeps_heading_path_as_parent_context():
    """Markdown 切分应把各级标题累计到 heading_path 作为父上下文。"""
    elements = [
        ChunkElement(id="e1", text="# 工作经历", page=1, kind="heading"),
        ChunkElement(id="e2", text="## 星云科技", page=1, kind="heading"),
        ChunkElement(id="e3", text="负责推荐系统升级", page=1),
    ]

    chunks = chunk_elements(
        elements,
        strategy=ChunkStrategy.MARKDOWN,
        chunk_size=100,
        chunk_overlap=0,
    )

    # 末尾内容分片应承接完整的标题层级
    assert chunks[-1].heading_path == ["工作经历", "星云科技"]
    assert chunks[-1].element_ids == ["e3"]


def test_long_markdown_section_keeps_heading_path_after_recursive_split():
    """超长 Markdown 章节被递归拆分后，每个子分片仍应保留标题层级。"""
    elements = [
        ChunkElement(id="e1", text="# 项目经历", kind="heading"),
        ChunkElement(id="e2", text="负责推荐系统升级。" * 30),
    ]

    chunks = chunk_elements(
        elements,
        strategy=ChunkStrategy.MARKDOWN,
        chunk_size=100,
        chunk_overlap=10,
    )

    assert len(chunks) > 1
    assert all(chunk.heading_path == ["项目经历"] for chunk in chunks)


def test_long_markdown_element_refines_markdown_offsets_for_each_child():
    """超长元素拆分后，各子分片 Markdown 偏移应连续且覆盖原区间。"""
    text = "负责推荐系统升级。" * 30
    elements = [
        ChunkElement(id="e1", text="# 项目经历", kind="heading"),
        ChunkElement(
            id="e2",
            text=text,
            markdown_start=100,
            markdown_end=100 + len(text),
        ),
    ]

    chunks = chunk_elements(elements, strategy=ChunkStrategy.MARKDOWN, chunk_size=80, chunk_overlap=10)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.markdown_end - chunk.markdown_start == len(chunk.content)
        assert chunk.source_locators == []

def test_recursive_chunking_for_plain_text_keeps_empty_heading_path():
    """纯文本递归切分时无标题，各分片 heading_path 应为空列表。"""
    elements = [
        ChunkElement(id="e1", text="第一段连续转录文本"),
        ChunkElement(id="e2", text="第二段连续转录文本"),
    ]

    chunks = chunk_elements(
        elements,
        strategy=ChunkStrategy.RECURSIVE,
        chunk_size=30,
        chunk_overlap=0,
    )

    covered = {element_id for chunk in chunks for element_id in chunk.element_ids}
    assert covered == {"e1", "e2"}
    assert all(chunk.heading_path == [] for chunk in chunks)