"""文档领域服务：上传、版本切换、解析触发与查询。"""

import hashlib
import json
import logging
import mimetypes
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.document_parsers import parse_document
from app.db.models import Document, DocumentVersion, EmployeeProfile, FileObject, ParseArtifact, ParseJob, ParseStatus
from app.integrations.mineru_client import parse_with_mineru
from app.integrations.object_store import ObjectStore
from app.documents.router import ParserKind, choose_parser
from app.integrations.storage import build_object_key

logger = logging.getLogger(__name__)



def upload_document(db: Session, store: ObjectStore, *, filename: str, content: bytes, candidate_id: str, title: str,
                    document_type: str, employee_id: UUID | None = None, knowledge_base_id: UUID | None = None,
                    permission_scope: str = "hr_private", tenant_id: str = "course-demo") -> Document:
    """上传一份新文档：落盘对象存储并建立 Document/FileObject/Version 记录。

    步骤：
      1) 计算 MIME 类型与内容哈希；
      2) 创建 Document 记录并生成 material_no；
      3) 将文件内容写入对象存储；
      4) 创建 FileObject 与首版本 DocumentVersion。
    Args:
        db: 数据库会话。
        store: 对象存储服务。
        filename: 文档文件名。
        content: 文档内容。
        candidate_id: 候选人 ID。
        title: 文档标题。
        document_type: 文档类型。
        employee_id: 关联的员工 ID。
        knowledge_base_id: 关联的知识库 ID。
        permission_scope: 权限范围。
        tenant_id: 租户 ID。

    Returns:
        创建好的 Document 记录。
    """
    logger.info(
        "document_upload_started filename=%s size_bytes=%s employee_id=%s knowledge_base_id=%s document_type=%s",
        filename,
        len(content),
        employee_id,
        knowledge_base_id,
        document_type,
    )
    # 根据文件名猜测 MIME，猜不到则用二进制流类型兜底
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # 对内容做 SHA256 摘要，用于去重与完整性校验
    digest = hashlib.sha256(content).hexdigest()

    # 若关联了员工则以员工工号作为候选标识，否则回退到 candidate_id
    employee = db.get(EmployeeProfile, employee_id) if employee_id else None
    employee_no = employee.employee_no if employee else candidate_id

    # 创建文档主记录并 flush 以获取自增主键
    document = Document(candidate_id=employee_no, employee_id=employee_id, knowledge_base_id=knowledge_base_id,
                        tenant_id=tenant_id, title=title, document_type=document_type,
                        permission_scope=permission_scope)
    db.add(document)
    db.flush()
    # 依据文档 ID 生成素材编号
    document.material_no = f"MAT-{str(document.id).split('-')[0].upper()}"

    # 构造对象键并把内容写入对象存储
    key = build_object_key(tenant_id, str(document.id), 1, filename)
    store.put_bytes(key, content, mime)

    # 记录文件对象（指向对象存储中的实际数据）
    file_object = FileObject(bucket_name=store.settings.s3_bucket, object_key=key, original_name=filename,
                             mime_type=mime, size_bytes=len(content), sha256=digest)
    db.add(file_object)
    db.flush()

    # 创建首个版本（version_no=1，并标记为当前版本）
    version = DocumentVersion(document_id=document.id, file_object_id=file_object.id, version_no=1, is_current=True)
    db.add(version)
    db.commit()
    db.refresh(document)
    logger.info(
        "document_upload_completed document_id=%s material_no=%s object_key=%s sha256=%s",
        document.id,
        document.material_no,
        key,
        digest,
    )
    return document


