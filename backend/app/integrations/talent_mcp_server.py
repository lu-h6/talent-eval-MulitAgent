from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from pydantic import Field

from app.retrieval.query_plan import FilterCondition
from app.talent.talent_tools import TalentToolContext, TalentToolService, ToolExecutor

# 调用上下文提供器类型，用于提供 TalentToolContext
ContextProvider = Callable[[], TalentToolContext]


def talent_context_from_token(token: AccessToken) -> TalentToolContext:
    """从 MCP Access Token 解析出人才工具的调用上下文（租户 + 权限）。

    依据 token 中的声明（claims）提取租户、权限范围、操作者与运行 ID，
    并对缺失或格式非法的关键信息做严格校验。

    Args:
        token: 经过验证的 MCP Access Token。

    Returns:
        组装好的 TalentToolContext。

    Raises:
        PermissionError: 当 token 缺少必要声明或 permission_scopes 非列表时抛出。
    """
    claims = token.claims or {}  # claims是
    tenant_id = str(claims.get("tenant_id", "")).strip()  # tenant_id是租户标识
    raw_permission_scopes = claims.get("permission_scopes", [])  # permission_scopes是权限范围
    if not isinstance(raw_permission_scopes, (list, tuple)):
        raise PermissionError("Access Token 的 permission_scopes 必须是列表")
    # 清洗权限范围：丢弃空串
    permission_scopes = tuple(
        str(item).strip() for item in raw_permission_scopes if str(item).strip()
    )
    if not tenant_id:
        raise PermissionError("Access Token 缺少 tenant_id")
    if not permission_scopes:
        raise PermissionError("Access Token 缺少 permission_scopes")
    return TalentToolContext(
        tenant_id=tenant_id,
        permission_scopes=permission_scopes,
        actor_id=token.subject or token.client_id,  # 操作者标识
        run_id=str(claims.get("run_id", "unknown")),  # 运行 ID，用于审计
    )


def authenticated_talent_context() -> TalentToolContext:
    """从当前受保护的 MCP 请求中解析调用上下文，未认证则拒绝。"""
    token = get_access_token()
    if token is None:
        raise PermissionError("缺少经过验证的 MCP Access Token")
    return talent_context_from_token(token)


def build_talent_mcp_server(
        service: TalentToolService,
        executor: ToolExecutor,
        *,
        context_provider: ContextProvider,
        token_verifier: TokenVerifier | None = None,
        auth: AuthSettings | None = None,
) -> MCPServer:
    """构建一个提供人才检索/证据读取能力的 MCP 服务端。

    通过 @server.tool 暴露工具、@server.resource 暴露资源、@server.prompt 暴露提示词，
    所有工具调用都会透传调用上下文做租户与权限约束。

    Args:
        service: 真正执行人才能力的服务。
        executor: 工具执行器（负责审计与异常处理）。
        context_provider: 提供调用上下文的工厂函数。
        token_verifier: 可选的访问令牌校验器。
        auth: 可选的认证设置。

    Returns:
        配置完成的 MCPServer 实例。
    """
    # 创建 MCP 服务器实例，配置服务名称、版本号和描述信息
    server = MCPServer(
        "Talent Capability Service",
        version="13.1.0",
        instructions="提供受租户与权限约束的人才检索和证据读取能力。",
        token_verifier=token_verifier,
        auth=auth,
    )

    # ---- 工具：可通过 MCP 调用的能力 ----
    @server.tool(structured_output=True,description="按岗位名称查询企业已经维护的岗位 JD。")
    def lookup_job_descriptions(
            query: Annotated[str, Field(min_length=1, max_length=200)],
            limit: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> dict[str, Any]:
        """按岗位名称查询企业已经维护的岗位 JD。"""
        context = context_provider()
        return executor.execute(
            "lookup_job_descriptions",
            lambda: service.lookup_job_descriptions(query, limit=limit, context=context),
            context=context,
            arguments={"query": query, "limit": limit},
        )

    @server.tool(structured_output=True,description="按结构化条件筛选授权租户内的候选人。")
    def filter_candidates(
            filters: Annotated[list[FilterCondition], Field(max_length=20)],
    ) -> dict[str, Any]:
        """按结构化条件筛选授权租户内的候选人。"""
        context = context_provider()
        return executor.execute(
            "filter_candidates",
            lambda: service.filter_candidates(filters, context=context),
            context=context,
            arguments={"filters": filters},
        )

    @server.tool(structured_output=True, description="在候选人范围内检索材料证据并返回 Candidate Evidence Pack 2.0。")
    def search_candidate_evidence(
            query: Annotated[str, Field(min_length=1, max_length=500)],
            candidate_ids: Annotated[list[str], Field(min_length=1, max_length=100)],
    ) -> dict[str, Any]:
        """在候选人范围内检索材料证据并返回 Candidate Evidence Pack 2.0。"""
        context = context_provider()
        return executor.execute(
            "search_candidate_evidence",
            lambda: service.search_candidate_evidence(query, candidate_ids, context=context),
            context=context,
            arguments={"query": query, "candidate_ids": candidate_ids},
        )

    @server.tool(structured_output=True, description="批量读取授权租户内候选人的结构化基础信息。")
    def get_candidate_profiles(
            candidate_ids: Annotated[list[str], Field(min_length=1, max_length=100)],
    ) -> dict[str, Any]:
        """批量读取授权租户内候选人的结构化基础信息。"""
        context = context_provider()
        return executor.execute(
            "get_candidate_profiles",
            lambda: service.get_candidate_profiles(candidate_ids, context=context),
            context=context,
            arguments={"candidate_ids": candidate_ids},
        )

    # ---- 资源：可通过 MCP 资源 URI 读取的数据 ----
    @server.resource(
        "talent://jobs/{job_code}",
        title="岗位 JD",
        mime_type="application/json",
        description="按岗位代码读取岗位 JD；不存在或无权访问时报错。"

    )
    def job_description(job_code: str) -> dict[str, Any]:
        """按岗位代码读取岗位 JD；不存在或无权访问时报错。"""
        item = service.get_job_description(job_code, context=context_provider())
        if item is None:
            raise ResourceNotFoundError("岗位 JD 不存在或当前身份无权访问")
        return item

    @server.resource(
        "talent://candidates/{candidate_id}/profile",
        title="候选人基础档案",
        mime_type="application/json",
        description="读取候选人的基础档案。"
    )
    def candidate_profile(candidate_id: str) -> dict[str, Any]:
        """读取候选人的基础档案。"""
        items = service.get_candidate_profiles([candidate_id], context=context_provider())
        if not items:
            raise ResourceNotFoundError("候选人不存在或当前身份无权访问")
        return items[0]

    @server.resource(
        "talent://policies/evaluation/current",
        title="人才评估规则",
        mime_type="text/markdown",
        description="返回当前生效的人才评估规则文案。"

    )
    def evaluation_policy() -> str:
        """返回当前生效的人才评估规则文案。"""
        return "# 人才评估规则\n\n所有判断必须引用授权范围内的候选人材料。"

    # ---- 提示词模板 ----
    @server.prompt(name="talent_assessment", title="人才评估与推荐")
    def talent_assessment(job_code: str, request_text: str) -> str:
        """生成人才评估与推荐请求的提示词。"""
        return f"按照岗位 {job_code} 处理人才评估与推荐请求：{request_text}"

    return server
