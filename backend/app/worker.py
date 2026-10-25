import logging
from uuid import UUID

from redis import Redis
from redis.exceptions import TimeoutError

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.documents.document_pipeline import continue_document_pipeline
from app.retrieval.evidence_index_service import run_index_job
from app.core.logging_config import configure_logging
from app.integrations.object_store import ObjectStore
from app.documents.services import parse_version


logger = logging.getLogger(__name__)


def run() -> None:
    """后台任务消费者主循环。

    从 Redis 阻塞队列（talent:parse:queue / talent:index:queue）取任务并执行：
    - 索引任务（talent:index:queue）→ 调用 run_index_job 写向量库
    - 解析任务（talent:parse:queue）→ 调用 parse_version 解析文档；
      解析成功后继续推进流水线（分块 + 构建索引）。
    """
    settings = get_settings()
    configure_logging(settings.log_dir, "worker")
    # 建立 Redis 连接，decode_responses=True 让返回值为字符串；socket_timeout 防止长时间阻塞挂起
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=15)
    queues = ["talent:parse:queue", "talent:index:queue"]

    logger.info("worker_started queues=%s", ",".join(queues))
    while True:
        try:
            # blpop 阻塞地从任一队列取一个元素，最多等待 5 秒
            item = redis.blpop(queues, timeout=5)
        except TimeoutError:
            # 等待超时是正常现象，继续下一轮
            continue
        except Exception:
            logger.exception("worker_queue_read_failed queue=talent:parse:queue")
            raise
        if not item:
            # 超时无数据，循环继续
            continue

        # item 形如 (queue_name, "job_id:version_id")
        queue_name, payload = item
        job_id, version_id = payload.split(":", 1)
        logger.info("worker_job_received queue=%s job_id=%s version_id=%s", queue_name, job_id, version_id)

        # 每个任务用独立的数据库会话处理，避免长连接与并发污染
        with SessionLocal() as db:
            if queue_name == "talent:index:queue":
                # 索引任务：将已分块的证据写入向量库
                job = run_index_job(db, UUID(job_id), UUID(version_id))
                logger.info("worker_index_job_completed job_id=%s status=%s indexed_count=%s", job.id, job.status, job.indexed_count)
            else:
                # 解析任务：解析文档内容为 Markdown/HTML 等产物
                job = parse_version(db, ObjectStore(), UUID(version_id), UUID(job_id))
                logger.info("worker_parse_job_completed job_id=%s status=%s parser=%s", job.id, job.status, job.parser_name)
                # 解析成功则继续文档流水线：触发分块 + 索引任务入队
                if job.status.value == "succeeded":
                    index_job = continue_document_pipeline(
                        db,
                        store=ObjectStore(),
                        redis_client=redis,
                        version_id=UUID(version_id),
                        settings=settings,
                    )
                    logger.info(
                        "worker_pipeline_continued version_id=%s index_job_id=%s status=%s",
                        version_id,
                        index_job.id,
                        index_job.status,
                    )


if __name__ == "__main__":
    run()