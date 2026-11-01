from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from difflib import SequenceMatcher
from threading import Lock
from typing import Annotated, Any, Callable
from uuid import uuid4

from langchain.tools import ToolRuntime, tool
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmployeeProfile, JobDescription, ToolCallAudit
from app.retrieval.query_plan import QueryPlan, FilterCondition, TaskType, select_candidate_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TalentToolContext:
    """一次工具调用的信任上下文（由访问令牌解析而来）。"""
    tenant_id: str  # 租户标识
    permission_scopes: tuple[str, ...]  # 可访问的权限范围
    actor_id: str = "unknown"  # 操作者标识
    run_id: str = "unknown"  # 运行 ID（用于链路追踪）


@dataclass(frozen=True)
class ToolPolicy:
    """单个工具的执行策略：重试次数、超时与退避间隔。"""
    max_attempts: int = 2  # 最大尝试次数
    timeout_seconds: float = 3.0  # 单次超时
    backoff_seconds: float = 0.05  # 重试退避基数


class TransientToolError(RuntimeError):
    """代表可重试的瞬时性工具错误（如依赖未就绪/暂时不可用）。"""
    pass

# 审计落盘策略
class InMemoryAuditSink:
    """内存版审计落盘（用于测试或默认场景）。"""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class SqlAuditSink:
    """持久化到数据库的审计落盘。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def write(self, record: dict[str, Any]) -> None:
        with self.session_factory() as db:
            db.add(ToolCallAudit(**record))
            db.commit()


class CircuitBreaker:
    """针对单个工具名的熔断器：连续失败过多则临时熔断。

    熔断期间直接拒绝调用，等待 recovery_seconds 后自动尝试恢复（半开）。
    """

    def __init__(self, *, failure_threshold: int = 3, recovery_seconds: float = 30) -> None:
        self.failure_threshold = failure_threshold  # 触发熔断的连续失败次数
        self.recovery_seconds = recovery_seconds  # 熔断恢复等待时长
        self._failures: dict[str, int] = {}  # 各工具的连续失败计数
        self._opened_at: dict[str, float] = {}  # 各工具熔断发生时刻
        self._lock = Lock()  # 多线程安全锁

    def allow(self, name: str) -> bool:
        """是否允许该工具调用：未熔断返回 True；已超过恢复期则复位并放行。"""
        with self._lock:
            opened_at = self._opened_at.get(name)
            if opened_at is None:
                return True
            # 熔断已超过恢复期：清零计数并放行（半开试探）
            if time.monotonic() - opened_at >= self.recovery_seconds:
                self._failures[name] = 0
                self._opened_at.pop(name, None)
                return True
            return False

    def succeed(self, name: str) -> None:
        """调用成功：清零失败计数并解除熔断。"""
        with self._lock:
            self._failures[name] = 0
            self._opened_at.pop(name, None)

    def fail(self, name: str) -> None:
        """调用失败：累计计数，达到阈值即打开熔断。"""
        with self._lock:
            failures = self._failures.get(name, 0) + 1
            self._failures[name] = failures
            if failures >= self.failure_threshold:
                self._opened_at[name] = time.monotonic()


class ToolExecutor:
    """统一的工具执行器：负责重试、超时、熔断与审计落盘。"""

    def __init__(
            self,
            *,
            policy: ToolPolicy | None = None,
            tool_policies: dict[str, ToolPolicy] | None = None,
            circuit_breaker: CircuitBreaker | None = None,
            audit_sink: Any | None = None,
    ) -> None:
        self.policy = policy or ToolPolicy()  # 默认执行策略
        self.tool_policies = tool_policies or {}  # 按工具名覆盖的策略
        self.circuit_breaker = circuit_breaker or CircuitBreaker()  # 熔断器
        self.audit_sink = audit_sink or InMemoryAuditSink()  # 审计落盘

    def _finish(
            self, name, call_id, context, arguments, started, attempts, status,
            *, data=None, error=None, degraded=False,
    ) -> dict[str, Any]:
        """
        汇总执行结果、写审计记录并返回统一结果结构。

        Args:
            name: 工具名称
            call_id: 唯一标识每个工具调用
            context: 调用上下文
            arguments: 工具调用参数
            started: 记录开始时间
            attempts: 工具调用尝试次数
            status: 工具调用状态
            data: 工具调用成功时返回的数据
            error: 工具调用失败时返回的错误信息
            degraded: 是否降级返回“依赖不可用”错误
        """
        meta = {  # 元数据：记录工具调用基本信息
            "tool_name": name,
            "call_id": call_id,
            "attempts": attempts,
            "degraded": degraded,  # 是否降级返回
        }
        # 落审计：只记录参数键，不记录参数值
        self.audit_sink.write(
            {
                "call_id": call_id,
                "tool_name": name,
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "run_id": context.run_id,
                "argument_keys": sorted(arguments),
                "status": status,
                "attempts": attempts,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error_code": error["code"] if error else None,
            }
        )
        return {"ok": error is None, "data": data, "error": error, "meta": meta}

    def execute(
            self,
            name: str,
            operation: Callable[[], Any],
            *,
            context: TalentToolContext,
            arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """
        带重试/超时/熔断/审计地执行一个工具调用，并统一返回结果结构。

        Args:
            name: 工具名称
            operation: 工具调用操作函数（返回数据或抛出异常）
            context: 调用上下文
            arguments: 工具调用参数

        Returns:
            形如 {"ok": bool, "data": …|None, "error": …|None, "meta": {…}} 的统一结果。
        """
        call_id = str(uuid4())  # 唯一标识每个工具调用
        started = time.monotonic()  # 记录开始时间
        policy = self.tool_policies.get(name, self.policy)  # 获取工具执行策略
        logger.info(
            "event=tool_call_started function=ToolExecutor.execute "
            "tool_name=%s call_id=%s run_id=%s actor_id=%s "
            "argument_keys=%s max_attempts=%s timeout_seconds=%s",
            name, call_id, context.run_id, context.actor_id,
            sorted(arguments), policy.max_attempts, policy.timeout_seconds,
        )
        # 熔断打开时直接拒绝而不真正执行
        if not self.circuit_breaker.allow(name):
            logger.error(
                "event=tool_call_failed function=ToolExecutor.execute "
                "failure_type=circuit_open tool_name=%s call_id=%s run_id=%s "
                "error_code=circuit_open argument_keys=%s",
                name, call_id, context.run_id, sorted(arguments),
            )
            return self._finish(
                name, call_id, context, arguments, started, 0, "blocked",
                error={"code": "circuit_open", "message": "依赖服务暂时不可用", "retryable": True},
                degraded=True,
            )

        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                # 在单独线程中执行，以便对阻塞的调用施加超时
                future = pool.submit(operation)  # 提交任务到线程池
                data = future.result(timeout=policy.timeout_seconds)  # 等待任务完成或超时
                self.circuit_breaker.succeed(name)  # 成功执行后，重置熔断器
                logger.info(
                    "event=tool_call_completed function=ToolExecutor.execute "
                    "tool_name=%s call_id=%s run_id=%s attempts=%s duration_ms=%s",
                    name, call_id, context.run_id, attempt,
                    round((time.monotonic() - started) * 1000, 2),
                )
                return self._finish(name, call_id, context, arguments, started, attempt, "succeeded", data=data)
            except (TransientToolError, FutureTimeoutError) as exc:
                # 瞬时错误或超时：记录并按退避策略重试
                last_error = exc
                if attempt < policy.max_attempts:
                    logger.warning(
                        "event=tool_call_retry function=ToolExecutor.execute "
                        "failure_type=%s tool_name=%s call_id=%s run_id=%s "
                        "attempt=%s max_attempts=%s",
                        "timeout" if isinstance(exc, FutureTimeoutError) else "transient",
                        name, call_id, context.run_id, attempt, policy.max_attempts,
                    )
                if attempt < policy.max_attempts and policy.backoff_seconds:
                    time.sleep(policy.backoff_seconds * (2 ** (attempt - 1)))
            except (ValueError, PermissionError) as exc:
                # 参数非法或权限不足：不再重试，立即失败
                code = "permission_denied" if isinstance(exc, PermissionError) else "invalid_argument"
                logger.error(
                    "event=tool_call_failed function=ToolExecutor.execute "
                    "failure_type=%s tool_name=%s call_id=%s run_id=%s "
                    "attempt=%s error_code=%s argument_keys=%s",
                    type(exc).__name__, name, call_id, context.run_id,
                    attempt, code, sorted(arguments),
                )
                return self._finish(
                    name, call_id, context, arguments, started, attempt, "failed",
                    error={"code": code, "message": str(exc), "retryable": False},
                )
            except Exception as exc:
                # 未知异常：记录堆栈后向上抛出（不在执行器内吞掉）
                logger.exception(
                    "event=tool_call_crashed function=ToolExecutor.execute "
                    "failure_type=%s tool_name=%s call_id=%s run_id=%s "
                    "attempt=%s argument_keys=%s",
                    type(exc).__name__, name, call_id, context.run_id,
                    attempt, sorted(arguments),
                )
                raise
            finally:
                pool.shutdown(wait=False, cancel_futures=True)  # 线程池资源回收

        # 重试耗尽：打开熔断并降级返回“依赖不可用”
        self.circuit_breaker.fail(name)
        message = "依赖服务调用超时" if isinstance(last_error, FutureTimeoutError) else "依赖服务暂时不可用"
        logger.error(
            "event=tool_call_failed function=ToolExecutor.execute "
            "failure_type=%s tool_name=%s call_id=%s run_id=%s "
            "attempts=%s error_code=dependency_unavailable argument_keys=%s "
            "duration_ms=%s",
            "timeout" if isinstance(last_error, FutureTimeoutError) else "transient",
            name, call_id, context.run_id, policy.max_attempts,
            sorted(arguments), round((time.monotonic() - started) * 1000, 2),
        )
        return self._finish(
            name, call_id, context, arguments, started, policy.max_attempts, "failed",
            error={"code": "dependency_unavailable", "message": message, "retryable": True},
            degraded=True,
        )


class TalentToolService:
    """人才能力服务：实现各工具背后的真实业务逻辑（受租户与权限约束）。"""

    def __init__(
            self,
            *,
            session_factory: Callable[[], Session],  #
            evidence_provider: Callable[..., dict[str, Any]] | None = None,  #
    ) -> None:
        self.session_factory = session_factory
        self.evidence_provider = evidence_provider  # 证据检索提供者（可缺省）

    @staticmethod
    def _check_context(context: TalentToolContext) -> None:
        """校验调用上下文：租户与权限范围必须有效。"""
        if not context.tenant_id.strip():
            raise PermissionError("缺少可信租户")
        if not context.permission_scopes:
            raise PermissionError("缺少可用权限范围")

    def lookup_job_descriptions(
            self,
            query: str,
            *,
            context: TalentToolContext,
            limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        按岗位名称匹配企业维护的岗位 JD，按相关度从高到低返回。

        """
        self._check_context(context) #
        # 归一化查询（去空格、转小写）以便更准确地匹配
        normalized_query = "".join(query.lower().split())
        if not normalized_query:
            raise ValueError("岗位名称不能为空")
        # 取当前租户下所有生效 JD
        with self.session_factory() as db:
            rows = db.scalars(
                select(JobDescription).where(
                    JobDescription.tenant_id == context.tenant_id,
                    JobDescription.status == "active",
                )
            ).all()
        matches = []
        for row in rows:
            normalized_name = "".join(row.name.lower().split())
            # 依次判断精确匹配、包含匹配与模糊匹配得分
            if normalized_query == normalized_name:  # 精确匹配
                match_type = "exact"
                score = 1.0
            elif normalized_query in normalized_name or normalized_name in normalized_query:  # 包含匹配
                match_type = "contains"
                score = 1.0
            else:  # 模糊匹配
                match_type = "fuzzy"
                score = round(SequenceMatcher(None, normalized_query, normalized_name).ratio(), 4)
            if score >= 0.3:
                matches.append(
                    {
                        "job_code": row.job_code,
                        "name": row.name,
                        "match_score": score,
                        "match_type": match_type,
                        "version": row.version,
                        "content": row.content,
                    }
                )
        # 按得分降序、job_code 升序排序后截取 limit 条
        return sorted(matches, key=lambda item: (-item["match_score"], item["job_code"]))[:limit]

    def get_job_description(self, job_code: str, *, context: TalentToolContext) -> dict[str, Any] | None:
        """
        按岗位代码读取单条 JD；不存在或无权限时返回 None。
        Args:
            job_code (str): 岗位代码。
            context (TalentToolContext): 调用上下文。
        Returns:
            dict[str, Any] | None: 岗位详情或 None。
        """
        self._check_context(context)
        with self.session_factory() as db:
            row = db.scalar(
                select(JobDescription).where(
                    JobDescription.job_code == job_code,
                    JobDescription.tenant_id == context.tenant_id,
                    JobDescription.status == "active",
                )
            )
        if row is None:
            return None
        return {
            "job_code": row.job_code,
            "name": row.name,
            "version": row.version,
            "content": row.content,
        }

    def filter_candidates(self, filters: list[FilterCondition], *, context: TalentToolContext) -> list[str]:
        """
        按结构化硬过滤条件筛选授权租户内的候选人工号列表。
        Args:
            filters (list[FilterCondition]): 筛选条件列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            list[str]: 候选人工人工号列表。
        """
        self._check_context(context)
        # 组装查询计划并交给 select_candidate_ids 执行 SQL 过滤
        plan = QueryPlan(task_type=TaskType.FIND_TALENT, filters=filters)
        with self.session_factory() as db:
            return select_candidate_ids(db, plan, tenant_id=context.tenant_id)

    def get_candidate_profiles(self, candidate_ids: list[str], *, context: TalentToolContext) -> list[dict[str, Any]]:
        """
        批量读取授权租户内候选人的结构化基础信息（仅在职且租户匹配）。
        Args:
            candidate_ids (list[str]): 候选人工人工号列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            list[dict[str, Any]]: 候选人详情列表。
        """
        self._check_context(context)
        if not candidate_ids:
            return []
        with self.session_factory() as db:
            rows = db.scalars(
                select(EmployeeProfile).where(
                    EmployeeProfile.tenant_id == context.tenant_id,
                    EmployeeProfile.employee_no.in_(candidate_ids),
                    EmployeeProfile.employment_status == "active",
                ).order_by(EmployeeProfile.employee_no)
            ).all()
        return [
            {"candidate_id": row.employee_no, "name": row.name, "region": row.region,
             "current_position": row.current_position, "job_level": row.job_level,
             "years_of_experience": row.years_of_experience, "department": row.department}
            for row in rows
        ]

    def search_candidate_evidence(
            self, query: str, candidate_ids: list[str], *, context: TalentToolContext,
    ) -> dict[str, Any]:
        """
        在候选人范围内检索材料证据并返回含 Evidence Pack 2.0 的结果。
        Args:
            query (str): 查询关键字。
            candidate_ids (list[str]): 候选人工人工号列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            dict[str, Any]: 检索结果。
        """
        logger.info(
            "event=talent_evidence_search_started "
            "function=TalentToolService.search_candidate_evidence "
            "run_id=%s candidate_ids=%s query_chars=%s permission_scope_count=%s",
            context.run_id, candidate_ids, len(query), len(context.permission_scopes),
        )
        self._check_context(context)
        if self.evidence_provider is None:
            raise TransientToolError("evidence_provider_not_configured")
        # 委托证据提供者执行混合检索与证据抽取
        result = self.evidence_provider(
            query=query,
            candidate_ids=candidate_ids,
            tenant_id=context.tenant_id,
            permission_scopes=list(context.permission_scopes),
            include_evidence_pack=True,
        )
        # 校验证据协议版本必须为 2.0
        for pack in result.get("evidence_packs", []):
            if pack.get("schema_version") != "2.0":
                raise ValueError("不支持的证据协议版本")
        logger.info(
            "event=talent_evidence_search_completed "
            "function=TalentToolService.search_candidate_evidence "
            "run_id=%s candidate_ids=%s chunk_count=%s evidence_pack_count=%s",
            context.run_id, candidate_ids, len(result.get("chunks", [])),
            len(result.get("evidence_packs", [])),
        )
        return result


