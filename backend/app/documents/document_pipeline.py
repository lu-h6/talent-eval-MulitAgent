from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from app.documents.chunk_service import create_chunking_run
from app.db.models import ChunkingStatus, Document, DocumentVersion, EvidenceIndexJob, IndexStatus, ParseJob, ParseStatus


# 默认分块参数
DEFAULT_CHUNK_SIZE = 800  # 分块大小（字符数）
DEFAULT_CHUNK_OVERLAP = 100  # 相邻分块间重叠字符数


@dataclass(frozen=True)
class PipelineStatusSnapshot:
    """文档处理流水线的阶段状态快照。"""
    parse_status: str | None  # 解析阶段状态
    chunk_status: str | None  # 分块阶段状态
    index_status: str | None  # 索引阶段状态
    pipeline_status: str  # 汇总的流水线状态（failed/ready/indexing/chunking/chunked/parsing/parsed/uploaded）


def queue_parse_job(db: Session, redis_client: Redis | Any, version_id: UUID) -> ParseJob:
    """创建一条解析任务记录并入队到 Redis 解析队列。

    Args:
        db: 数据库会话。
        redis_client: Redis 客户端。
        version_id: 文档版本 ID。

    Returns:
        新建的 ParseJob 记录。
    """
    job = ParseJob(document_version_id=version_id, parser_name="queued", status=ParseStatus.PENDING)
    db.add(job)
    db.commit()
    # 任务负载格式为 "job_id:version_id"
    redis_client.rpush("talent:parse:queue", f"{job.id}:{version_id}")
    return job


def queue_index_job(db: Session, redis_client: Redis | Any, version_id: UUID, *, settings: Any) -> EvidenceIndexJob:
    """创建一条向量索引任务记录并入队到 Redis 索引队列。

    Args:
        db: 数据库会话。
        redis_client: Redis 客户端。
        version_id: 文档版本 ID。
        settings: 应用配置（用于读取向量模型与集合名）。

    Returns:
        新建的 EvidenceIndexJob 记录。
    """
    job = EvidenceIndexJob(
        document_version_id=version_id,
        status=IndexStatus.PENDING,
        embedding_model=settings.embedding_model,  # 记录所用的向量化模型
        collection_name=settings.milvus_collection,  # 记录写入的向量集合
    )
    db.add(job)
    db.commit()
    # 任务负载格式为 "job_id:version_id"
    redis_client.rpush("talent:index:queue", f"{job.id}:{version_id}")
    return job


def continue_document_pipeline(
    db: Session,
    *,
    store: Any,
    redis_client: Redis | Any,
    version_id: UUID,
    settings: Any,
) -> EvidenceIndexJob:
    """在解析成功后继续推进流水线：执行分块，再入队索引任务。

    步骤：校验版本与文档 → 使用默认参数对文档分块 → 将索引任务入队。

    Returns:
        入队后的 EvidenceIndexJob 记录。
    """
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise ValueError("文档版本不存在")
    document = db.get(Document, version.document_id)
    if not document:
        raise ValueError("文档不存在")
    # 1) 基于最新解析产物执行分块
    create_chunking_run(
        db,
        store,
        document,
        version,
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    )
    # 2) 分块成功后入队索引任务
    return queue_index_job(db, redis_client, version.id, settings=settings)


def summarize_pipeline_status(*, parse_job: Any = None, chunk_run: Any = None, index_job: Any = None) -> PipelineStatusSnapshot:
    """汇总解析 / 分块 / 索引三个阶段的状态，推导出整体流水线状态。

    优先级（从高到低）：任一阶段失败 → 已就绪 → 索引中 → 分块中 → 已分块 →
    解析中 → 已解析 → 仅上传。

    Returns:
        各阶段原始状态 + 汇总后的流水线状态。
    """
    parse_status = getattr(parse_job, "status", None)
    chunk_status = getattr(chunk_run, "status", None)
    index_status = getattr(index_job, "status", None)

    # 任一阶段失败 = 整体失败
    if parse_status == ParseStatus.FAILED or chunk_status in {ChunkingStatus.FAILED, "failed"} or index_status == IndexStatus.FAILED:
        pipeline_status = "failed"
    # 索引完成 = 整条链路已完成
    elif index_status == IndexStatus.SUCCEEDED:
        pipeline_status = "ready"
    elif index_status in {IndexStatus.PENDING, IndexStatus.RUNNING, "pending", "running"}:
        pipeline_status = "indexing"
    elif chunk_status in {ChunkingStatus.PENDING, ChunkingStatus.RUNNING, "pending", "running"}:
        pipeline_status = "chunking"
    elif chunk_status in {ChunkingStatus.SUCCEEDED, "succeeded", "partially_succeeded"}:
        pipeline_status = "chunked"
    elif parse_status in {ParseStatus.PENDING, ParseStatus.RUNNING, "pending", "running"}:
        pipeline_status = "parsing"
    elif parse_status == ParseStatus.SUCCEEDED:
        pipeline_status = "parsed"
    else:
        pipeline_status = "uploaded"

    # 返回快照：把枚举状态统一转为字符串形式（无枚举值的原始值直接透传）
    return PipelineStatusSnapshot(
        parse_status=parse_status.value if hasattr(parse_status, "value") else parse_status,
        chunk_status=chunk_status.value if hasattr(chunk_status, "value") else chunk_status,
        index_status=index_status.value if hasattr(index_status, "value") else index_status,
        pipeline_status=pipeline_status,
    )