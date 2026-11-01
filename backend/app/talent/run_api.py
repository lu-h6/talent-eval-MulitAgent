"""运行（流式）与会话的 HTTP 接口。

对外提供：创建运行、订阅 SSE 事件、HITL 恢复、取消运行，以及运行历史与会话列表。
路由前缀为 /api/runs 与 /api/conversations，运行管理器由 app.state.talent_run_manager 注入。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.talent.run_stream import RunEvent, TalentRunManager


router = APIRouter(prefix="/api/runs", tags=["talent-runs"])
conversation_router = APIRouter(prefix="/api/conversations", tags=["talent-conversations"])


class CreateRunInput(BaseModel):
    """创建运行请求体：用户消息与可选会话 ID。"""
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ResumeRunInput(BaseModel):
    """HITL 恢复请求体：用户提交的选择或修订文本。"""
    value: str = Field(min_length=1, max_length=200)


def encode_sse(event: RunEvent) -> str:
    """把一条运行事件编码为 SSE 帧（id / event / data）。"""
    payload = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {payload}\n\n"


def _manager(request: Request) -> TalentRunManager:
    """从应用状态取出本次请求使用的运行管理器。"""
    return request.app.state.talent_run_manager


@router.get("/history")
async def list_run_history(request: Request, limit: int = 20):
    """分页列出运行历史摘要。"""
    return _manager(request).list_history(limit=max(1, min(limit, 100)))


@router.get("/history/{run_id}")
async def get_run_history(run_id: str, request: Request):
    """读取单次运行详情（步骤、引用与错误）。"""
    detail = _manager(request).get_history(run_id)
    if detail is None:
        raise HTTPException(404, "运行记录不存在")
    return detail


@conversation_router.get("")
async def list_conversations(
    request: Request,
    status: str | None = None,
    limit: int = 20,
):
    """列出会话，可按状态过滤。"""
    return _manager(request).list_conversations(
        status=status,
        limit=max(1, min(limit, 100)),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_run(payload: CreateRunInput, request: Request):
    """创建一次运行并返回事件流地址；同一会话未结束时返回 409。"""
    try:
        run = await _manager(request).create_run(
            payload.message,
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "run_id": run.run_id,
        "conversation_id": run.conversation_id,
        "turn_no": run.turn_no,
        "status": run.status,
        "events_url": f"/api/runs/{run.run_id}/events",
    }


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
):
    """以 SSE 推送运行事件，运行结束或客户端断开时关闭连接。"""
    manager = _manager(request)
    try:
        manager.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    cursor = 0

    async def generate() -> AsyncIterator[str]:
        """事件生成器：推送增量事件、发心跳，运行结束即退出。"""
        nonlocal cursor
        yield "retry: 1500\n\n"
        while True:
            events = manager.events_since(run_id, cursor)
            for event in events:
                cursor = event.sequence
                yield encode_sse(event)
            if manager.is_stream_finished(run_id):
                return
            await manager.wait_for_change(run_id, cursor)
            if await request.is_disconnected():
                return
            if not manager.events_since(run_id, cursor):
                yield ": heartbeat\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, payload: ResumeRunInput, request: Request):
    """把人工决策提交回暂停中的图，继续执行。"""
    manager = _manager(request)
    try:
        run = await manager.resume_run(run_id, payload.value)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run.run_id, "status": run.status}


@router.delete("/{run_id}")
async def cancel_run(run_id: str, request: Request):
    """取消运行（幂等，已结束的运行原样返回）。"""
    try:
        run = await _manager(request).cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run.run_id, "status": run.status}
