"""
chunk_service.py —— 文档分块（Chunking）服务层。

本模块负责将解析好的文档（通常为 Markdown 文本 + 结构化 JSON 产物）转换为一组
带层级关系的分块（DocumentChunk），并把这些分块以"分块批次（ChunkingRun）"的
形式落库。核心能力包括：

1. 从解析产物中还原出有序的"块元素（ChunkElement）"列表，例如标题、正文段落、
   表格等；
2. 根据块元素是否包含标题，自动推断合适的分块策略（Markdown 结构分块或简单的
   递归均匀分块）；
3. 执行实际分块（chunk_elements），并构建"父块（heading 段落的聚合）→ 子块
   （具体的切分片段）"的树形关系；
4. 记录分块运行的状态（运行中 / 成功 / 失败）与错误信息，便于追踪与回滚。

说明：本文件只做"分块"，不涉及向量化或检索，那些部分在 retrieval 子包中实现。
"""
from __future__ import annotations  # 启用延迟注解求值，兼容新旧版 Python 类型标注

import hashlib  # 提供 SHA-256 等哈希算法，用于生成分块的稳定键（stable_key）
import json  # 提供 JSON 序列化/反序列化，用于读写结构化产物与位置定位器
import re  # 提供正则表达式，用于从 Markdown 中切分出文本块
from datetime import UTC, datetime  # 提供带时区的 UTC 时间，用于记录任务完成时间
from uuid import UUID  # UUID 类型标注，表示数据库主键

# SQLAlchemy 查询与 ORM 会话
from sqlalchemy import select
from sqlalchemy.orm import Session

# 分块算法与数据模型
from app.documents.chunking import ChunkElement, ChunkStrategy, chunk_elements
from app.db.models import (
    ChunkingRun,  # 一次分块运行记录
    ChunkingStatus,  # 分块运行状态枚举
    ChunkStrategyName,  # 分块策略名称（数据库枚举）
    Document,  # 文档（候选人上传的原始文档）
    DocumentChunk,  # 落库后的分块记录
    DocumentVersion,  # 文档的某个版本
    ParseArtifact,  # 一次解析任务产出的工件（Markdown / 结构化 JSON）
    ParseJob,  # 解析任务
    ParseStatus,  # 解析任务状态枚举
)
from app.integrations.object_store import ObjectStore  # 对象存储客户端，用于读取产物


def infer_chunk_strategy(elements: list[ChunkElement]) -> ChunkStrategy:
    """根据块元素是否包含标题，推断采用的分块策略。

    若存在元素的 kind 为 "heading"（意味着文档具备清晰的 Markdown 标题结构），
    则采用结构化的 MARKDOWN 分块；否则退化为按内容长度均匀切分的 RECURSIVE 分块。
    """
    return ChunkStrategy.MARKDOWN if any(element.kind == "heading" for element in elements) else ChunkStrategy.RECURSIVE


def parent_paths_for_heading(heading_path: list[str]) -> list[tuple[str, ...]]:
    """为单个标题路径生成其所有层级前缀（父路径）。

    例如标题路径 ["第1章", "第1节"] 会生成：
        ("第1章",)
        ("第1章", "第1节")
    这些前缀会被用作"父块"的定位键，从而把每一小节归并到它所属的大章节下。
    """
    return [tuple(heading_path[:index]) for index in range(1, len(heading_path) + 1)]


def content_element_ids(elements: list[ChunkElement]) -> list[str]:
    """收集所有"非标题"块元素的 id。

    由于标题元素本身不承载实质正文内容，在聚合正文时通常只需要内容元素的 id 列表。
    """
    return [element.id for element in elements if element.kind != "heading"]


def _source_locator(item: dict, *, page: int | None = None) -> dict:
    """从单一来源条目中提取"来源定位器（source_locator）"。

    定位器用于描述某段内容在原始文档中的精确位置，便于证据回溯。处理逻辑：
    1. 若条目本身已带 locator 且属于可用的定位类型（页面区域 / 幻灯片 / 时间区
       间），则原样返回；
    2. 否则根据是否提供页码，构造一个 page_region 定位器，并尽量带上 bbox（边界
       框）信息。
    """
    locator = dict(item.get("locator") or {})
    if locator.get("kind") in {"page_region", "slide", "slide_region", "time_range"}:
        return locator
    locator = {}
    bbox = item.get("bbox")
    if page is not None:
        locator = {"kind": "page_region", "page": page}
        if bbox is not None:
            locator["bbox"] = bbox
    return locator


