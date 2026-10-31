from __future__ import annotations

# 必须在导入任何 app 模块前把默认 PostgreSQL 指向 SQLite，规避本机缺 psycopg 驱动。
# talent_request_graph -> database 在模块导入时就 create_engine，本测试不访问真实库。
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_talent_confirm_job_node.db")

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.retrieval.query_plan import SemanticRequirement
from app.talent.talent_decision_graph import DecisionContext
from app.talent.talent_request_graph import TalentRequestDraft, build_talent_request_graph


def _context() -> DecisionContext:
    return DecisionContext(tenant_id="course-demo", permission_scopes=("hr_private",))


def _job(code: str, name: str, *, match_type: str, content: str = "负责企业知识库与 Agent 应用建设") -> dict:
    return {
        "job_code": code,
        "name": name,
        "match_score": 1.0,
        "match_type": match_type,
        "version": 1,
        "content": content,
    }


class FixtureInterpreter:
    """请求解释器：命中岗位后，用岗位名称与内容生成查询计划。"""

    def __call__(self, request_text: str, selected_job: dict | None = None) -> TalentRequestDraft:
        if selected_job is not None:
            return TalentRequestDraft(
                input_mode="job_name",
                job_query=selected_job["name"],
                filters=[],
                semantic_requirements=[
                    SemanticRequirement(requirement_id="S1", query=selected_job["content"], required=True)
                ],
                evaluation_preferences=[],
                clarifications=[],
            )
        return TalentRequestDraft(
            input_mode="job_name",
            job_query=request_text,
            filters=[],
            semantic_requirements=[],
            evaluation_preferences=[],
            clarifications=[],
        )


def _build_graph(matches: list[dict]):
    """构造一个必然进入 _confirm_job 的图：job_lookup 总是返回多个候选岗位。"""
    return build_talent_request_graph(
        request_interpreter=FixtureInterpreter(),
        job_lookup=lambda query, context: matches,
        checkpointer=InMemorySaver(),
    )


def test_confirm_job_interrupt_payload_contains_selection_options():
    """_confirm_job 暂停时给出完整的确认载荷：类型、问题、请求文本、候选选项、可用动作。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
            _job("JD-AI-002", "AI 应用工程师（平台方向）", match_type="contains"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-payload"}}

    interrupted = graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    payload = interrupted["__interrupt__"][0].value
    assert payload["type"] == "job_selection"
    assert payload["question"] == "请选择本次人才评估使用的岗位 JD"
    assert payload["request_text"] == "AI 应用工程师"
    assert [item["job_code"] for item in payload["options"]] == ["JD-AI-001", "JD-AI-002"]
    assert payload["actions"] == ["select", "cancel"]


def test_confirm_job_select_resumes_and_builds_plan_with_chosen_job():
    """用户在确认载荷中选择一个岗位后，图用该岗位构建查询计划并走完。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains", content="负责大模型平台建设"),
            _job("JD-AI-002", "AI 应用工程师（平台方向）", match_type="contains", content="负责企业知识库与 Agent 应用建设"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-select"}}

    interrupted = graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())
    assert interrupted["status"] == "job_confirmed" or "__interrupt__" in interrupted

    resumed = graph.invoke(
        Command(resume={"action": "select", "job_code": "JD-AI-002"}),
        config,
        context=_context(),
    )

    assert resumed["status"] == "plan_ready"
    assert resumed["selected_job"]["job_code"] == "JD-AI-002"
    assert resumed["selected_job"]["name"] == "AI 应用工程师（平台方向）"
    assert resumed["talent_request"]["target_job"]["job_code"] == "JD-AI-002"
    assert resumed["query_plan"]["semantic_requirements"][0]["query"] == "负责企业知识库与 Agent 应用建设"


def test_confirm_job_cancel_resumes_and_ends_thread():
    """用户在确认载荷中选择取消，图以 cancelled 终结且线程不再有后续节点。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
            _job("JD-AI-002", "AI 应用工程师（平台方向）", match_type="contains"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-cancel"}}

    graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    result = graph.invoke(Command(resume={"action": "cancel"}), config, context=_context())

    assert result["status"] == "cancelled"
    assert result["candidate_ids"] == []
    assert graph.get_state(config).next == ()


def test_confirm_job_rejects_unknown_job_code():
    """恢复数据中的 job_code 不在候选列表时，_confirm_job 抛出 ValueError。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-bad-code"}}

    graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    with pytest.raises(ValueError, match="job_code 不在待确认岗位列表中"):
        graph.invoke(
            Command(resume={"action": "select", "job_code": "JD-NOT-EXISTS"}),
            config,
            context=_context(),
        )


def test_confirm_job_rejects_invalid_action():
    """恢复数据中 action 不是 select/cancel 时，_confirm_job 抛出 ValueError。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-bad-action"}}

    graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    with pytest.raises(ValueError, match="action 必须是 select 或 cancel"):
        graph.invoke(
            Command(resume={"action": "delete", "job_code": "JD-AI-001"}),
            config,
            context=_context(),
        )


def test_confirm_job_rejects_non_dict_resume():
    """恢复数据不是 JSON 对象（dict）时，_confirm_job 抛出 ValueError。"""
    graph = _build_graph(
        [
            _job("JD-AI-001", "高级 AI 应用工程师", match_type="contains"),
        ]
    )
    config = {"configurable": {"thread_id": "cfj-non-dict"}}

    graph.invoke({"request_text": "AI 应用工程师"}, config, context=_context())

    with pytest.raises(ValueError, match="恢复数据必须是 JSON 对象"):
        graph.invoke(Command(resume="not-a-dict"), config, context=_context())