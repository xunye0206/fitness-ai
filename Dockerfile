# 健身AI Agent —— 生产部署镜像
# 用法：docker build -t fitness-agent . && docker run -p 8000:8000 --env-file .env fitness-agent
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存：只有 requirements.txt 变才重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码（.dockerignore 已排除 .venv/.git/.env 等敏感与冗余文件）
COPY . .

# 运行时不带 dev reload；起步单进程 uvicorn 即可，后续可按需加 gunicorn 多 worker
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
