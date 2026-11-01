"""数据库引擎与会话工厂（SQLAlchemy）。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

# 创建全局数据库引擎：连接信息来自配置，pool_pre_ping 会在取连接前先做一次
# 轻量的探测（SELECT 1），避免复用已被数据库断开的失效连接而导致偶发报错。
engine = create_engine(get_settings().database_url, pool_pre_ping=True)

# 基于该引擎创建会话工厂；expire_on_commit=False 使提交后对象属性仍可访问，
# 避免默认的"提交后自动过期"导致在读属性时再次触发查询。
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    """FastAPI 依赖项：提供一个请求级的数据库会话，并在请求结束时自动关闭。

    以生成器方式向外逐出 session，配合 FastAPI 的 Depends 使用，
    在依赖释放时通过上下文管理器安全地关闭连接。
    """
    with SessionLocal() as session:
        yield session