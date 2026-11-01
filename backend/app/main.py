"""main.py —— 服务入口。

负责组装 FastAPI 应用：加载配置、初始化日志、注册跨域中间件、加入请求日志
中间件与健康检查端点，并挂载业务路由。模块导入时即创建 app 实例，便于
uvicorn 等 ASGI 服务器直接引用。
"""
import logging  # 日志
from contextlib import asynccontextmanager  # 应用生命周期管理
from time import perf_counter  # 高精度计时，用于统计请求耗时

from fastapi import FastAPI  # Web 框架
from fastapi.middleware.cors import CORSMiddleware  # 跨域（CORS）中间件
from starlette.requests import Request  # 请求对象类型

from app.api.routes import router  # 业务路由（所有 /api 接口）
from app.core.config import get_settings  # 应用配置
from app.core.logging_config import configure_logging  # 日志初始化
from app.db.database import SessionLocal  # 数据库会话工厂
from app.evaluation.api import router as evaluation_router  # 评测（human-eval）接口
from app.talent.run_api import conversation_router as conversation_router  # 会话接口
from app.talent.run_api import router as talent_run_router  # 运行（流式）接口
from app.talent.run_history import SqlTalentRunHistoryStore  # 运行历史落库
from app.talent.run_stream import TalentRunManager  # 运行（流式）管理器

# 进程启动时加载配置并初始化日志
settings = get_settings()
configure_logging(settings.log_dir, settings.service_name)
logger = logging.getLogger(__name__)


def create_app(*, talent_run_manager: TalentRunManager | None = None) -> FastAPI:
    """构建并返回 FastAPI 应用实例。

    Args:
        talent_run_manager: 可选，外部注入的运行（流式）管理器；缺省时创建默认
            实例（带 Postgres 检查点与运行历史落库）。测试可注入内存实现。
    """
    manager = talent_run_manager or TalentRunManager(
        record_store=SqlTalentRunHistoryStore(SessionLocal)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """应用生命周期：启动时拉起 LangGraph 运行时，关闭时释放检查点连接。

        运行图依赖 Milvus 与 Postgres：依赖未就绪时只记录明确的中文错误，
        不再让整个进程启动失败，其余接口仍可对外服务（/api/runs 会返回错误）。
        """
        ready = False
        try:
            await manager.startup()
            ready = True
        except Exception as exc:  # noqa: BLE001 - 启动期依赖探测，失败要可读
            logger.error(
                "运行图启动失败，/api/runs 与 /api/conversations 不可用；"
                "请确认 Milvus(19531)、Postgres(15432) 与模型配置：%s",
                exc,
            )
        app.state.talent_runtime_ready = ready
        try:
            yield
        finally:
            if ready:
                await manager.shutdown()

    app = FastAPI(title="人才文档解析 API", version="0.1.0", lifespan=lifespan)
    # 运行管理器挂到应用状态，供 /api/runs 等路由取用
    app.state.talent_run_manager = manager

    # 配置跨域中间件：允许前端地址跨域访问，允许携带凭据并开放全部方法与请求头
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        """HTTP 请求日志中间件：记录每个请求的方法、路径、耗时，失败时记错误日志。"""
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 请求处理抛异常：记录耗时作为错误日志后原样上抛
            duration_ms = (perf_counter() - started_at) * 1000
            logger.exception(
                "http_request_failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        # 正常返回：记录状态码与耗时
        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "http_request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """健康检查接口：同时报告运行图（Milvus/Postgres）是否已就绪。"""
        runtime_ready = bool(getattr(app.state, "talent_runtime_ready", False))
        logger.info("health_check status=ok talent_runtime=%s", runtime_ready)
        return {
            "service": "talent-document-api",
            "status": "ok",
            "talent_runtime": "ready" if runtime_ready else "unavailable",
        }

    # 挂载业务路由
    app.include_router(router)
    # 多轮会话运行（流式）与评测接口
    app.include_router(talent_run_router)
    app.include_router(conversation_router)
    app.include_router(evaluation_router)
    return app


# 模块导入时即创建应用实例（供 uvicorn 等 ASGI 服务器直接引用）
app = create_app()