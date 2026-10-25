"""chunking.py —— 文档分块算法核心。

把"解析产物还原出的块元素（ChunkElement）"按一定策略切分成适合向量检索的
分片（ChunkPiece）。支持两种策略：
- RECURSIVE：按内容长度使用 RecursiveCharacterTextSplitter 均匀切分，超长元素
  会被进一步拆分；适合纯文本、无明显标题结构的文档。
- MARKDOWN：按标题层级（#~######）切分章节，先按标题分段，再在段落内按长度
  打包/拆分，保证标题结构完整。
分片结果会保留页码、时间戳、Markdown 偏移与来源定位器等出处信息。
"""
from __future__ import annotations  # 延迟注解求值

import re  # 正则，用于识别 Markdown 标题
from dataclasses import dataclass, field  # 数据类定义
from enum import StrEnum  # 字符串枚举
from typing import Iterable  # 可迭代对象类型标注

from langchain_text_splitters import RecursiveCharacterTextSplitter  # 递归文本切分器


class ChunkStrategy(StrEnum):
    """分块策略枚举。"""
    RECURSIVE = "recursive"  # 按长度递归切分
    MARKDOWN = "markdown"  # 按 Markdown 标题结构切分


@dataclass(slots=True)
class ChunkElement:
    """单个文档元素：解析产物中一段最小内容单元（一段文本 / 一个标题等）。"""
    id: str  # 元素唯一标识
    text: str  # 元素文本
    page: int | None = None  # 所在页码
    kind: str = "text"  # 元素类型（text / heading / title 等）
    timestamp_start: float | None = None  # 起始时间戳（音视频类）
    timestamp_end: float | None = None  # 结束时间戳
    speaker: str | None = None  # 说话人（转写内容）
    markdown_start: int | None = None  # 在 Markdown 原文中的起始偏移
    markdown_end: int | None = None  # 在 Markdown 原文中的结束偏移
    source_locator: dict = field(default_factory=dict)  # 来源定位器（精确出处）


@dataclass(slots=True)
class ChunkPiece:
    """分块结果：由若干元素聚合或拆分得到的一个可检索分片。"""
    content: str  # 分片文本内容
    element_ids: list[str]  # 组成该分片的元素 id 列表
    heading_path: list[str] = field(default_factory=list)  # 标题层级路径
    page_start: int | None = None  # 起始页码
    page_end: int | None = None  # 结束页码
    timestamp_start: float | None = None  # 起始时间戳
    timestamp_end: float | None = None  # 结束时间戳
    markdown_start: int | None = None  # 在 Markdown 中的起始偏移
    markdown_end: int | None = None  # 在 Markdown 中的结束偏移
    source_locators: list[dict] = field(default_factory=list)  # 来源定位器列表


def _piece(elements: list[ChunkElement], heading_path: list[str] | None = None) -> ChunkPiece:
    """把一个元素列表聚合成一个 ChunkPiece。

    Args:
        elements: 待聚合成片的元素列表。
        heading_path: 这些元素所属的标题层级路径。

    Returns:
        聚合后的 ChunkPiece，页码范围/时间戳/偏移取各元素的最小~最大区间。
    """
    # 分别收集非空的页码、时间戳与 Markdown 偏移，用于计算各区间边界
    pages = [item.page for item in elements if item.page is not None]
    starts = [item.timestamp_start for item in elements if item.timestamp_start is not None]
    ends = [item.timestamp_end for item in elements if item.timestamp_end is not None]
    markdown_starts = [item.markdown_start for item in elements if item.markdown_start is not None]
    markdown_ends = [item.markdown_end for item in elements if item.markdown_end is not None]
    return ChunkPiece(
        content="\n\n".join(item.text for item in elements).strip(),  # 元素文本以双换行拼接
        element_ids=[item.id for item in elements],
        heading_path=list(heading_path or []),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        timestamp_start=min(starts) if starts else None,
        timestamp_end=max(ends) if ends else None,
        markdown_start=min(markdown_starts) if markdown_starts else None,
        markdown_end=max(markdown_ends) if markdown_ends else None,
        source_locators=[item.source_locator for item in elements if item.source_locator],
    )


def _split_long_element(
    element: ChunkElement,
    splitter: RecursiveCharacterTextSplitter,
    heading_path: list[str] | None = None,
) -> list[ChunkPiece]:
    """把单个超长元素拆成多个分片。

    Args:
        element: 需要拆分的元素（其文本长度超过 chunk_size）。
        splitter: 递归文本切分器，负责具体切分。
        heading_path: 元素所属的标题层级路径。

    Returns:
        拆分得到的分片列表；若拆分后为空则返回空列表。
    """
    texts = splitter.split_text(element.text)
    if not texts:
        return []
    result: list[ChunkPiece] = []
    previous_end = 0
    overlap = int(getattr(splitter, "_chunk_overlap", 0))  # 读取切分器的重叠量，用于偏移对齐
    for text in texts:
        # 在上一次结束位置附近（扣除重叠区）搜索当前文本片段的起始位置，以还原其在原文中的偏移
        search_start = max(0, previous_end - overlap)
        local_start = element.text.find(text, search_start)
        if local_start < 0:
            local_start = search_start
        previous_end = local_start + len(text)
        result.append(
            ChunkPiece(
                content=text,
                element_ids=[element.id],
                heading_path=list(heading_path or []),
                page_start=element.page,
                page_end=element.page,
                timestamp_start=element.timestamp_start,
                timestamp_end=element.timestamp_end,
                markdown_start=(element.markdown_start + local_start) if element.markdown_start is not None else None,
                markdown_end=(element.markdown_start + previous_end) if element.markdown_start is not None else None,
                source_locators=[element.source_locator] if element.source_locator else [],
            )
        )
    return result


