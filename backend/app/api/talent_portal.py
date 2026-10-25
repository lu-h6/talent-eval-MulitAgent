"""talent_portal.py —— 评估/推荐页所需的对外接口。

本模块只做三件事：
1. 岗位 JD 与候选人画像的只读查询；
2. 把 LangGraph Agent Server 的线程/运行/状态/SSE 暴露成稳定的 REST 契约；
3. 用统一入口创建“评估运行”，并从线程状态读取报告。

图逻辑仍在 Agent Server（backend/langgraph.json），本层不复制任何编排。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import JobDescription
from app.talent.agent_gateway import AgentServerError, TalentAgentGateway, get_agent_gateway
from app.talent.talent_tools import TalentToolContext, TalentToolService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["talent-portal"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class CandidateProfilesRequest(BaseModel):
    """批量候选人画像请求。"""

    candidate_ids: list[str] = Field(min_length=1, max_length=100)


class ThreadCreateRequest(BaseModel):
    """创建会话线程的请求体；metadata 只允许业务附加信息。"""

    metadata: dict[str, Any] | None = None


class RunRequest(BaseModel):
    """在既有线程上发起一次新运行（新一轮对话）。"""

    request_text: str = Field(min_length=1, max_length=4000)


class ResumeRequest(BaseModel):
    """恢复一次被 interrupt 暂停的运行。

    载荷形状由暂停点决定，例如：
    - job_selection: {"action": "select", "job_code": "JD-AI-002"}
    - requirement_revision: {"interaction_id": "rr-v0", "action": "revise", "revision_text": "..."}
    - empty_candidate_set: {"interaction_id": "ec-v0", "action": "relax", "relaxations": [...]}
    """

    payload: dict[str, Any]


class EvaluationCreateRequest(BaseModel):
    """创建一次完整人才评估运行。"""

    request_text: str = Field(min_length=1, max_length=4000)
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def get_talent_service() -> TalentToolService:
    """构造人才工具服务；测试可通过依赖覆盖替换为 SQLite。"""
    return TalentToolService(session_factory=SessionLocal)


def get_tenant_context(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_permission_scopes: str = Header(..., alias="X-Permission-Scopes"),
) -> tuple[str, list[str]]:
    """从请求头解析受信任的租户与权限范围。"""
    tenant_id = x_tenant_id.strip()
    permission_scopes = [item.strip() for item in x_permission_scopes.split(",") if item.strip()]
    if not tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "缺少租户标识")
    if not permission_scopes:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "缺少可用权限范围")
    return tenant_id, permission_scopes


def _tool_context(tenant_id: str, permission_scopes: list[str]) -> TalentToolContext:
    return TalentToolContext(
        tenant_id=tenant_id,
        permission_scopes=tuple(permission_scopes),
        actor_id="frontend",
        run_id="http",
    )


def _agent_error(exc: AgentServerError) -> HTTPException:
    logger.warning("agent_server_call_failed status=%s message=%s", exc.status_code, exc.message)
    return HTTPException(exc.status_code, exc.message)


# ---------------------------------------------------------------------------
# 岗位 JD
# ---------------------------------------------------------------------------


@router.get("/job-descriptions")
def list_job_descriptions(
    query: str = Query("", max_length=200, description="岗位名称关键词；为空时返回全部生效 JD"),
    limit: int = Query(20, ge=1, le=50),
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    service: TalentToolService = Depends(get_talent_service),
):
    """按岗位名称查询 JD；不传 query 时列出当前租户全部生效 JD。"""
    tenant_id, permission_scopes = context
    tool_context = _tool_context(tenant_id, permission_scopes)
    keyword = query.strip()
    if keyword:
        return service.lookup_job_descriptions(keyword, context=tool_context, limit=limit)

    with service.session_factory() as db:
        rows = db.scalars(
            select(JobDescription)
            .where(
                JobDescription.tenant_id == tenant_id,
                JobDescription.status == "active",
            )
            .order_by(JobDescription.name)
            .limit(limit)
        ).all()
    return [
        {
            "job_code": row.job_code,
            "name": row.name,
            "match_score": 1.0,
            "match_type": "all",
            "version": row.version,
            "content": row.content,
        }
        for row in rows
    ]


@router.get("/job-descriptions/{job_code}")
def get_job_description(
    job_code: str,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    service: TalentToolService = Depends(get_talent_service),
):
    """读取单个岗位 JD；不存在或不属于当前租户时返回 404。"""
    tenant_id, permission_scopes = context
    item = service.get_job_description(job_code, context=_tool_context(tenant_id, permission_scopes))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "岗位 JD 不存在或当前身份无权访问")
    return item


# ---------------------------------------------------------------------------
# 候选人画像
# ---------------------------------------------------------------------------


@router.post("/candidates/profiles")
def get_candidate_profiles(
    payload: CandidateProfilesRequest,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    service: TalentToolService = Depends(get_talent_service),
):
    """按工号批量读取候选人基础画像（仅在职且租户匹配）。"""
    tenant_id, permission_scopes = context
    candidate_ids = list(dict.fromkeys(item.strip() for item in payload.candidate_ids if item.strip()))
    if not candidate_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "candidate_ids 不能为空")

    items = service.get_candidate_profiles(
        candidate_ids,
        context=_tool_context(tenant_id, permission_scopes),
    )
    found = {item["candidate_id"] for item in items}
    return {
        "items": items,
        "missing": [item for item in candidate_ids if item not in found],
    }


# ---------------------------------------------------------------------------
# 会话线程
# ---------------------------------------------------------------------------


def _thread_summary(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": thread.get("thread_id"),
        "status": thread.get("status"),
        "metadata": thread.get("metadata") or {},
        "created_at": thread.get("created_at"),
        "updated_at": thread.get("updated_at"),
        "state_updated_at": thread.get("state_updated_at"),
    }


async def _owned_thread(
    gateway: TalentAgentGateway,
    thread_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """读取线程并校验租户归属，避免跨租户读取会话。"""
    try:
        thread = await gateway.get_thread(thread_id)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    metadata = thread.get("metadata") or {}
    if metadata.get("tenant_id") != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return thread


def _collect_interrupts(state: dict[str, Any]) -> list[dict[str, Any]]:
    """汇总顶层与子图任务中的 interrupt，兼容子图暂停的两种返回形态。"""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(items: list[Any] | None) -> None:
        for item in items or []:
            key = item.get("id") if isinstance(item, dict) else None
            marker = str(key) if key else repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            collected.append(item)

    add(state.get("interrupts"))

    def walk(tasks: list[Any] | None) -> None:
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            add(task.get("interrupts"))
            nested = task.get("state")
            if isinstance(nested, dict):
                walk(nested.get("tasks"))

    walk(state.get("tasks"))
    return collected


def _state_payload(thread: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = state.get("values") or {}
    interrupts = _collect_interrupts(state)
    next_nodes = state.get("next") or []
    thread_status = thread.get("status") or "idle"
    return {
        "thread_id": thread.get("thread_id"),
        "status": thread_status,
        "state_status": values.get("status"),
        "values": values,
        "next": next_nodes,
        "interrupts": interrupts,
        "metadata": thread.get("metadata") or {},
        "created_at": thread.get("created_at"),
        "updated_at": thread.get("updated_at"),
    }


@router.post("/talent/threads", status_code=status.HTTP_201_CREATED)
async def create_talent_thread(
    payload: ThreadCreateRequest,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """创建一个评估/推荐会话线程。"""
    tenant_id, _ = context
    metadata = dict(payload.metadata or {})
    metadata["tenant_id"] = tenant_id
    metadata.setdefault("kind", "talent_chat")
    try:
        thread = await gateway.create_thread(metadata)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    return _thread_summary(thread)


@router.get("/talent/threads")
async def list_talent_threads(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """列出当前租户的会话，按 Agent Server 默认顺序返回。"""
    tenant_id, _ = context
    try:
        threads = await gateway.list_threads(
            metadata={"tenant_id": tenant_id},
            limit=limit,
            offset=offset,
        )
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    return [_thread_summary(item) for item in threads]


@router.delete("/talent/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_talent_thread(
    thread_id: str,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """删除当前租户的会话线程。"""
    tenant_id, _ = context
    await _owned_thread(gateway, thread_id, tenant_id)
    try:
        await gateway.delete_thread(thread_id)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/talent/threads/{thread_id}/state")
async def get_talent_thread_state(
    thread_id: str,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """读取会话当前状态、下一节点和待处理 interrupt。"""
    tenant_id, _ = context
    thread = await _owned_thread(gateway, thread_id, tenant_id)
    try:
        state = await gateway.get_state(thread_id)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    return _state_payload(thread, state)


@router.post("/talent/threads/{thread_id}/runs")
async def stream_talent_thread_run(
    thread_id: str,
    payload: RunRequest,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """在新一轮对话上发起运行，并以 SSE 流式返回节点更新与 interrupt。"""
    tenant_id, permission_scopes = context
    await _owned_thread(gateway, thread_id, tenant_id)
    try:
        await gateway.ensure_available()
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    stream = gateway.stream_run(
        thread_id,
        request_text=payload.request_text,
        tenant_id=tenant_id,
        permission_scopes=permission_scopes,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/talent/threads/{thread_id}/resume")
async def resume_talent_thread(
    thread_id: str,
    payload: ResumeRequest,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """用暂停点要求的载荷恢复运行，并以 SSE 流式返回后续事件。"""
    tenant_id, permission_scopes = context
    await _owned_thread(gateway, thread_id, tenant_id)
    try:
        await gateway.ensure_available()
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    stream = gateway.stream_run(
        thread_id,
        resume=payload.payload,
        tenant_id=tenant_id,
        permission_scopes=permission_scopes,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# 评估运行与报告
# ---------------------------------------------------------------------------


@router.post("/talent/evaluations", status_code=status.HTTP_202_ACCEPTED)
async def create_talent_evaluation(
    payload: EvaluationCreateRequest,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """创建一次完整评估运行，立即返回 evaluation_id / thread_id / run_id。

    evaluation_id 就是 thread_id，后续用同一路径轮询状态和读取报告。
    """
    tenant_id, permission_scopes = context
    metadata = dict(payload.metadata or {})
    metadata.update({"tenant_id": tenant_id, "kind": "talent_evaluation"})
    try:
        thread = await gateway.create_thread(metadata)
        thread_id = thread["thread_id"]
        run = await gateway.start_run(
            thread_id,
            request_text=payload.request_text,
            tenant_id=tenant_id,
            permission_scopes=permission_scopes,
        )
    except AgentServerError as exc:
        raise _agent_error(exc) from exc

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "evaluation_id": thread_id,
            "thread_id": thread_id,
            "run_id": run.get("run_id"),
            "status": run.get("status", "pending"),
        },
    )


@router.get("/talent/evaluations/{evaluation_id}")
async def get_talent_evaluation(
    evaluation_id: str,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """轮询评估运行状态；返回值同时包含 thread 状态与图状态。"""
    tenant_id, _ = context
    thread = await _owned_thread(gateway, evaluation_id, tenant_id)
    try:
        state = await gateway.get_state(evaluation_id)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc
    payload = _state_payload(thread, state)
    payload["evaluation_id"] = evaluation_id
    return payload


@router.get("/talent/evaluations/{evaluation_id}/report")
async def get_talent_evaluation_report(
    evaluation_id: str,
    context: tuple[str, list[str]] = Depends(get_tenant_context),
    gateway: TalentAgentGateway = Depends(get_agent_gateway),
):
    """读取评估报告；未完成时返回 available=false 和当前暂停信息。"""
    tenant_id, _ = context
    thread = await _owned_thread(gateway, evaluation_id, tenant_id)
    try:
        state = await gateway.get_state(evaluation_id)
    except AgentServerError as exc:
        raise _agent_error(exc) from exc

    values = state.get("values") or {}
    report = values.get("report")
    payload: dict[str, Any] = {
        "evaluation_id": evaluation_id,
        "thread_id": evaluation_id,
        "status": thread.get("status"),
        "state_status": values.get("status"),
        "available": bool(report),
        "next": state.get("next") or [],
        "interrupts": _collect_interrupts(state),
    }
    if not report:
        payload["message"] = "评估尚未完成，请继续轮询状态或处理 interrupt"
        return payload

    for field in (
        "ranking",
        "candidate_ids",
        "dimension_assessments",
        "candidate_assessments",
        "consistency_issues",
        "errors",
    ):
        payload[field] = values.get(field, [])
    payload["report"] = report
    return payload