def _markdown_blocks(markdown: str) -> list[tuple[str, int, int]]:
    """将整段 Markdown 文本按空行拆分为若干块，返回 (块内容, 起始偏移, 结束偏移)。

    采用正则匹配，每个块以空行分隔；同时记录该块在原文中的起始/结束字符偏移，
    以便后续在 Markdown 中定位精确出处。空文本块会被跳过。
    """
    blocks: list[tuple[str, int, int]] = []
    for match in re.finditer(r"(?:^|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", markdown, flags=re.DOTALL):
        raw = match.group(1)
        block = raw.strip()  # 去掉首尾空白
        if not block:
            continue  # 跳过空块
        start = match.start(1) + len(raw) - len(raw.lstrip())  # 计算首个非空白字符的偏移
        blocks.append((block, start, start + len(block)))
    return blocks


def elements_from_artifacts(markdown: str, structured: dict) -> list[ChunkElement]:
    """把解析产物（Markdown 与结构化数据）整合成有序的块元素列表。

    主要步骤：
    1. 若结构化数据没有 content_list，而是 segments（例如转写类内容），则先把
       segments 规整成 content_list 的结构；
    2. 当存在 Markdown 正文时，优先以 Markdown 为权威来源，把每个 Markdown 块与
       结构化条目按文本内容进行对齐匹配，命中则带上页码与定位器；
    3. 若没有 Markdown 正文，则直接以结构化内容条目逐一生成块元素，并维护一个
       递增的 markdown 游标用于估算偏移。
    """
    # 从结构化产物里取内容列表；没有 content_list 时检查是否有 segments
    content_list = structured.get("content_list") if isinstance(structured, dict) else None
    if not content_list and isinstance(structured, dict) and isinstance(structured.get("segments"), list):
        # 把语音转写的 segments 转成统一的内容条目结构
        content_list = [
            {
                "type": "text",
                "text": item.get("text", ""),
                "locator": {
                    "kind": "time_range",  # 定位类型：时间区间
                    "segment": index,  # 段落在转写中的序号
                    "speaker": item.get("speaker"),  # 说话人
                    "timestamp_start": item.get("start"),  # 起始时间戳
                    "timestamp_end": item.get("end"),  # 结束时间戳
                },
            }
            for index, item in enumerate(structured["segments"], start=1)
        ]
    # 收集所有非空的来源条目，规整成 (序号, 条目, 文本) 三元组
    source_items: list[tuple[int, dict, str]] = []
    if isinstance(content_list, list):
        for index, item in enumerate(content_list, start=1):
            if not isinstance(item, dict):
                continue  # 容错：跳过非字典条目
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                source_items.append((index, item, text))

    if markdown.strip():
        # ---- 首选路径：以 Markdown 为权威 ----
        blocks = _markdown_blocks(markdown)
        used_sources: set[int] = set()  # 记录已被匹配用掉的来源条目序号
        result: list[ChunkElement] = []
        for block_index, (block, markdown_start, markdown_end) in enumerate(blocks, start=1):
            # 去掉 Markdown 标题前缀（# 号），用于与结构化文本做对齐匹配
            normalized_block = re.sub(r"^#{1,6}\s+", "", block).strip()
            # 找到首个文本内容一致的未使用来源条目（忽略空白差异）
            source = next(
                (
                    (index, item)
                    for index, item, text in source_items
                    if index not in used_sources and re.sub(r"\s+", " ", text) == re.sub(r"\s+", " ", normalized_block)
                ),
                None,
            )
            if source:
                source_index, source_item = source
                used_sources.add(source_index)  # 标记该来源已被使用
                # 页码：结构化中 page_idx 从 0 开始，转成 1-based 页码
                page = int(source_item["page_idx"]) + 1 if source_item.get("page_idx") is not None else None
                element_id = f"p{page}-e{source_index}" if page is not None else f"md-e{block_index}"
            else:
                # 未匹配到结构化条目，仅保留 Markdown 信息
                source_item = {}
                page = None
                element_id = f"md-e{block_index}"
            # 判断是否为标题块（Markdown 前缀是 #，或结构化类型为 title/heading）
            is_heading = bool(re.match(r"^#{1,6}\s+", block)) or source_item.get("type") in {"title", "heading"}
            locator = _source_locator(source_item, page=page)
            speaker = locator.get("speaker") or source_item.get("speaker")
            result.append(
                ChunkElement(
                    id=element_id,
                    text=block,
                    page=page,
                    kind="heading" if is_heading else str(source_item.get("type") or "text"),
                    timestamp_start=locator.get("timestamp_start"),
                    timestamp_end=locator.get("timestamp_end"),
                    speaker=str(speaker) if speaker else None,
                    markdown_start=markdown_start,
                    markdown_end=markdown_end,
                    source_locator=locator,
                )
            )
        return result

    # ---- 备用路径：没有 Markdown，直接使用结构化条目 ----
    result = []
    markdown_cursor = 0  # 用于估算每个条目在伪 Markdown 中的字符偏移
    for index, item, text in source_items:
        page = int(item["page_idx"]) + 1 if item.get("page_idx") is not None else None
        kind = "heading" if item.get("type") in {"title", "heading"} else str(item.get("type") or "text")
        locator = _source_locator(item, page=page)
        speaker = locator.get("speaker") or item.get("speaker")
        result.append(
            ChunkElement(
                id=f"p{page}-e{index}" if page is not None else f"src-e{index}",
                text=text,
                page=page,
                kind=kind,
                timestamp_start=locator.get("timestamp_start"),
                timestamp_end=locator.get("timestamp_end"),
                speaker=str(speaker) if speaker else None,
                markdown_start=markdown_cursor,
                markdown_end=markdown_cursor + len(text),  # 估算偏移（含分隔符）
                source_locator=locator,
            )
        )
        markdown_cursor += len(text) + 2  # 每段之间预留两个字符（如换行）
    return result


