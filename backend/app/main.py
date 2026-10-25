"""main.py —— 服务入口。

负责组装 FastAPI 应用：加载配置、初始化日志、注册跨域中间件、加入请求日志
中间件与健康检查端点，并挂载业务路由。模块导入时即创建 app 实例，便于
uvicorn 等 ASGI 服务器直接引用。
"""
import logging  # 日志
from time import perf_counter  # 高精度计时，用于统计请求耗时

from fastapi import FastAPI  # Web 框架
from fastapi.middleware.cors import CORSMiddleware  # 跨域（CORS）中间件
from starlette.requests import Request  # 请求对象类型

from app.api.routes import router  # 业务路由（所有 /api 接口）
from app.api.talent_portal import router as talent_portal_router  # 评估/推荐页接口
from app.core.config import get_settings  # 应用配置
from app.core.logging_config import configure_logging  # 日志初始化

# 进程启动时加载配置并初始化日志
settings = get_settings()
configure_logging(settings.log_dir, settings.service_name)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用实例。"""
    app = FastAPI(title="人才文档解析 API", version="0.1.0")

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
        """健康检查接口，用于探活。"""
        logger.info("health_check status=ok")
        return {"service": "talent-document-api", "status": "ok"}

    # 挂载业务路由
    app.include_router(router)
    app.include_router(talent_portal_router)
    return app


# 模块导入时即创建应用实例（供 uvicorn 等 ASGI 服务器直接引用）
app = create_app()