def parse_version(db: Session, store: ObjectStore, version_id: UUID, job_id: UUID | None = None) -> ParseJob:
    """解析指定文档版本：按文件类型选择解析器，产出生成 Markdown 与结构化 JSON。

    - 先判断解析器类型（MinerU 或基础解析器）；
    - 将文件写入临时文件交给解析器处理；
    - 把 Markdown / structured.json 产物写入对象存储并记录 ParseArtifact；
    - 解析失败时记录错误信息并标记 FAILED。
    Args:
        db: 数据库会话。
        store: 对象存储服务。
        version_id: 文档版本 ID。
        job_id: 解析任务 ID。

    Returns:
        更新后的 ParseJob。
    """
    version = db.get(DocumentVersion, version_id)
    if not version:
        raise ValueError("文档版本不存在")
    file_object = db.get(FileObject, version.file_object_id)
    # 根据原始文件名选择解析器类型
    parser_kind = choose_parser(file_object.original_name)
    logger.info(
        "parse_job_started job_id=%s version_id=%s filename=%s parser_kind=%s",
        job_id,
        version_id,
        file_object.original_name,
        parser_kind.value,
    )
    # 复用已有任务或新建任务，并置为 RUNNING
    job = db.get(ParseJob, job_id) if job_id else None
    if job is None:
        job = ParseJob(document_version_id=version.id, parser_name=parser_kind.value)
        db.add(job)
    job.parser_name = parser_kind.value
    job.status = ParseStatus.RUNNING
    job.progress = 10
    job.started_at = datetime.now(UTC)
    db.commit()
    try:
        # 读取对象存储中的原始文件字节并写入临时文件，供解析器读取
        content = store.get_bytes(file_object.object_key)
        suffix = Path(file_object.original_name).suffix  # 获取文件扩展名
        # Windows 下 NamedTemporaryFile 的句柄未关闭时无法按路径重新打开，
        # 因此写入后先关闭句柄，再交给解析器按路径读取，最后由 finally 清理。
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(content)
            temp.flush()
            temp_path = Path(temp.name)  # 临时文件路径
        try:
            if parser_kind == ParserKind.MINERU:
                # 复杂文档走 MinerU 深度解析
                result = parse_with_mineru(temp_path)
            else:
                try:
                    # 简单文档走本地基础解析器
                    result = parse_document(temp_path)
                except Exception as exc:
                    # 基础解析失败且是图片/PDF 时，回退到 MinerU
                    if suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
                        raise
                    logger.warning(
                        "basic_parser_failed_fallback_mineru job_id=%s filename=%s reason=%s",
                        job.id,
                        file_object.original_name,
                        exc,
                    )
                    result = parse_with_mineru(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)  # 删除临时文件

        # 编码 Markdown 与结构化 JSON 两种产物
        markdown = result.text.encode("utf-8")
        structured = json.dumps(result.structured or result.metadata, ensure_ascii=False, indent=2).encode("utf-8")
        base = f"artifacts/{job.id}"
        # 逐个写入对象存储并登记产物记录
        for artifact_type, key, body, content_type in [
            ("markdown", f"{base}/content.md", markdown, "text/markdown; charset=utf-8"),
            ("structured_json", f"{base}/structured.json", structured, "application/json"),
        ]:
            store.put_bytes(key, body, content_type)
            db.add(ParseArtifact(parse_job_id=job.id, artifact_type=artifact_type, bucket_name=store.settings.s3_bucket,
                                 object_key=key, content_type=content_type, size_bytes=len(body)))
            logger.info("parse_artifact_saved job_id=%s artifact_type=%s object_key=%s size_bytes=%s", job.id,
                        artifact_type, key, len(body))

        # 全部成功 → 标记 SUCCEEDED
        job.parser_name = result.parser_name
        job.status = ParseStatus.SUCCEEDED
        job.progress = 100
        logger.info("parse_job_succeeded job_id=%s parser_name=%s text_chars=%s", job.id, result.parser_name,
                    len(result.text))
    except Exception as exc:
        # 任一环节异常 → 记录失败信息
        job.status = ParseStatus.FAILED
        job.error_code = "PARSE_FAILED"
        job.error_message = str(exc)[:2000]
        job.progress = 100
        logger.exception("parse_job_failed job_id=%s version_id=%s error=%s", job.id, version_id, exc)
    job.finished_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    logger.info("parse_job_finished job_id=%s status=%s progress=%s", job.id, job.status, job.progress)
    return job


def current_version(db: Session, document_id: UUID) -> DocumentVersion | None:
    """查询文档当前生效的版本记录（is_current=True）。"""
    return db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id, DocumentVersion.is_current.is_(True)))