def _load_parse_input(db: Session, store: ObjectStore, version_id: UUID) -> tuple[ParseJob, str, dict]:
    """加载某文档版本最新的成功解析任务及其产物。

    返回 (解析任务, Markdown 文本, 结构化字典)。若没有成功解析任务或没有任何
    可用产物（既无 Markdown 也无结构化数据），则抛出 ValueError，交由上层处理。
    """
    # 取该版本最近一次“成功”的解析任务
    job = db.scalar(
        select(ParseJob)
        .where(ParseJob.document_version_id == version_id, ParseJob.status == ParseStatus.SUCCEEDED)
        .order_by(ParseJob.created_at.desc())
    )
    if not job:
        raise ValueError("文档尚无成功的解析任务")  # 没有成功解析，无法分块
    # 取出该解析任务产出的所有工件
    artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.parse_job_id == job.id)).all()
    markdown = ""
    structured: dict = {}
    for artifact in artifacts:
        body = store.get_bytes(artifact.object_key)  # 从对象存储读取产物原文
        if artifact.artifact_type == "markdown":
            markdown = body.decode("utf-8", errors="replace")
        elif artifact.artifact_type == "structured_json":
            structured = json.loads(body.decode("utf-8"))
    if not markdown and not structured:
        raise ValueError("解析任务没有可用的 Markdown 或结构化产物")
    return job, markdown, structured


