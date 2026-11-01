"""评估与推荐演示数据：员工、岗位 JD 与可选的项目复盘材料。

用法：
    # 只写入员工和岗位（默认，幂等）
    python -m scripts.seed_portal_demo

    # 同时上传 samples/portal-demo 下的 Markdown 材料，并等待解析/切片/索引
    python -m scripts.seed_portal_demo --with-documents --api http://127.0.0.1:18080/api

注意：本脚本会写 employee_profiles、job_descriptions、knowledge_bases、documents，
并可能写对象存储和 Milvus。执行前必须获得数据写入授权。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import EmployeeProfile, JobDescription, KnowledgeBase

TENANT_ID = "course-demo"
KB_NAME = "人才评估演示库"
KB_DESCRIPTION = "用于评估与推荐页面演示的候选人项目复盘材料"
DEMO_DIR = Path(__file__).resolve().parents[1] / "samples" / "portal-demo"

EMPLOYEES = [
    {
        "employee_no": "C001",
        "name": "林晓岚",
        "gender": "女",
        "birth_date": "1994-03-12",
        "region": "上海",
        "current_position": "高级 AI 应用工程师",
        "job_level": "L4",
        "years_of_experience": 7,
        "department": "平台研发部",
        "employment_status": "active",
    },
    {
        "employee_no": "C002",
        "name": "陈泽宇",
        "gender": "男",
        "birth_date": "1990-08-21",
        "region": "上海",
        "current_position": "AI 平台工程师",
        "job_level": "L4",
        "years_of_experience": 9,
        "department": "模型平台部",
        "employment_status": "active",
    },
    {
        "employee_no": "C003",
        "name": "周雨桐",
        "gender": "女",
        "birth_date": "1996-11-05",
        "region": "杭州",
        "current_position": "数据产品经理",
        "job_level": "L3",
        "years_of_experience": 5,
        "department": "数据产品部",
        "employment_status": "active",
    },
    {
        "employee_no": "C004",
        "name": "赵明远",
        "gender": "男",
        "birth_date": "1988-02-18",
        "region": "北京",
        "current_position": "后端架构师",
        "job_level": "L5",
        "years_of_experience": 12,
        "department": "交易系统部",
        "employment_status": "active",
    },
    {
        "employee_no": "C005",
        "name": "郭思远",
        "gender": "男",
        "birth_date": "1993-06-30",
        "region": "深圳",
        "current_position": "前端工程师",
        "job_level": "L3",
        "years_of_experience": 6,
        "department": "用户体验部",
        "employment_status": "active",
    },
    {
        "employee_no": "C006",
        "name": "孙可欣",
        "gender": "女",
        "birth_date": "1997-09-14",
        "region": "上海",
        "current_position": "算法工程师",
        "job_level": "L3",
        "years_of_experience": 4,
        "department": "算法部",
        "employment_status": "active",
    },
]

JOBS = [
    {
        "job_code": "JD-AI-001",
        "name": "高级 AI 应用工程师",
        "version": 2,
        "content": (
            "岗位职责：负责企业知识库、RAG、Agent 应用与评测体系建设；设计检索链路、切分策略、"
            "重排与引用回链；建立离线评测集和线上质量监控。\n"
            "任职要求：5 年以上后端或 AI 应用经验；熟悉 Python、LangChain/LangGraph、向量数据库；"
            "有 RAG 项目从 0 到 1 落地经验；能独立设计检索质量与回答质量评测方案。"
        ),
    },
    {
        "job_code": "JD-AI-002",
        "name": "AI 应用工程师（平台方向）",
        "version": 1,
        "content": (
            "岗位职责：负责模型服务、Agent 平台、工具注册中心与可观测性建设；设计多租户运行、"
            "超时重试、熔断与审计；推动平台在业务团队落地。\n"
            "任职要求：熟悉 LangGraph/Agent 运行时、FastAPI、PostgreSQL、Redis；有平台化和"
            "稳定性治理经验；理解租户隔离与权限上下文。"
        ),
    },
    {
        "job_code": "JD-DATA-001",
        "name": "数据产品经理（人才画像）",
        "version": 1,
        "content": (
            "岗位职责：负责人才画像标签体系、数据口径、画像详情与内部人才市场；推动 HR、招聘和"
            "业务共建标签标准；建立数据质量与使用效果指标。\n"
            "任职要求：3 年以上数据产品经验；熟悉 SQL 与指标体系；有 HR SaaS、招聘或人才盘点"
            "产品经验优先；具备跨部门推动能力。"
        ),
    },
    {
        "job_code": "JD-FRONT-001",
        "name": "高级前端工程师（数据产品）",
        "version": 1,
        "content": (
            "岗位职责：负责人才数据看板、评估工作台和数据填报产品的前端架构与体验；建设组件库、"
            "请求层、错误处理和响应式布局；推动性能与可访问性治理。\n"
            "任职要求：5 年以上 React/TypeScript 经验；熟悉 Vite、数据可视化和复杂表格；"
            "有 B 端数据产品经验优先。"
        ),
    },
]


def upsert_master_data() -> tuple[int, int, str]:
    """幂等写入员工、岗位和演示知识库，返回 (员工数, 岗位数, 知识库 id)。"""
    created_employees = 0
    created_jobs = 0

    with SessionLocal() as db:
        for item in EMPLOYEES:
            payload = {**item, "birth_date": date.fromisoformat(item["birth_date"])}
            row = db.scalar(
                select(EmployeeProfile).where(EmployeeProfile.employee_no == item["employee_no"])
            )
            if row is None:
                db.add(EmployeeProfile(tenant_id=TENANT_ID, **payload))
                created_employees += 1
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
                row.tenant_id = TENANT_ID

        for item in JOBS:
            row = db.scalar(
                select(JobDescription).where(JobDescription.job_code == item["job_code"])
            )
            if row is None:
                db.add(
                    JobDescription(
                        tenant_id=TENANT_ID,
                        status="active",
                        **item,
                    )
                )
                created_jobs += 1
            else:
                row.tenant_id = TENANT_ID
                row.name = item["name"]
                row.content = item["content"]
                row.version = item["version"]
                row.status = "active"

        kb = db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == TENANT_ID,
                KnowledgeBase.name == KB_NAME,
            )
        )
        if kb is None:
            kb = KnowledgeBase(
                tenant_id=TENANT_ID,
                name=KB_NAME,
                description=KB_DESCRIPTION,
                permission_scope="hr_private",
                status="active",
            )
            db.add(kb)
            db.flush()
        kb_id = str(kb.id)
        db.commit()

    return created_employees, created_jobs, kb_id


def wait_for(check, *, timeout: float = 180.0, interval: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check()
        if result is not None:
            return result
        time.sleep(interval)
    return None


def upload_documents(api: str, kb_id: str, *, reindex: bool = False) -> None:
    """上传 Markdown 项目复盘，并等待解析、切片和索引完成。"""
    headers = {"X-Tenant-Id": TENANT_ID, "X-Permission-Scopes": "hr_private,department,internal"}
    client = httpx.Client(timeout=300.0, headers=headers)

    employees = {
        item["employee_no"]: item["id"]
        for item in client.get(f"{api}/employees").raise_for_status().json()
    }
    existing = client.get(
        f"{api}/documents", params={"knowledge_base_id": kb_id}
    ).raise_for_status().json()
    existing_by_key = {
        (item["candidate_id"], item["title"]): item["id"] for item in existing
    }

    for path in sorted(DEMO_DIR.glob("*.md")):
        candidate_id = re.match(r"(C\d{3})_", path.name).group(1)
        title = path.stem
        existing_id = existing_by_key.get((candidate_id, title))
        if existing_id and not reindex:
            print(f"skip  {candidate_id}  {title}")
            continue
        if existing_id:
            client.post(f"{api}/documents/{existing_id}/evidence-index").raise_for_status()
            final = wait_for(
                lambda: (
                    item
                    if (item := client.get(f"{api}/documents/{existing_id}").json())["index_status"]
                    in {"succeeded", "failed"}
                    else None
                )
            )
            print(
                f"reindex {candidate_id}  {title}  index={final['index_status'] if final else 'timeout'}"
            )
            continue

        data = {
            "candidate_id": candidate_id,
            "title": title,
            "document_type": "project",
            "knowledge_base_id": kb_id,
            "permission_scope": "hr_private",
        }
        if employees.get(candidate_id):
            data["employee_id"] = employees[candidate_id]

        response = client.post(
            f"{api}/documents",
            files={"file": (path.name, path.read_bytes(), "text/markdown")},
            data=data,
        )
        response.raise_for_status()
        document_id = response.json()["id"]

        # 上传已自动排队；这里显式同步解析一次，避免 worker 未启动时一直 pending。
        client.post(f"{api}/documents/{document_id}/parse", params={"async_mode": "false"}).raise_for_status()

        detail = wait_for(
            lambda: (
                item
                if (item := client.get(f"{api}/documents/{document_id}").json())["parse_status"]
                in {"succeeded", "failed"}
                else None
            )
        )
        if not detail or detail["parse_status"] != "succeeded":
            print(f"parse failed: {candidate_id} {title}", file=sys.stderr)
            continue

        chunk = client.post(
            f"{api}/documents/{document_id}/chunks",
            json={"chunk_size": 800, "chunk_overlap": 100},
        )
        chunk.raise_for_status()

        client.post(f"{api}/documents/{document_id}/evidence-index").raise_for_status()
        final = wait_for(
            lambda: (
                item
                if (item := client.get(f"{api}/documents/{document_id}").json())["index_status"]
                in {"succeeded", "failed"}
                else None
            )
        )
        if not final or final["index_status"] != "succeeded":
            print(f"index pending/failed: {candidate_id} {title}", file=sys.stderr)
            continue

        print(f"ready {candidate_id}  {title}  chunk={chunk.json()['strategy']}  index=succeeded")

    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="写入评估与推荐演示数据")
    parser.add_argument("--with-documents", action="store_true", help="上传并索引 Markdown 项目复盘")
    parser.add_argument("--api", default="http://127.0.0.1:18080/api", help="后端 API 地址")
    parser.add_argument("--reindex", action="store_true", help="为已存在的材料重建 Milvus 索引")
    args = parser.parse_args()

    employees, jobs, kb_id = upsert_master_data()
    print(f"employees: +{employees}  jobs: +{jobs}  knowledge_base: {kb_id}")

    if args.with_documents:
        upload_documents(args.api.rstrip("/"), kb_id, reindex=args.reindex)


if __name__ == "__main__":
    main()
