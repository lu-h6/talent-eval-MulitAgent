"""查询计划 DSL：过滤条件、语义要求、偏好与可执行性校验。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session

from app.db.models import EmployeeProfile


class TaskType(StrEnum):
    """查询任务的类型。"""
    FIND_TALENT = "find_talent"  # 找人：检索候选人
    LOOKUP_PROFILE = "lookup_profile"  # 查档：读取候选人资料


class FilterField(StrEnum):
    """可从用户原话中提取并用于结构化硬过滤的人才字段。"""
    AGE = "age"  # 年龄
    REGION = "region"  # 地区
    DEPARTMENT = "department"  # 部门
    JOB_LEVEL = "job_level"  # 职级
    YEARS_OF_EXPERIENCE = "years_of_experience"  # 工作年限
    CURRENT_POSITION = "current_position"  # 当前岗位
    EMPLOYMENT_STATUS = "employment_status"  # 在职状态


class FilterCondition(BaseModel):
    """一条结构化硬过滤条件（由 LLM 从用户原话编译而来）。"""
    field: FilterField = Field(description="结构化人才字段，只能使用 FilterField 枚举中的值")
    operator: Literal["eq", "in", "lt", "lte", "gt", "gte"] = Field(
        description="比较操作符：eq 等于，in 属于集合，lt/lte/gt/gte 为数值比较"
    )
    value: str | int | float | list[str] = Field(description="从用户原话提取的比较值，不得补充用户未提供的阈值")

    @model_validator(mode="after")
    def validate_operator_and_value(self):
        """基于字段类型校验操作符与值类型的匹配关系。"""
        numeric = {FilterField.AGE, FilterField.YEARS_OF_EXPERIENCE}
        # 范围运算符只允许用于数值字段
        if self.operator in {"lt", "lte", "gt", "gte"} and self.field not in numeric:
            raise ValueError("范围运算符只允许用于数值字段")
        # in 必须接收列表，非 in 不允许列表
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("in 运算符必须接收列表")
        if self.operator != "in" and isinstance(self.value, list):
            raise ValueError("只有 in 运算符允许列表值")
        return self


class SemanticRequirement(BaseModel):
    """一条需要在人才材料中通过语义检索匹配的软性要求。"""
    requirement_id: str = Field(pattern=r"^S\d+$", description="语义要求编号，如 S1、S2")
    query: str = Field(min_length=1, max_length=500, description="需要在人才材料中检索的经历、技能或成果要求")
    required: bool = Field(default=True, description="用户是否把该语义要求表达为必须条件")


class Clarification(BaseModel):
    """一条需要向用户澄清的含糊/缺失/矛盾条件。"""
    expression: str  # 含糊的表达
    reason: str  # 需要澄清的原因


class QueryPlan(BaseModel):
    """一次人才查询的完整执行计划，由 LLM 编译后供执行器消费。"""
    task_type: TaskType = Field(description="查询任务类型，用于选择后续执行器")
    filters: list[FilterCondition] = Field(default_factory=list, description="可由 PostgreSQL 精确执行的结构化硬条件")
    semantic_requirements: list[SemanticRequirement] = Field(default_factory=list, description="需要调用混合检索寻找材料证据的条件")
    preferences: list[str] = Field(default_factory=list, description="优先、倾向等软条件，保留给后续排序或评估，不作为 SQL 排除条件")
    clarifications: list[Clarification] = Field(default_factory=list, description="缺少阈值、含义不明或互相矛盾的条件")

    @property
    def executable(self) -> bool:
        """是否存在需要澄清的条件：无澄清即可直接执行。"""
        return not self.clarifications


def _age_boundary(value: int, today: date) -> date:
    """把“年龄多少岁”换算为出生日期边界（处理闰年 2/29 边界）。"""
    try:
        return today.replace(year=today.year - value)
    except ValueError:
        return today.replace(month=2, day=28, year=today.year - value)


@dataclass(frozen=True)
class FilterFieldSpec:
    """单个过滤字段的描述与如何把条件编译为 SQL 表达式。"""
    description: str  # 字段说明（用于提示 LLM）
    operators: frozenset[str]  # 该字段支持的操作符集合
    build: Callable[[FilterCondition, date], Any]  # 编译函数：返回 SQLAlchemy 表达式


def _column_spec(column: Any, description: str, operators: set[str]) -> FilterFieldSpec:
    """为字典列生成通用的 FilterFieldSpec（按操作符构造列比较表达式）。"""
    def build(item: FilterCondition, _: date):
        """拼装 SQL 片段与对应参数。"""
        value = item.value
        return {
            "eq": lambda: column == value,
            "in": lambda: column.in_(value),
            "lt": lambda: column < value,
            "lte": lambda: column <= value,
            "gt": lambda: column > value,
            "gte": lambda: column >= value,
        }[item.operator]()

    return FilterFieldSpec(description, frozenset(operators), build)


def _age_spec(item: FilterCondition, today: date):
    """年龄条件的特殊编译：运算对象逆转为出生日期比较。"""
    if item.operator not in {"lt", "lte", "gt", "gte"}:
        raise ValueError("age 只支持 lt、lte、gt、gte")
    boundary = _age_boundary(int(item.value), today)
    # 年龄小于 X 岁 => 出生日期大于 X 年前，操作符需取反
    reversed_operator = {"lt": ">", "lte": ">=", "gt": "<", "gte": "<="}[item.operator]
    return EmployeeProfile.birth_date.op(reversed_operator)(boundary)


# 全部可用过滤字段的注册表；LLM 只能引用这里的字段与操作符
FILTER_FIELD_REGISTRY = {
    FilterField.AGE: FilterFieldSpec("年龄，由 birth_date 按查询基准日换算", frozenset({"lt", "lte", "gt", "gte"}), _age_spec),
    FilterField.REGION: _column_spec(EmployeeProfile.region, "工作地区", {"eq", "in"}),
    FilterField.DEPARTMENT: _column_spec(EmployeeProfile.department, "所属部门", {"eq", "in"}),
    FilterField.JOB_LEVEL: _column_spec(EmployeeProfile.job_level, "职级，编码格式为 L1、L2 等", {"eq", "in"}),
    FilterField.YEARS_OF_EXPERIENCE: _column_spec(EmployeeProfile.years_of_experience, "工作年限", {"eq", "lt", "lte", "gt", "gte"}),
    FilterField.CURRENT_POSITION: _column_spec(EmployeeProfile.current_position, "当前岗位", {"eq", "in"}),
    FilterField.EMPLOYMENT_STATUS: _column_spec(EmployeeProfile.employment_status, "在职状态", {"eq", "in"}),
}


def filter_dsl_catalog() -> str:
    """生成供 LLM 使用的 Filter DSL 字段目录文本。"""
    return "\n".join(
        f"- {field.value}: {spec.description}; operators={','.join(sorted(spec.operators))}"
        for field, spec in FILTER_FIELD_REGISTRY.items()
    )


def build_candidate_statement(
    plan: QueryPlan, *, tenant_id: str, today: date | None = None
) -> Select[tuple[str]]:
    """把 QueryPlan 的结构化硬条件编译为查询候选人 employee_no 的 SQL 语句。

    Args:
        plan: 已澄清可执行的查询计划。
        tenant_id: 租户隔离标识。
        today: 查询基准日（用于年龄换算），默认当天。

    Returns:
        按 employee_no 排序的 Select 语句。

    Raises:
        ValueError: 当租户为空、计划未澄清或字段使用了不支持的操作符时抛出。
    """
    if not tenant_id:
        raise ValueError("tenant_id 不能为空")
    if not plan.executable:
        raise ValueError("查询计划仍有待澄清条件")

    current_day = today or date.today()
    # 基础条件：租户隔离 + 只在职
    clauses: list[Any] = [
        EmployeeProfile.tenant_id == tenant_id,
        EmployeeProfile.employment_status == "active",
    ]
    # 把每条硬过滤条件编译为 SQL 表达式
    for item in plan.filters:
        spec = FILTER_FIELD_REGISTRY[item.field]
        if item.operator not in spec.operators:
            raise ValueError(f"{item.field.value} 不支持 {item.operator} 操作符")
        clauses.append(spec.build(item, current_day))

    return select(EmployeeProfile.employee_no).where(and_(*clauses)).order_by(EmployeeProfile.employee_no)


def select_candidate_ids(db: Session, plan: QueryPlan, *, tenant_id: str, today: date | None = None) -> list[str]:
    """执行硬过滤并返回命中的候选人工号列表。"""
    return list(db.scalars(build_candidate_statement(plan, tenant_id=tenant_id, today=today)).all())


def execute_composite_search(
    plan: QueryPlan,
    *,
    candidate_ids: list[str],
    search_requirement: Any,
) -> dict[str, Any]:
    """执行复合检索（向量召回 + 关键词召回）。"""
    if not plan.executable:
        raise ValueError("查询计划仍有待澄清条件")
    if not candidate_ids:
        return {
            "task_type": plan.task_type,
            "candidate_ids": [],
            "evidence_by_requirement": {},
            "preferences": plan.preferences,
        }

    # 仅在 SQL 命中的候选人集合内执行每条语义要求检索
    evidence = {
        requirement.requirement_id: search_requirement(
            query=requirement.query,
            candidate_ids=candidate_ids,
        )
        for requirement in plan.semantic_requirements
    }
    return {
        "task_type": plan.task_type,
        "candidate_ids": candidate_ids,
        "evidence_by_requirement": evidence,
        "preferences": plan.preferences,
    }


class QueryOptimizationPlan(BaseModel):
    """
    LLM 生成的多路检索优化方案。

    优化策略：rewrite、multi_query、decompose。
    """
    strategy: Literal["rewrite", "multi_query", "decompose"] # 优化策略
    queries: list[str] = Field(min_length=1, max_length=3, description="最多 3 条检索查询，不能改变原始约束") # 优化后的检索查询
    reason: str # 优化原因


def choose_optimization_strategy(*, hit_count: int, requirement_count: int, expression_is_vague: bool = False) -> str | None:
    """根据首轮命中数选择是否优化及优化策略。

    无命中时：要求多于 1 条则分解；表达含糊则改写；否则多路查询。
    """
    if hit_count > 0:
        return None
    if requirement_count > 1:
        return "decompose"
    if expression_is_vague:
        return "rewrite"
    return "multi_query"


def optimize_semantic_query(requirement: SemanticRequirement, *, strategy: str, model: Any) -> QueryOptimizationPlan:
    """让 LLM 依据指定策略把单条语义要求改写为多条检索查询。"""
    structured_model = model.with_structured_output(QueryOptimizationPlan)
    return structured_model.invoke(
        [
            ("system", "根据指定策略生成检索查询。保留原始人才条件，不增加年限、职级或技能要求。最多返回 3 条查询。"),
            ("user", f"strategy={strategy}\noriginal_requirement={requirement.query}"),
        ]
    )


def merge_query_results(result_sets: list[list[Any]]) -> list[Any]:
    """
    合并多个查询结果集，保留每个分块的首次出现（即排名最高的那份）。
    """
    merged: list[Any] = []
    seen: set[str] = set()
    for result_set in result_sets:
        for item in result_set:
            chunk_id = item.chunk_id if hasattr(item, "chunk_id") else item["chunk_id"]
            # 同一分块只保留首次出现（即排名最高的那份）
            if chunk_id not in seen:
                seen.add(chunk_id)
                merged.append(item)
    return merged


def search_with_optimization(
    requirement: SemanticRequirement,
    *,
    search_query: Callable[[str], list[Any]],
    model: Any,
    requirement_count: int = 1,
    expression_is_vague: bool = False,
) -> dict[str, Any]:
    """带优化策略的语义检索：首轮无命中时自动改写/分解/多路后重试并合并去重。"""
    first_pass = search_query(requirement.query)
    strategy = choose_optimization_strategy(
        hit_count=len(first_pass),
        requirement_count=requirement_count,
        expression_is_vague=expression_is_vague,
    )
    if strategy is None:
        return {"strategy": None, "queries": [requirement.query], "results": first_pass}

    # 生成优化后的多条查询并逐条检索，最后合并去重
    optimization = optimize_semantic_query(requirement, strategy=strategy, model=model)
    fallback_results = [search_query(query) for query in optimization.queries]
    return {
        "strategy": optimization.strategy,
        "queries": [requirement.query, *optimization.queries],
        "results": merge_query_results([first_pass, *fallback_results]),
    }


QUERY_PLAN_SYSTEM_PROMPT = """你是人才查询计划编译器。只把用户明确表达的条件转换为 QueryPlan。
硬条件只允许使用下面的 Filter DSL，禁止生成 SQL：
{filter_dsl}
经历、技能、项目成果等材料内容写入 semantic_requirements。
“优先”等软偏好写入 preferences，不能改写为硬条件。
含糊表达写入 clarifications，禁止自行补充阈值。权限、租户和密级不从用户文本提取。"""


def compile_query_plan(query: str, model: Any) -> QueryPlan:
    """
    让 LLM 把用户的自然语言查询编译为结构化的 QueryPlan。

    """
    structured_model = model.with_structured_output(QueryPlan)
    return structured_model.invoke(
        [("system", QUERY_PLAN_SYSTEM_PROMPT.format(filter_dsl=filter_dsl_catalog())), ("user", query)]
    )

# 你是人才查询计划编译器。只把用户明确表达的条件转换为 QueryPlan。
# 硬条件只允许使用下面的 Filter DSL，禁止生成 SQL：
# - age: 年龄，由 birth_date 按查询基准日换算; operators=gt,gte,lt,lte
# - region: 工作地区; operators=eq,in
# - department: 所属部门; operators=eq,in
# - job_level: 职级，编码格式为 L1、L2 等; operators=eq,in
# - years_of_experience: 工作年限; operators=eq,gt,gte,lt,lte
# - current_position: 当前岗位; operators=eq,in
# - employment_status: 在职状态; operators=eq,in
# 经历、技能、项目成果等材料内容写入 semantic_requirements。
# “优先”等软偏好写入 preferences，不能改写为硬条件。
# 含糊表达写入 clarifications，禁止自行补充阈值。权限、租户和密级不从用户文本提取。