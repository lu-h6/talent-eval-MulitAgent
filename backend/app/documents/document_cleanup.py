"""文档删除时的级联清理：对象存储文件、解析产物与向量索引。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChunkingRun,        # 分块运行记录
    Document,           # 文档主表
    DocumentChunk,      # 文档分块明细
    DocumentMetadata,   # 文档元数据（键值对）
    DocumentVersion,    # 文档版本
    EvidenceIndexJob,   # 向量索引任务
    FileObject,         # 文件对象（真实存储在对象存储）
    ParseArtifact,      # 解析产物（如下图所示、Markdown 等）
    ParseJob,           # 解析任务
)


def delete_document_bundle(db: Session, object_store, milvus_store, document_id: UUID) -> dict[str, object]:
    """级联删除一个文档及其全部关联数据（物理删除）。

    一次删除会把与文档相关的数据库记录、对象存储文件、向量库索引全部清空，
    顺序上遵循“先删外部资源、再删关联子表、最后删主表”的依赖关系，
    避免外键约束冲突与残留孤儿数据。

    Args:
        db: SQLAlchemy 数据库会话。
        object_store: 对象存储客户端（用于删除原始文件与解析产物）。
        milvus_store: 向量数据库客户端（用于删除该文档各版本的向量索引）。
        document_id: 待删除文档的主键 UUID。

    Returns:
        删除结果摘要 dict，包含 document_id、被删版本 ID 列表、被删对象键列表。

    Raises:
        ValueError: 当指定 document_id 对应的文档不存在时抛出。
    """
    # 1. 按主键加载文档；不存在则直接报错中止
    document = db.get(Document, document_id)
    if not document:
        raise ValueError("文档不存在")

    # 2. 查出该文档的所有版本，并收集版本 ID 列表
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).all()
    version_ids = [version.id for version in versions]

    # 3. 通过版本关联的文件对象 ID，查出原始文件对象（注意判空以兼容无版本场景）
    file_objects = db.scalars(
        select(FileObject).where(FileObject.id.in_([version.file_object_id for version in versions]))
    ).all() if versions else []

    # 4. 通过版本 ID 查出所有解析任务（ParseJob）
    parse_jobs = db.scalars(
        select(ParseJob).where(ParseJob.document_version_id.in_(version_ids))
    ).all() if version_ids else []
    parse_job_ids = [job.id for job in parse_jobs]

    # 5. 通过解析任务 ID 查出所有解析产物（ParseArtifact，如解析出的图片/Markdown）
    artifacts = db.scalars(
        select(ParseArtifact).where(ParseArtifact.parse_job_id.in_(parse_job_ids))
    ).all() if parse_job_ids else []

    # 6. 通过解析任务 ID 查出所有分块运行（ChunkingRun），并收集分块运行 ID
    chunk_runs = db.scalars(
        select(ChunkingRun).where(ChunkingRun.parse_job_id.in_(parse_job_ids))
    ).all() if parse_job_ids else []
    chunk_run_ids = [run.id for run in chunk_runs]

    # ---- 7. 先清理外部存储资源（对象存储 + 向量库） ----
    # 7.1 删除向量库中该文档各版本的向量索引
    for version in versions:
        milvus_store.delete_version(tenant_id=document.tenant_id, document_version_id=str(version.id))
    # 7.2 删除对象存储中的原始文件
    for file_object in file_objects:
        object_store.delete(file_object.object_key)
    # 7.3 删除对象存储中的解析产物文件
    for artifact in artifacts:
        object_store.delete(artifact.object_key)

    # ---- 8. 再按“子表 → 父表”顺序删除数据库记录 ----
    # 8.1 删除解析产物记录（依赖 ParseJob）
    if parse_job_ids:
        db.execute(delete(ParseArtifact).where(ParseArtifact.parse_job_id.in_(parse_job_ids)))
    # 8.2 先删分块明细（依赖 ChunkingRun），再删分块运行记录
    if chunk_run_ids:
        db.execute(delete(DocumentChunk).where(DocumentChunk.chunking_run_id.in_(chunk_run_ids)))
        db.execute(delete(ChunkingRun).where(ChunkingRun.id.in_(chunk_run_ids)))
    # 8.3 依次删除：索引任务 → 解析任务 → 版本关联分块 → 版本记录
    if version_ids:
        db.execute(delete(EvidenceIndexJob).where(EvidenceIndexJob.document_version_id.in_(version_ids)))
        db.execute(delete(ParseJob).where(ParseJob.document_version_id.in_(version_ids)))
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id.in_(version_ids)))
        db.execute(delete(DocumentVersion).where(DocumentVersion.id.in_(version_ids)))
    # 8.4 删除文件对象记录（对象存储中的实际文件已在第 7 步删除）
    if file_objects:
        db.execute(delete(FileObject).where(FileObject.id.in_([item.id for item in file_objects])))

    # 8.5 删除文档元数据，最后删除文档主记录并提交事务
    db.execute(delete(DocumentMetadata).where(DocumentMetadata.document_id == document.id))
    db.delete(document)
    db.commit()

    # ---- 9. 返回删除摘要 ----
    return {
        "document_id": str(document.id),
        "deleted_versions": [str(version.id) for version in versions],
        "deleted_objects": [item.object_key for item in [*file_objects, *artifacts]],
    }