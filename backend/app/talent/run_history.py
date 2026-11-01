"""运行历史与会话读模型的持久化。

Run、Run 步骤与会话列表都写在这里；读模型供前端历史列表与详情使用，
不参与 LangGraph 状态恢复（恢复仍走 Postgres checkpointer）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import TalentConversationRecord, TalentRunRecord, TalentRunStepRecord


def _timestamp(value: str) -> datetime:
    """把 ISO 字符串解析为带时区的 datetime。"""
    return datetime.fromisoformat(value)


def _duration_ms(started_at: datetime | None, completed_at: datetime) -> int | None:
    """计算从开始到结束的毫秒耗时。"""
    if started_at is None:
        return None
    if started_at.tzinfo is None and completed_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=None)
    return max(0, round((completed_at - started_at).total_seconds() * 1000))


def _iso(value: datetime | None) -> str | None:
    """把 datetime 序列化为 ISO 字符串，空值原样返回 None。"""
    return value.isoformat() if value is not None else None


class SqlTalentRunHistoryStore:
    """运行历史与会话读模型的 SQL 实现。"""

    def __init__(self, session_factory: sessionmaker) -> None:
        """保存会话工厂。"""
        self._session_factory = session_factory

    def record(self, event: Any) -> None:
        """落库一次运行及其步骤。"""
        occurred_at = _timestamp(event.timestamp)
        with self._session_factory() as session:
            run = session.get(TalentRunRecord, event.run_id)
            if event.type == "run.started":
                conversation_id = str(event.data.get("conversation_id") or event.run_id)
                if run is None:
                    run = TalentRunRecord(
                        run_id=event.run_id,
                        conversation_id=conversation_id,
                        turn_no=int(event.data.get("turn_no") or 1),
                        message=str(event.data.get("message", "")),
                        status="running",
                        citations=[],
                        started_at=occurred_at,
                        updated_at=occurred_at,
                    )
                    session.add(run)
                else:
                    run.status = "running"
                    run.updated_at = occurred_at
                conversation = session.get(TalentConversationRecord, conversation_id)
                if conversation is None:
                    conversation = TalentConversationRecord(
                        conversation_id=conversation_id,
                        title="新会话",
                        status="running",
                        latest_run_id=event.run_id,
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                    session.add(conversation)
                else:
                    conversation.status = "running"
                    conversation.latest_run_id = event.run_id
                    conversation.updated_at = occurred_at
                session.commit()
                return
            if run is None:
                return

            if event.type.startswith("step."):
                self._record_step(session, run, event, occurred_at)
            elif event.type == "conversation.updated":
                title = str(event.data.get("title", "")).strip()
                conversation = session.get(
                    TalentConversationRecord,
                    run.conversation_id,
                )
                if conversation is not None and title:
                    conversation.title = title
            elif event.type == "hitl.required":
                run.status = "waiting_input"
                self._set_active_step_status(session, run.run_id, "paused", occurred_at)
            elif event.type == "hitl.resolved":
                run.status = "running"
                self._set_active_step_status(session, run.run_id, "running", occurred_at)
            elif event.type == "report.completed":
                run.report = str(event.data.get("markdown", ""))
                run.citations = list(event.data.get("citations", []))
                run.trace_id = str(event.data.get("trace_id", "")) or None
            elif event.type in {"run.completed", "run.cancelled", "run.failed"}:
                run.status = event.type.removeprefix("run.")
                run.completed_at = occurred_at
                if event.type == "run.failed":
                    run.error_message = str(event.data.get("message", "")) or None
                if event.type != "run.completed":
                    self._set_active_step_status(
                        session,
                        run.run_id,
                        run.status,
                        occurred_at,
                    )
            run.updated_at = occurred_at
            conversation = session.get(TalentConversationRecord, run.conversation_id)
            if conversation is not None:
                conversation.status = run.status
                conversation.updated_at = occurred_at
            session.commit()

    def _record_step(
        self,
        session: Any,
        run: TalentRunRecord,
        event: Any,
        occurred_at: datetime,
    ) -> None:
        """写入或更新单个步骤。"""
        step_id = str(event.data.get("step_id", ""))
        if not step_id:
            return
        step = session.scalar(
            select(TalentRunStepRecord).where(
                TalentRunStepRecord.run_id == run.run_id,
                TalentRunStepRecord.step_id == step_id,
            )
        )
        if step is None:
            position = session.scalar(
                select(func.count()).select_from(TalentRunStepRecord).where(
                    TalentRunStepRecord.run_id == run.run_id
                )
            ) or 0
            step = TalentRunStepRecord(
                run_id=run.run_id,
                step_id=step_id,
                position=position + 1,
                title=str(event.data.get("title", step_id)),
                kind=str(event.data.get("kind", "")) or None,
                status="running",
                output={},
                started_at=occurred_at,
                active_started_at=occurred_at,
            )
            session.add(step)

        step.title = str(event.data.get("title", step.title))
        step.kind = str(event.data.get("kind", step.kind or "")) or step.kind
        if isinstance(event.data.get("completed"), int):
            step.completed = event.data["completed"]
        if isinstance(event.data.get("total"), int):
            step.total = event.data["total"]
        if event.data.get("summary"):
            step.summary = str(event.data["summary"])
        if isinstance(event.data.get("output"), dict):
            step.output = {**(step.output or {}), **event.data["output"]}
        if event.type == "step.completed":
            step.status = "completed"
            step.completed_at = occurred_at
            step.duration_ms = (step.duration_ms or 0) + (
                _duration_ms(step.active_started_at, occurred_at) or 0
            )
            step.active_started_at = None
        else:
            step.status = "running"

    @staticmethod
    def _set_active_step_status(
        session: Any,
        run_id: str,
        status: str,
        occurred_at: datetime,
    ) -> None:
        """更新处于活动状态的步骤。"""
        active_steps = session.scalars(
            select(TalentRunStepRecord).where(
                TalentRunStepRecord.run_id == run_id,
                TalentRunStepRecord.status.in_(("running", "paused")),
            )
        ).all()
        for step in active_steps:
            if status == "paused":
                step.duration_ms = (step.duration_ms or 0) + (
                    _duration_ms(step.active_started_at, occurred_at) or 0
                )
                step.active_started_at = None
            elif status == "running":
                step.active_started_at = occurred_at
            if status in {"cancelled", "failed"}:
                step.completed_at = occurred_at
                step.duration_ms = (step.duration_ms or 0) + (
                    _duration_ms(step.active_started_at, occurred_at) or 0
                )
                step.active_started_at = None
            step.status = status

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """按开始时间倒序列出运行摘要。"""
        with self._session_factory() as session:
            runs = session.scalars(
                select(TalentRunRecord)
                .order_by(TalentRunRecord.started_at.desc())
                .limit(limit)
            ).all()
            return [self._run_summary(item) for item in runs]

    def list_conversations(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列出会话及其最新运行。"""
        with self._session_factory() as session:
            statement = select(TalentConversationRecord)
            if status:
                statement = statement.where(TalentConversationRecord.status == status)
            conversations = session.scalars(
                statement.order_by(TalentConversationRecord.updated_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "conversation_id": item.conversation_id,
                    "title": item.title,
                    "status": item.status,
                    "updated_at": _iso(item.updated_at),
                    "latest_run_id": item.latest_run_id or "",
                }
                for item in conversations
            ]

    def next_turn_no(self, conversation_id: str) -> int:
        """计算会话下一轮的轮次号。"""
        with self._session_factory() as session:
            latest = session.scalar(
                select(func.max(TalentRunRecord.turn_no)).where(
                    TalentRunRecord.conversation_id == conversation_id
                )
            )
            return int(latest or 0) + 1

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """读取运行详情。"""
        with self._session_factory() as session:
            run = session.get(TalentRunRecord, run_id)
            if run is None:
                return None
            conversation_runs = session.scalars(
                select(TalentRunRecord)
                .where(
                    TalentRunRecord.conversation_id == run.conversation_id,
                    TalentRunRecord.turn_no <= run.turn_no,
                )
                .order_by(TalentRunRecord.turn_no)
            ).all()
            run_ids = [item.run_id for item in conversation_runs]
            conversation_steps = session.scalars(
                select(TalentRunStepRecord)
                .where(TalentRunStepRecord.run_id.in_(run_ids))
                .order_by(TalentRunStepRecord.run_id, TalentRunStepRecord.position)
            ).all()
            steps_by_run: dict[str, list[TalentRunStepRecord]] = {
                item.run_id: [] for item in conversation_runs
            }
            for step in conversation_steps:
                steps_by_run[step.run_id].append(step)
            return {
                **self._run_summary(run),
                "report": run.report or "",
                "citations": list(run.citations or []),
                "trace_id": run.trace_id or "",
                "error": run.error_message or "",
                "steps": [
                    self._step_detail(step) for step in steps_by_run[run.run_id]
                ],
                "conversation_turns": [
                    self._conversation_turn(item, steps_by_run[item.run_id])
                    for item in conversation_runs
                ],
            }

    @staticmethod
    def _run_summary(run: TalentRunRecord) -> dict[str, Any]:
        """把运行行转换为摘要结构。"""
        duration_ms = _duration_ms(run.started_at, run.completed_at) if run.completed_at else None
        return {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "turn_no": run.turn_no,
            "message": run.message,
            "status": run.status,
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
            "duration_ms": duration_ms,
        }

    @staticmethod
    def _conversation_turn(
        run: TalentRunRecord,
        steps: list[TalentRunStepRecord],
    ) -> dict[str, Any]:
        """取会话当前轮次。"""
        return {
            "run_id": run.run_id,
            "turn_no": run.turn_no,
            "message": run.message,
            "status": run.status,
            "report": run.report or "",
            "citations": list(run.citations or []),
            "trace_id": run.trace_id or "",
            "error": run.error_message or "",
            "steps": [SqlTalentRunHistoryStore._step_detail(step) for step in steps],
        }

    @staticmethod
    def _step_detail(step: TalentRunStepRecord) -> dict[str, Any]:
        """把步骤行转换为详情结构。"""
        return {
            "step_id": step.step_id,
            "position": step.position,
            "title": step.title,
            "kind": step.kind,
            "status": step.status,
            "completed": step.completed,
            "total": step.total,
            "summary": step.summary,
            "output": dict(step.output or {}),
            "started_at": _iso(step.started_at),
            "completed_at": _iso(step.completed_at),
            "duration_ms": step.duration_ms,
        }
