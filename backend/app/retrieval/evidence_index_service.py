"""证据索引任务：切块向量化并写入 Milvus，维护任务状态。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.milvus_store import EvidenceRecord
from app.integrations.milvus_store import MilvusEvidenceStore
from app.integrations.model_provider import get_embedding_model
from app.db.models import ChunkingRun, ChunkingStatus, Document, DocumentChunk, DocumentVersion, EvidenceIndexJob, IndexStatus


logger = logging.getLogger(__name__)


def build_evidence_records(
    *,
    document: Any,
    chunks: Sequence[Any],
    vectors: Sequence[list[float]],
    embedding_model: str,
) -> list[EvidenceRecord]:
    """把分块与对应向量组装为待写入向量库的 EvidenceRecord 列表。

    Args:
        document: 所属文档对象（提供 tenant_id）。
        chunks: 分块对象序列。
        vectors: 与 chunks 一一对应的向量序列。
        embedding_model: 使用的向量化模型名。

    Returns:
        组装完成的 EvidenceRecord 列表。

    Raises:
        ValueError: 当向量数量与分块数量不一致时抛出。
    """
    if len(chunks) != len(vectors):
        raise ValueError(f"vector count {len(vectors)} does not match chunk count {len(chunks)}")
    records: list[EvidenceRecord] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        records.append(
            EvidenceRecord(
                chunk_id=str(chunk.id),
                candidate_id=chunk.candidate_id,
                document_version_id=str(chunk.document_version_id),
                tenant_id=document.tenant_id,
                document_type=chunk.document_type,
                permission_scope=chunk.permission_scope,
                embedding_model=embedding_model,
                # 用内容哈希区分“不同内容”与“重复写入”
                content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                content=chunk.content,
                embedding=vector,
                parent_chunk_id=str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            )
        )
    return records


def index_chunks(
    *,
    document: Any,
    chunks: Sequence[Any],
    embedding_model: str,
    embedder: Any,
    store: Any,
) -> int:
    """对一批分块做向量化并写入向量库，返回写入条数。

    流程：确保集合存在 -> 生成向量 -> 组装记录 -> upsert。

    Args:
        document: 所属文档对象。
        chunks: 待向量化的分块序列。
        embedding_model: 向量化模型名。
        embedder: 提供 embed_documents(...) 的向量化模型实例。
        store: Milvus 证据向量库实例。

    Returns:
        成功写入条数。
    """
    store.ensure_collection()
    # 批量向量化所有分块内容
    vectors = embedder.embed_documents([chunk.content for chunk in chunks])
    records = build_evidence_records(
        document=document,
        chunks=chunks,
        vectors=vectors,
        embedding_model=embedding_model,
    )
    return store.upsert(records)


def get_evidence_store() -> MilvusEvidenceStore:
    """依据配置创建并返回 Milvus 证据向量库实例。"""
    settings = get_settings()
    return MilvusEvidenceStore.connect(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection_name=settings.milvus_collection,
        dimension=settings.embedding_dimension,
    )


def run_index_job(db: Session, job_id: Any, version_id: Any) -> EvidenceIndexJob:
    """执行向量索引任务的状态机：RUNNING -> 处理 -> SUCCEEDED/FAILED。

    按任务锁定状态并刷新开始时间；先删除该版本的旧向量再重建，
    处理中任何异常都会落库为 FAILED 并记录错误信息。

    Returns:
        更新后的 EvidenceIndexJob（已完成状态）。

    Raises:
        ValueError: 当任务/版本/文档不存在或缺少可用的成功切片时抛出。
    """
    job = db.get(EvidenceIndexJob, job_id)
    if not job:
        raise ValueError("索引任务不存在")
    job.status = IndexStatus.RUNNING
    job.started_at = datetime.now(UTC)
    db.commit()
    try:
        # 逐级加载版本与文档
        version = db.get(DocumentVersion, version_id)
        if not version:
            raise ValueError("文档版本不存在")
        document = db.get(Document, version.document_id)
        if not document:
            raise ValueError("文档不存在")
        # 找到该版本最后一条成功的切片运行（仅取子分块）
        run_id = db.scalar(
            select(ChunkingRun.id)
            .join(DocumentChunk, DocumentChunk.chunking_run_id == ChunkingRun.id)
            .where(
                DocumentChunk.document_version_id == version.id,
                DocumentChunk.chunk_level == "child",
                ChunkingRun.status == ChunkingStatus.SUCCEEDED,
            )
            .order_by(ChunkingRun.created_at.desc())
            .limit(1)
        )
        if not run_id:
            raise ValueError("文档尚无可用的成功切片")
        # 取出该运行下的全部子分块（按位置排序）
        chunks = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.chunking_run_id == run_id, DocumentChunk.chunk_level == "child")
            .order_by(DocumentChunk.position)
        ).all()
        embedder = get_embedding_model()
        if embedder is None:
            raise ValueError("DASHSCOPE_API_KEY 未配置，无法生成证据向量")
        store = get_evidence_store()
        store.ensure_collection()
        # 先删除该版本的旧向量，再写入重建，避免脏数据残留
        store.delete_version(tenant_id=document.tenant_id, document_version_id=str(version.id))
        job.indexed_count = index_chunks(
            document=document,
            chunks=chunks,
            embedding_model=job.embedding_model,
            embedder=embedder,
            store=store,
        )
        job.status = IndexStatus.SUCCEEDED
        job.error_message = None
    except Exception as exc:
        # 任何异常均标记为失败并保留截断的错误信息
        job.status = IndexStatus.FAILED
        job.error_message = str(exc)[:2000]
        logger.exception("evidence_index_job_failed job_id=%s version_id=%s", job_id, version_id)
    job.finished_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    return job