# 飞书 AI 新闻推送 Bot
# Python 3.10+

FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（gcc 用于 trafilatura C 扩展编译，安装后清理）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 清理 gcc（不再需要）
RUN apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

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
