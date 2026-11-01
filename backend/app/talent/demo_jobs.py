from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import JobDescription


def seed_job_descriptions(
    records: Sequence[dict[str, Any]],
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """批量写入岗位 JD 演示数据（按 job_code 幂等：存在则更新，不存在则插入）。

    Args:
        records: 每条含 job_code/tenant_id/name/content/version/status 的 JD 记录。
        session_factory: 会话工厂，默认使用 SessionLocal。

    Returns:
        {"inserted": 新插入数, "updated": 更新数}。
    """
    inserted = 0
    updated = 0
    with session_factory() as db:
        for record in records:
            # 按 job_code 查找是否已存在
            job = db.scalar(
                select(JobDescription).where(JobDescription.job_code == record["job_code"])
            )
            if job is None:
                # 不存在：新增记录
                db.add(JobDescription(**record))
                inserted += 1
                continue
            # 已存在：仅更新可变字段
            for field in ("tenant_id", "name", "content", "version", "status"):
                setattr(job, field, record[field])
            updated += 1
        db.commit()
    return {"inserted": inserted, "updated": updated}