def create_chunking_run(
    db: Session,
    store: ObjectStore,
    document: Document,
    version: DocumentVersion,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> ChunkingRun:
    """创建并执行一次分块任务，返回对应的 ChunkingRun 记录。

    参数说明：
    - db: 数据库会话；
    - store: 对象存储，用于读取解析产物；
    - document / version: 待分块的文档及其版本；
    - chunk_size: 每个子块的目标长度；
    - chunk_overlap: 相邻子块之间的重叠长度。

    流程：读取解析产物 → 还原块元素 → 推断策略 → 记录运行状态”运行中“ →
    执行分块并构建父子块链条（同时维护前后相邻指针）→ 成功后提交；任何异常都会
    把该运行标记为失败并记录错误信息后重新抛出。
    """
    # 加载成功解析任务及其产物
    parse_job, markdown, structured = _load_parse_input(db, store, version.id)
    elements = elements_from_artifacts(markdown, structured)  # 还原块元素
    strategy = infer_chunk_strategy(elements)  # 推断分块策略
    # 创建运行记录（初始为“运行中”）
    run = ChunkingRun(
        parse_job_id=parse_job.id,
        strategy=ChunkStrategyName(strategy.value),
        status=ChunkingStatus.RUNNING,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    db.add(run)
    db.flush()  # 立即落库以拿到 run.id
    try:
        # 执行实际分块，得到有序的分片（piece）列表
        pieces = chunk_elements(elements, strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        children: list[DocumentChunk] = []  # 所有子块（用于维护链式前后指针）
        parent_by_path: dict[tuple[str, ...], DocumentChunk] = {}  # 路径 → 已创建的父块
        for position, piece in enumerate(pieces):
            parent = None  # 该分片的最终父块
            parents_for_piece: list[DocumentChunk] = []  # 该分片沿途经过的所有父块
            # 按标题层级逐级创建/复用父块
            for path_key in parent_paths_for_heading(piece.heading_path):
                current_parent = parent_by_path.get(path_key)
                if current_parent is None:
                    # 该层级父块还不存在，则创建一个空的“父块”占位记录
                    current_parent = DocumentChunk(
                        chunking_run_id=run.id,
                        document_version_id=version.id,
                        candidate_id=document.candidate_id,
                        document_type=document.document_type,
                        permission_scope=document.permission_scope,
                        stable_key=_stable_key(version.id, strategy, f"parent:{'/'.join(path_key)}"),
                        position=position,
                        chunk_level="parent",
                        content="",  # 内容后续通过聚合子块填充
                        element_ids=[],
                        heading_path=list(path_key),
                        parent_chunk_id=parent.id if parent else None,
                        markdown_start=None,
                        markdown_end=None,
                        source_locators=[],
                    )
                    db.add(current_parent)
                    db.flush()  # 落库拿到父块 id
                    parent_by_path[path_key] = current_parent  # 缓存，避免重复创建
                parent = current_parent  # 更新为当前层级父块
                parents_for_piece.append(current_parent)  # 收集沿途父块
            # 创建实际的“子块”记录
            child = DocumentChunk(
                chunking_run_id=run.id,
                document_version_id=version.id,
                candidate_id=document.candidate_id,
                document_type=document.document_type,
                permission_scope=document.permission_scope,
                stable_key=_stable_key(version.id, strategy, f"{position}:{piece.content}"),
                position=position,
                chunk_level="child",
                content=piece.content,
                element_ids=piece.element_ids,
                heading_path=piece.heading_path,
                parent_chunk_id=parent.id if parent else None,
                page_start=piece.page_start,
                page_end=piece.page_end,
                timestamp_start=piece.timestamp_start,
                timestamp_end=piece.timestamp_end,
                markdown_start=piece.markdown_start,
                markdown_end=piece.markdown_end,
                source_locators=piece.source_locators,
            )
            db.add(child)
            db.flush()  # 落库拿到子块 id
            # 把新子块“聚合”进它沿途的每一个父块（内容拼接、合并元素 id、合并偏移、去重定位器）
            for current_parent in parents_for_piece:
                current_parent.content = f"{current_parent.content}\n\n{child.content}".strip()
                # 用 dict.fromkeys 去重并保持顺序地合并元素 id
                current_parent.element_ids = list(dict.fromkeys([*current_parent.element_ids, *child.element_ids]))
                # 合并 Markdown 起止偏移（父块范围取所有子块的最小/最大值）
                if child.markdown_start is not None:
                    current_parent.markdown_start = min(current_parent.markdown_start, child.markdown_start) if current_parent.markdown_start is not None else child.markdown_start
                if child.markdown_end is not None:
                    current_parent.markdown_end = max(current_parent.markdown_end, child.markdown_end) if current_parent.markdown_end is not None else child.markdown_end
                # 合并来源定位器（先建立 JSON 指纹集合用于去重）
                existing_locators = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current_parent.source_locators}
                current_parent.source_locators = [
                    *current_parent.source_locators,
                    *(item for item in child.source_locators if json.dumps(item, ensure_ascii=False, sort_keys=True) not in existing_locators),
                ]
            # 维护子块之间"上一块 / 下一块"的前后链表指针
            if children:
                child.previous_chunk_id = children[-1].id
                children[-1].next_chunk_id = child.id
            children.append(child)
        # 全部成功：更新运行状态并提交
        run.status = ChunkingStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)  # 刷新到最新状态
        return run
    except Exception as exc:
        # 任一步出错：标记失败、记录错误（截断到 2000 字符）、提交后抛出
        run.status = ChunkingStatus.FAILED
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(UTC)
        db.commit()
        raise


def _stable_key(version_id: UUID, strategy: ChunkStrategy, value: str) -> str:
    """根据版本、策略与内容值生成稳定的分块键。

    通过对 (版本 id, 策略, 值) 做 SHA-256 并取前 32 位十六进制，保证同一内容是
    同一个 key，从而在多次分块之间实现稳定去重。
    """
    return hashlib.sha256(f"{version_id}:{strategy.value}:{value}".encode()).hexdigest()[:32]


def latest_chunks(db: Session, version_id: UUID) -> tuple[ChunkingRun | None, list[DocumentChunk]]:
    """查询某文档版本最近一次成功分块运行及其全部分块。

    返回 (最近成功的运行, 有序的分块列表)。若没有成功运行则返回 (None, [])。
    分块按 (chunk_level, position) 排序，保证父块在前、子块按位置依次排列，
    便于上层构建树形结构或顺序渲染。
    """
    # 找到最近一次成功、且属于该文档版本的分块运行
    run = db.scalar(
        select(ChunkingRun)
        .join(ParseJob, ParseJob.id == ChunkingRun.parse_job_id)
        .where(ParseJob.document_version_id == version_id, ChunkingRun.status == ChunkingStatus.SUCCEEDED)
        .order_by(ChunkingRun.created_at.desc())
    )
    if not run:
        return None, []
    # 取出该运行的所有分块，先按层级再按位置排序
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.chunking_run_id == run.id).order_by(DocumentChunk.chunk_level, DocumentChunk.position)
    ).all()
    return run, chunks