def _pack_elements(
    elements: Iterable[ChunkElement],
    splitter: RecursiveCharacterTextSplitter,
    chunk_size: int,
    heading_path: list[str] | None = None,
) -> list[ChunkPiece]:
    """把元素按长度打包成若干分片，超长元素单独拆分。

    Args:
        elements: 待打包的元素序列。
        splitter: 递归切分器（用于处理超长元素）。
        chunk_size: 每个分片的最大长度。
        heading_path: 当前元素的标题层级路径。

    Returns:
        打包好的分片列表。
    """
    result: list[ChunkPiece] = []
    pending: list[ChunkElement] = []  # 正在累积、尚未达到上限的元素
    pending_length = 0  # 当前累积的文本长度
    for element in elements:
        text_length = len(element.text)
        separator_length = 2 if pending else 0  # 之前已有元素时需计入分隔符长度
        # 超过上限则先结算当前累积批次
        if pending and pending_length + separator_length + text_length > chunk_size:
            result.append(_piece(pending, heading_path))
            pending = []
            pending_length = 0
        if text_length > chunk_size:
            # 单个元素就超长：拆分成多个分片
            result.extend(_split_long_element(element, splitter, heading_path))
            continue
        pending.append(element)
        pending_length += separator_length + text_length
    if pending:
        result.append(_piece(pending, heading_path))
    return result


def _markdown_chunks(
    elements: list[ChunkElement],
    splitter: RecursiveCharacterTextSplitter,
    chunk_size: int,
) -> list[ChunkPiece]:
    """按 Markdown 标题结构切分：先按标题分段，再在段落内打包/拆分。

    Args:
        elements: 待切分的元素列表（含标题类元素）。
        splitter: 递归切分器（用于处理段落内超长元素）。
        chunk_size: 每个分片的最大长度。

    Returns:
        切分得到的分片列表，分片不跨标题边界。
    """
    result: list[ChunkPiece] = []
    heading_path: list[str] = []  # 当前章节的标题层级路径
    section: list[ChunkElement] = []  # 当前标题下累积的段落元素

    def flush() -> None:
        """把当前 section 打包为分片并清空，等待下一个标题。"""
        nonlocal section
        result.extend(_pack_elements(section, splitter, chunk_size, heading_path))
        section = []

    for element in elements:
        # 尝试将文本匹配为标题，如 “### 标题”
        match = re.match(r"^(#{1,6})\s+(.+)$", element.text.strip())
        # 遇到标题类元素则先结算上一段，再更新此后的标题路径
        if element.kind == "heading" or match:
            flush()
            if match:
                level = len(match.group(1))  # 标题级别
                title = match.group(2).strip()  # 标题文字
            else:
                level = 1
                title = element.text.lstrip("# ").strip()
            # 标题路径按层级截断后追加新标题
            heading_path = heading_path[: level - 1] + [title]
            continue
        section.append(element)  # 普通内容元素归入当前段落
    flush()
    return result


def chunk_elements(
    elements: list[ChunkElement],
    *,
    strategy: ChunkStrategy,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> list[ChunkPiece]:
    """对外公开的分块入口：按指定策略把元素切分成可检索分片。

    同时负责参数校验与切分器（含自适应分隔符）的构建。

    Args:
        elements: 待分块的元素列表。
        strategy: 分块策略（RECURSIVE / MARKDOWN）。
        chunk_size: 每个分片的最大字符数，默认 800。
        chunk_overlap: 相邻分片重叠字符数，默认 100。

    Returns:
        分块结果列表。

    Raises:
        ValueError: 当 chunk_size 不大于 0，或 chunk_overlap 越界时抛出。
    """
    # 参数校验：分块大小需为正，重叠量需在合法区间
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须大于等于 0 且小于 chunk_size")
    # 自适应分隔符，由粗到细避免破坏语义单元
    separators = ["\n\n", "\n", "。", "！", "？", ";", "；", "，", " ", ""]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators,
        keep_separator=True,
    )
    # 按策略分派：Markdown 结构优先，否则按长度均匀打包
    if strategy == ChunkStrategy.MARKDOWN:
        return _markdown_chunks(elements, splitter, chunk_size)
    return _pack_elements(elements, splitter, chunk_size)