"""引用处理：把短编号映射回 Chunk 并回表校验来源。"""

# 依据 PostgreSQL 中当前的权限解析引用，绝不信任向量索引里的元信息。
from uuid import UUID
from fastapi import HTTPException
from app.db.models import Document, DocumentVersion, DocumentChunk, ChunkingRun, ParseJob, KnowledgeBase


def resolve_citation(db, chunk_id: UUID, *, tenant_id: str, permission_scopes: list[str],
                     quote_start: int | None = None, quote_end: int | None = None):
    """把分块 ID 解析为带权限校验的引用详情。

    以数据库实时数据为准逐级做权限检查（文档、知识库、分块），
    确保调用方只能读取自己在权限范围内、且归属同一候选人的生效内容。

    Args:
        db: 数据库会话。
        chunk_id: 分块主键 UUID。
        tenant_id: 租户标识（必须匹配）。
        permission_scopes: 当前身份拥有的权限范围列表。
        quote_start: 可选的引用起始位置。
        quote_end: 可选的引用结束位置。

    Returns:
        引用详情字典（回引字段、版本状态、原文内容、定位器与位置等）。

    Raises:
        HTTPException: 引用不可用/无权限（404）或引用位置无效（422）。
    """

    def unavailable():
        """把无法解析的引用标记为不可用。"""
        raise HTTPException(404, '引用不可用或无访问权限')

    # 逐级加载分块 -> 版本 -> 文档，任一级缺失即视为不可访问
    chunk = db.get(DocumentChunk, chunk_id)
    if chunk is None:
        unavailable()
    version = db.get(DocumentVersion, chunk.document_version_id)
    if version is None:
        unavailable()
    doc = db.get(Document, version.document_id)
    # 权限与一致性校验：文档生效、租户匹配、权限范围命中、候选人与分块一致
    if (doc is None or doc.tenant_id != tenant_id or doc.status != 'active'
            or doc.permission_scope not in permission_scopes
            or chunk.permission_scope not in permission_scopes
            or chunk.candidate_id != doc.candidate_id):
        unavailable()
    # 若文档挂接知识库，则知识库也必须处于可用且可访问状态
    if doc.knowledge_base_id:
        kb = db.get(KnowledgeBase, doc.knowledge_base_id)
        if (kb is None or kb.tenant_id != tenant_id or kb.status != 'active'
                or kb.permission_scope not in permission_scopes):
            unavailable()
    # 追溯解析运行与解析作业（用于取得解析器版本）
    run = db.get(ChunkingRun, chunk.chunking_run_id)
    job = db.get(ParseJob, run.parse_job_id) if run else None
    # 校验引用位置：要么都为空，要么在正文有效区间内
    if ((quote_start is None) != (quote_end is None)
            or (quote_start is not None and not (0 <= quote_start < quote_end <= len(chunk.content)))):
        raise HTTPException(422, '引用位置无效')
    return {
        'citation_id': str(chunk.id), 'chunk_id': str(chunk.id),
        'candidate_id': doc.candidate_id, 'document_id': str(doc.id),
        'document_title': doc.title, 'document_version_id': str(version.id),
        'version_no': version.version_no,
        'version_state': 'current' if version.is_current else 'historical',
        'permission_scope': doc.permission_scope, 'content': chunk.content,
        'heading_path': chunk.heading_path, 'markdown_start': chunk.markdown_start,
        'markdown_end': chunk.markdown_end, 'source_locators': chunk.source_locators,
        'quote_start': quote_start, 'quote_end': quote_end,
        'page_start': chunk.page_start, 'page_end': chunk.page_end,
        'timestamp_start': chunk.timestamp_start, 'timestamp_end': chunk.timestamp_end,
        'parser_version': job.parser_version if job else None,
        'parent_chunk_id': str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
    }


def load_pack_sources(db, chunks, *, tenant_id, permission_scopes):
    """Rehydrate trusted text and optionally add same-version parent context.

    Args:
        db: 数据库会话。
        chunks: 命中的分块信息列表（含 chunk_id、candidate_id、requirement_ids）。
        tenant_id: 租户标识。
        permission_scopes: 当前身份的权限范围列表。

    Returns:
        解析后的可信来源行列表（含 content、requirement_ids 等），
        无法解析或不属于当前候选人的命中会被跳过。
    """
    sources = {}
    for hit in chunks:
        try:
            row = resolve_citation(db, UUID(hit['chunk_id']), tenant_id=tenant_id,
                                   permission_scopes=permission_scopes)
        except (ValueError, HTTPException):
            continue  # 无法解析命中，直接跳过
        # 候选人归属不一致或为历史版本，跳过
        if row['candidate_id'] != hit['candidate_id'] or row['version_state'] != 'current':
            continue
        expanded = [row]
        # 存在父分块时附带同版本父上下文（内容过长则不加）
        if row['parent_chunk_id']:
            try:
                parent = resolve_citation(db, UUID(row['parent_chunk_id']), tenant_id=tenant_id,
                                          permission_scopes=permission_scopes)
                if (parent['candidate_id'] == row['candidate_id']
                        and parent['document_version_id'] == row['document_version_id']
                        and len(parent['content']) <= 8000):
                    expanded.append(parent)
            except (ValueError, HTTPException):
                pass
        # 合并来源并累计命中要求的 requirement_ids
        for entry in expanded:
            existing = sources.setdefault(entry['chunk_id'], {**entry, 'requirement_ids': []})
            existing['requirement_ids'] = sorted(set(existing['requirement_ids'] + hit['requirement_ids']))
    return list(sources.values())
