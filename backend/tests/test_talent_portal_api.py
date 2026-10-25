from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.talent_portal import get_agent_gateway, get_talent_service
from app.db.models import Base, EmployeeProfile, JobDescription
from app.main import create_app
from app.talent.agent_gateway import AgentServerError
from app.talent.talent_tools import TalentToolService

HEADERS = {"X-Tenant-Id": "tenant-a", "X-Permission-Scopes": "hr_private"}


class FakeGateway:
    """用内存字典模拟 Agent Server 的线程/状态/运行控制面。"""

    def __init__(self) -> None:
        self.threads: dict[str, dict[str, Any]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.sequence = 0
        self.runs: list[dict[str, Any]] = []

    async def create_thread(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        thread_id = f"thread-{self.sequence}"
        thread = {
            "thread_id": thread_id,
            "status": "idle",
            "metadata": metadata or {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "state_updated_at": "2026-01-01T00:00:00+00:00",
        }
        self.threads[thread_id] = thread
        self.states[thread_id] = {"values": {}, "next": [], "interrupts": []}
        return thread

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        if thread_id not in self.threads:
            raise AgentServerError("会话或运行不存在", 404)
        return self.threads[thread_id]

    async def list_threads(
        self,
        *,
        metadata: dict[str, Any],
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = [
            item
            for item in self.threads.values()
            if all(item["metadata"].get(key) == value for key, value in metadata.items())
        ]
        return items[offset : offset + limit]

    async def delete_thread(self, thread_id: str) -> None:
        self.threads.pop(thread_id, None)
        self.states.pop(thread_id, None)

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        if thread_id not in self.states:
            raise AgentServerError("会话或运行不存在", 404)
        return self.states[thread_id]

    async def ensure_available(self) -> None:
        return None

    async def start_run(
        self,
        thread_id: str,
        *,
        request_text: str,
        tenant_id: str,
        permission_scopes: list[str],
    ) -> dict[str, Any]:
        self.runs.append(
            {
                "thread_id": thread_id,
                "request_text": request_text,
                "tenant_id": tenant_id,
                "permission_scopes": permission_scopes,
            }
        )
        return {"run_id": f"run-{len(self.runs)}", "thread_id": thread_id, "status": "pending"}

    async def stream_run(self, thread_id: str, **_: Any):
        yield b'event: metadata\ndata: {"run_id":"run-stream"}\n\n'
        yield b'event: values\ndata: {"status":"received"}\n\n'


@pytest.fixture()
def service(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'portal.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                JobDescription(
                    job_code="JD-AI-001",
                    tenant_id="tenant-a",
                    name="高级 AI 应用工程师",
                    content="负责企业知识库与 Agent 应用建设",
                    version=2,
                ),
                JobDescription(
                    job_code="JD-JAVA-001",
                    tenant_id="tenant-a",
                    name="Java 后端工程师",
                    content="负责交易系统后端开发",
                    version=1,
                ),
                JobDescription(
                    job_code="JD-AI-OTHER",
                    tenant_id="tenant-b",
                    name="高级 AI 应用工程师",
                    content="其他租户数据",
                    version=1,
                ),
                EmployeeProfile(
                    employee_no="C001",
                    tenant_id="tenant-a",
                    name="林晓岚",
                    region="上海",
                    department="平台研发",
                    current_position="AI 应用工程师",
                    job_level="L3",
                    years_of_experience=6,
                ),
                EmployeeProfile(
                    employee_no="C002",
                    tenant_id="tenant-b",
                    name="跨租户",
                    region="北京",
                ),
            ]
        )
        db.commit()
    return TalentToolService(session_factory=lambda: Session(engine))


@pytest.fixture()
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture()
def client(service, gateway):
    app = create_app()
    app.dependency_overrides[get_talent_service] = lambda: service
    app.dependency_overrides[get_agent_gateway] = lambda: gateway
    return TestClient(app)


def test_job_descriptions_list_and_query_are_tenant_scoped(client):
    response = client.get("/api/job-descriptions", headers=HEADERS)

    assert response.status_code == 200
    assert {item["job_code"] for item in response.json()} == {"JD-AI-001", "JD-JAVA-001"}

    response = client.get("/api/job-descriptions", params={"query": "AI"}, headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["job_code"] == "JD-AI-001"
    assert response.json()[0]["match_type"] == "contains"


def test_job_description_detail_rejects_other_tenant(client):
    response = client.get("/api/job-descriptions/JD-AI-OTHER", headers=HEADERS)

    assert response.status_code == 404


def test_candidate_profiles_return_only_active_same_tenant_items(client):
    response = client.post(
        "/api/candidates/profiles",
        headers=HEADERS,
        json={"candidate_ids": ["C001", "C002", "MISSING"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["candidate_id"] for item in payload["items"]] == ["C001"]
    assert payload["items"][0]["department"] == "平台研发"
    assert payload["missing"] == ["C002", "MISSING"]


def test_thread_state_hides_other_tenant_thread(client, gateway):
    thread = client.post("/api/talent/threads", headers=HEADERS, json={}).json()
    state = client.get(f"/api/talent/threads/{thread['thread_id']}/state", headers=HEADERS)

    assert state.status_code == 200
    assert state.json()["metadata"]["tenant_id"] == "tenant-a"

    gateway.threads[thread["thread_id"]]["metadata"]["tenant_id"] = "tenant-b"
    hidden = client.get(f"/api/talent/threads/{thread['thread_id']}/state", headers=HEADERS)

    assert hidden.status_code == 404


def test_thread_state_collects_subgraph_interrupts(client, gateway):
    thread = client.post("/api/talent/threads", headers=HEADERS, json={}).json()
    gateway.states[thread["thread_id"]] = {
        "values": {"status": "job_confirmed"},
        "next": ["confirm_job"],
        "interrupts": [],
        "tasks": [
            {
                "interrupts": [
                    {
                        "id": "interrupt-1",
                        "value": {"type": "job_selection", "options": [{"job_code": "JD-AI-001"}]},
                    }
                ],
                "state": None,
            }
        ],
    }

    response = client.get(f"/api/talent/threads/{thread['thread_id']}/state", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["interrupts"][0]["value"]["type"] == "job_selection"


def test_create_evaluation_returns_202_with_thread_and_run_ids(client):
    response = client.post(
        "/api/talent/evaluations",
        headers=HEADERS,
        json={"request_text": "筛选上海 AI 工程师"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["evaluation_id"] == payload["thread_id"]
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "pending"


def test_report_is_available_after_graph_completion(client, gateway):
    thread = client.post("/api/talent/threads", headers=HEADERS, json={}).json()
    gateway.states[thread["thread_id"]] = {
        "values": {
            "status": "completed",
            "report": "# 人才评估报告",
            "ranking": ["C001"],
            "candidate_ids": ["C001"],
        },
        "next": [],
        "interrupts": [],
    }

    response = client.get(f"/api/talent/evaluations/{thread['thread_id']}/report", headers=HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"] == "# 人才评估报告"
    assert payload["ranking"] == ["C001"]


def test_report_reports_pending_interrupt_when_not_completed(client, gateway):
    thread = client.post("/api/talent/threads", headers=HEADERS, json={}).json()
    gateway.states[thread["thread_id"]] = {
        "values": {"status": "clarification_required"},
        "next": ["resolve_requirements"],
        "interrupts": [],
        "tasks": [
            {
                "interrupts": [],
                "state": {
                    "tasks": [
                        {
                            "interrupts": [
                                {
                                    "value": {
                                        "type": "requirement_revision",
                                        "interaction_id": "rr-v0",
                                        "actions": ["revise", "cancel"],
                                    }
                                }
                            ]
                        }
                    ]
                },
            }
        ],
    }

    response = client.get(f"/api/talent/evaluations/{thread['thread_id']}/report", headers=HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["interrupts"][0]["value"]["type"] == "requirement_revision"


def test_thread_run_streams_sse_events(client):
    thread = client.post("/api/talent/threads", headers=HEADERS, json={}).json()

    with client.stream(
        "POST",
        f"/api/talent/threads/{thread['thread_id']}/runs",
        headers=HEADERS,
        json={"request_text": "继续"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: metadata" in body
    assert "event: values" in body


def test_missing_permission_header_is_rejected(client):
    response = client.get("/api/job-descriptions", headers={"X-Tenant-Id": "tenant-a"})

    assert response.status_code == 422
