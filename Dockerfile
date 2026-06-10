# ============================================================
# Anything — RAG & Agent — Dockerfile
#
# 构建:
#     docker build -t anything:latest .
#
# 单容器运行 (不需要 ollama 时):
#     docker run --rm -p 8000:8000 -e DASHSCOPE_API_KEY=sk-xxx \
#         -v $(pwd)/run/documents:/app/run/documents \
#         -v $(pwd)/run/vector_store:/app/run/vector_store \
#         anything:latest
#
# 推荐: docker-compose up (自动挂卷 + 环境变量) — 见 docker-compose.yml
# ============================================================

FROM python:3.12-slim AS base

# 系统依赖: 编译 numpy/faiss 时需要的 build tools 加上,
# 还有运行时网络相关基础包
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/basic_support:/app/data_layer:/app/business:/app/interface:/app/application:/app/run:/app

WORKDIR /app

# Python 依赖先装 (单独一层, 利用缓存)
COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# 项目代码 (放后面, 改代码不会重装依赖)
COPY . /app

# 预创建运行期目录 (一会儿挂卷会覆盖, 但保证 PathExists 防 mkdir 边缘 bug)
RUN mkdir -p /app/run/documents /app/run/vector_store /app/run/state_store /app/run/uploads

# 健康检查 — 后端 /healthz 端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# 默认监听 0.0.0.0:8000 (compose 端口映射 8000:8000), DEV_MODE 默认 1 让 secrets 缺失不挂
ENV ANYTHING_DEV_MODE=1
# kb/feedback sqlite 数据根: 默认相对路径 "run" 只在 CWD=项目根时正确,
# WORKDIR=/app/run 下不设会写出 /app/run/run/ 分裂库
ENV ANYTHING_DATA_ROOT=/app/run

EXPOSE 8000

WORKDIR /app/run
CMD ["python", "-m", "uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8000"]
