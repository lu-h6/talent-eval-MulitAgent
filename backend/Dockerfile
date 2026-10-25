FROM docker.1ms.run/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md .python-version ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY scripts ./scripts
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18080"]

