# MinerU 本地解析运行时

该目录是复杂 PDF 与扫描件的本地私有解析运行时，环境文件位于后端目录内部

## 方式一：本机直接运行（macOS / glibc 2.27+ 的 Linux）

```bash
uv sync --project backend/mineru-runtime
uv run --project backend/mineru-runtime mineru-api --host 127.0.0.1 --port 18001
```

首次解析会从配置的模型源下载模型，CPU 环境使用 pipeline 后端

## 方式二：Docker 容器运行（CentOS 7 等 glibc 过低的服务器）

onnxruntime 从 1.17.0 起加入 Python 3.12 支持的同时，Linux wheel 最低要求 glibc 2.27 (manylinux_2_27)。CentOS 7 的 glibc 是 2.17，没有兼容的 wheel，无法本机直接安装。用 Docker 容器绕过：

```bash
# 在项目根目录执行
docker compose -f docker-compose-mineru.yml up -d mineru    # 首次会构建镜像 + 下载模型，耗时较长
docker compose  -f docker-compose-mineru.yml logs -f mineru  # 观察模型下载进度
```

容器内用 `python:3.12-slim`（Debian Bookworm，glibc 2.36），`uv sync` 可正常安装所有依赖。模型缓存在 `mineru_models` 数据卷中，重启容器不会重新下载。

docker-compose-mineru.yml 中 backend / worker 的 `MINERU_URL` 已指向 `http://mineru:18001`，容器间直连，无需 `host.docker.internal`
