from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 位置按文件路径锚定，不依赖进程 cwd：
# 开发态解析为仓库根目录下的 .env（backend 的上一级）；容器内该文件不存在，
# pydantic-settings 会静默跳过，改用 compose 注入的环境变量。
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """应用运行所需的全部配置项（自动从环境变量 / 配置文件读取）。

    继承 pydantic BaseSettings，字段名与环境变量名一一对应，
    例如 database_url 对应环境变量 DATABASE_URL。
    未显式设置时使用下方给出的默认值。
    """
    # 配置来源：读取项目根目录下的 .env 文件；extra="ignore" 表示忽略多余键，不影响启动。
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # ---- 基础依赖连接配置 ----
    database_url: str = "postgresql+psycopg://talent:talent_dev_password@127.0.0.1:15432/talent_docs"  # PostgreSQL 连接串（psycopg 驱动）
    redis_url: str = "redis://127.0.0.1:16379/0"  # Redis 连接串（用于任务队列等）

    # ---- 对象存储（MinIO / S3）配置 ----
    s3_endpoint_url: str = "http://127.0.0.1:19000"  # 对象存储服务端点
    s3_public_endpoint_url: str = "http://127.0.0.1:19000"  # 对外可访问的对象存储端点（用于生成预签名 URL）
    s3_access_key: str = "talent_minio"  # 访问密钥
    s3_secret_key: str = "talent_minio_password"  # 访问密钥密码
    s3_bucket: str = "talent-documents"  # 默认桶名

    # ---- 跨域与外部解析服务 ----
    cors_origins: str = "http://localhost:15173,http://127.0.0.1:15173"  # 允许的跨域来源（逗号分隔）
    mineru_url: str = "http://127.0.0.1:18001"  # MinerU 文档解析服务地址

    # ---- 模型服务（DashScope 通义）配置 ----
    dashscope_api_key: str = ""  # DashScope API Key
    chat_model: str = "qwen-plus"  # 对话/生成模型名
    # ---- 评测 Judge 模型（可与对话模型分开配置）----
    judge_model: str = ""  # Judge 模型名（留空表示未配置）
    judge_api_key: str = ""  # Judge 服务的 API Key
    judge_base_url: str = ""  # Judge 服务的 OpenAI 兼容端点
    embedding_model: str = "text-embedding-v3"  # 向量化（Embedding）模型名
    embedding_dimension: int = 1024  # 向量维度
    rerank_model: str = "gte-rerank-v2"  # Rerank 排序模型名
    rerank_top_n: int = 20  # Rerank 后返回的最多结果数

    # ---- 向量数据库（Milvus）配置 ----
    milvus_uri: str = "http://127.0.0.1:19531"  # Milvus 服务地址
    milvus_token: str = "root:Milvus"  # Milvus 认证凭据
    milvus_collection: str = "talent_evidence_v2"  # 默认集合名

    # ---- 日志配置 ----
    log_dir: str = "logs"  # 日志输出目录
    service_name: str = "backend"  # 服务名（用于日志文件命名）


@lru_cache
def get_settings() -> Settings:
    """返回进程级缓存的 Settings 实例。

    使用 lru_cache 保证全局只解析一次配置，避免每次调用都重复读取环境变量。
    """
    return Settings()