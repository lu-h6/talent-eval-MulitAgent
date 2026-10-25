from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class AgentServerError(RuntimeError):
    """Agent Server 调用失败：携带建议返回给前端的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 502, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _sse_error(code: str, message: str) -> bytes:
    """把运行期错误编码成前端可识别的 SSE error 事件。"""
    payload = json.dumps({"code": code, "message": message}, ensure_ascii=False)
    return f"event: error\ndata: {payload}\n\n".encode("utf-8")


class TalentAgentGateway:
    """对 LangGraph Agent Server 的最小 HTTP 封装。

    只暴露前端需要的控制面：线程、运行、状态和 SSE 流。
    图本身仍由 Agent Server 加载，本服务不复制任何图逻辑。
    """

    def __init__(
        self,
        *,
        base_url: str,
        assistant_id: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.assistant_id = assistant_id
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "TalentAgentGateway":
        current = settings or get_settings()
        return cls(
            base_url=current.langgraph_url,
            assistant_id=current.langgraph_assistant_id,
            timeout_seconds=current.langgraph_timeout_seconds,
        )

    # ---- 基础请求 ----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
            ) as client:
                response = await client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise AgentServerError(
                "Agent Server 不可用，请先启动本地 langgraph dev（端口 2024）",
                503,
                str(exc),
            ) from exc

        if response.status_code == 404:
            raise AgentServerError("会话或运行不存在", 404)
        if response.status_code >= 400:
            raise AgentServerError(
                self._error_message(response),
                response.status_code if response.status_code < 500 else 502,
                response.text[:1000],
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AgentServerError("Agent Server 返回了非 JSON 响应", 502, response.text[:1000]) from exc

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:300] or f"Agent Server 返回 {response.status_code}"
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message")
            if isinstance(detail, str) and detail.strip():
                return detail
        return json.dumps(payload, ensure_ascii=False)[:300]

    async def ensure_available(self) -> None:
        """在开始 SSE 之前做一次轻量健康检查，便于返回明确的 503。"""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(5.0),
            ) as client:
                response = await client.get("/ok")
        except httpx.HTTPError as exc:
            raise AgentServerError(
                "Agent Server 不可用，请先启动本地 langgraph dev（端口 2024）",
                503,
                str(exc),
            ) from exc
        if response.status_code != 200:
            raise AgentServerError("Agent Server 健康检查失败", 503, response.text[:500])

    # ---- 线程 ----

    async def create_thread(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("POST", "/threads", json_body={"metadata": metadata or {}})

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/threads/{thread_id}")

    async def list_threads(
        self,
        *,
        metadata: dict[str, Any],
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "POST",
            "/threads/search",
            json_body={"metadata": metadata, "limit": limit, "offset": offset},
        )

    async def delete_thread(self, thread_id: str) -> None:
        await self._request("DELETE", f"/threads/{thread_id}")

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/threads/{thread_id}/state",
            params={"subgraphs": "true"},
        )

    # ---- 运行 ----

    def _context(self, tenant_id: str, permission_scopes: list[str]) -> dict[str, Any]:
        return {"tenant_id": tenant_id, "permission_scopes": permission_scopes}

    async def start_run(
        self,
        thread_id: str,
        *,
        request_text: str,
        tenant_id: str,
        permission_scopes: list[str],
    ) -> dict[str, Any]:
        """启动一次后台运行，立即返回 run 元信息（供 202 接口使用）。"""
        body = {
            "assistant_id": self.assistant_id,
            "input": {"request_text": request_text},
            "context": self._context(tenant_id, permission_scopes),
        }
        return await self._request("POST", f"/threads/{thread_id}/runs", json_body=body)

    def _stream_body(
        self,
        *,
        request_text: str | None,
        resume: dict[str, Any] | None,
        tenant_id: str,
        permission_scopes: list[str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "assistant_id": self.assistant_id,
            "context": self._context(tenant_id, permission_scopes),
            "stream_mode": ["updates", "values"],
            "stream_subgraphs": True,
        }
        if resume is not None:
            body["command"] = {"resume": resume}
        else:
            body["input"] = {"request_text": request_text or ""}
        return body

    async def stream_run(
        self,
        thread_id: str,
        *,
        request_text: str | None = None,
        resume: dict[str, Any] | None = None,
        tenant_id: str,
        permission_scopes: list[str],
    ) -> AsyncIterator[bytes]:
        """把 Agent Server 的 SSE 原样转发给浏览器。

        连接阶段失败时抛出 AgentServerError；已经开始输出后失败时，
        改为输出一条 SSE error 事件，避免前端一直等待。
        """
        body = self._stream_body(
            request_text=request_text,
            resume=resume,
            tenant_id=tenant_id,
            permission_scopes=permission_scopes,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            ) as client:
                async with client.stream(
                    "POST",
                    f"/threads/{thread_id}/runs/stream",
                    json=body,
                ) as response:
                    if response.status_code == 404:
                        raise AgentServerError("会话不存在", 404)
                    if response.status_code >= 400:
                        raw = await response.aread()
                        raise AgentServerError(
                            self._error_message_from_text(raw),
                            response.status_code if response.status_code < 500 else 502,
                            raw.decode("utf-8", errors="replace")[:1000],
                        )
                    # 使用解码后的字节，避免把上游 gzip 压缩流原样透传给浏览器
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
        except AgentServerError as exc:
            yield _sse_error("agent_server_error", exc.message)
        except httpx.HTTPError as exc:
            yield _sse_error("agent_server_unavailable", f"Agent Server 连接中断：{exc}")

    @staticmethod
    def _error_message_from_text(raw: bytes) -> str:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return raw.decode("utf-8", errors="replace")[:300] or "运行启动失败"
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message")
            if isinstance(detail, str) and detail.strip():
                return detail
        return json.dumps(payload, ensure_ascii=False)[:300]


def get_agent_gateway() -> TalentAgentGateway:
    """FastAPI 依赖：按当前配置构造 Agent Server 客户端。"""
    return TalentAgentGateway.from_settings()