def build_talent_tools(service: TalentToolService, executor: ToolExecutor):
    """构造并返回人才相关的 LangChain 工具列表（岗位查找/候选人筛选/证据检索/档案读取）。"""

    @tool
    def lookup_job_descriptions(
            query: Annotated[str, Field(min_length=1, max_length=200)],
            runtime: ToolRuntime[TalentToolContext],
            limit: Annotated[int, Field(ge=1, le=10)] = 5,
    ):
        """
        按岗位名称查询企业已经维护的岗位 JD，不生成或补写岗位要求。
        Args:
            query (str): 岗位名称查询。
            limit (int): 返回结果数量限制。
            context (TalentToolContext): 调用上下文。
        Returns:
            list[dict[str, Any]]: 岗位详情列表。
        """
        return executor.execute(
            "lookup_job_descriptions",
            lambda: service.lookup_job_descriptions(query, limit=limit, context=runtime.context),
            context=runtime.context,
            arguments={"query": query, "limit": limit},
        )

    @tool
    def filter_candidates(
            filters: Annotated[list[FilterCondition], Field(max_length=20)],
            runtime: ToolRuntime[TalentToolContext],
    ):
        """
        按地区、职级、年限等结构化条件筛选授权租户内的候选人。
        Args:
            filters (list[FilterCondition]): 筛选条件列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            list[str]: 候选人工人工号列表。
        """
        return executor.execute(
            "filter_candidates", lambda: service.filter_candidates(filters, context=runtime.context),
            context=runtime.context, arguments={"filters": filters},
        )

    @tool
    def search_candidate_evidence(
            query: Annotated[str, Field(min_length=1, max_length=500)],
            candidate_ids: Annotated[list[str], Field(min_length=1, max_length=100)],
            runtime: ToolRuntime[TalentToolContext],
    ):
        """
        在给定候选人范围内检索材料证据并返回 Candidate Evidence Pack 2.0。
        Args:
            query (str): 查询关键字。
            candidate_ids (list[str]): 候选人工人工号列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            dict[str, Any]: 检索结果。
        """
        return executor.execute(
            "search_candidate_evidence",
            lambda: service.search_candidate_evidence(query, candidate_ids, context=runtime.context),
            context=runtime.context, arguments={"query": query, "candidate_ids": candidate_ids},
        )

    @tool
    def get_candidate_profiles(
            candidate_ids: Annotated[list[str], Field(min_length=1, max_length=100)],
            runtime: ToolRuntime[TalentToolContext],
    ):
        """
        批量读取授权租户内候选人的结构化基础信息，用于评估取证。
        Args:
            candidate_ids (list[str]): 候选人工人工号列表。
            context (TalentToolContext): 调用上下文。
        Returns:
            list[dict[str, Any]]: 候选人详情列表。
        """
        return executor.execute(
            "get_candidate_profiles",
            lambda: service.get_candidate_profiles(candidate_ids, context=runtime.context),
            context=runtime.context, arguments={"candidate_ids": candidate_ids},
        )

    return [lookup_job_descriptions, filter_candidates, search_candidate_evidence, get_candidate_profiles]


def build_candidate_provider(
        service: TalentToolService,
        *,
        compile_filters: Callable[[str], list[FilterCondition]],
):
    """
    把结构化筛选服务适配到决策图的 provider 契约（provider 形式）。
    Args:
        service (TalentToolService): 人才工具服务实例。
        compile_filters (Callable[[str], list[FilterCondition]]): 筛选条件编译函数，将原始文本转换为结构化筛选条件。
    Returns:
        Callable[[dict[str, Any], Any]: 候选人提供器函数，根据原始文本筛选候选人。
    """

    def provider(request: dict[str, Any], decision_context: Any) -> list[str]:
        """
        从决策上下文构造工具调用上下文，并解析筛选条件后执行。
        Args:
            request (dict[str, Any]): 包含原始文本的请求。
            decision_context (Any): 决策上下文，包含租户 ID 和权限范围。
        Returns:
            list[str]: 筛选后的候选人人工人工号列表。
        """
        context = TalentToolContext(
            tenant_id=decision_context.tenant_id,
            permission_scopes=tuple(decision_context.permission_scopes),
            actor_id="langgraph",
        )
        return service.filter_candidates(compile_filters(request["original_text"]), context=context)

    return provider
