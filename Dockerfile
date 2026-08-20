# 飞书 AI 新闻推送 Bot
# Python 3.10+
#
# 多阶段构建:
#   builder  — 用精确锁定的 requirements-lock.txt 安装依赖(可复现),gcc 仅构建阶段需要
#   runtime  — 只拷贝已装依赖 + 源码,不含编译工具链,镜像更小更安全

# ===== 构建阶段 =====
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖(gcc 用于无 wheel 的 sdist 编译,仅构建阶段需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖到独立目录(锁文件与生产环境一致)
COPY requirements-lock.txt .
RUN pip install --no-cache-dir --prefix=/opt/deps -r requirements-lock.txt

# ===== 运行时阶段 =====
FROM python:3.11-slim AS runtime

WORKDIR /app

# 依赖与可执行脚本(uvicorn 等)
ENV PYTHONPATH=/opt/deps/lib/python3.11/site-packages
ENV PATH=/opt/deps/bin:$PATH
COPY --from=builder /opt/deps /opt/deps

# 复制源码
COPY app/ ./app/

# 创建非 root 用户运行应用
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
