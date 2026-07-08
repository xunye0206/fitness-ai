# 健身AI Agent —— 生产部署镜像
# 用法：docker build -t fitness-agent . && docker run -p 8000:8000 --env-file .env fitness-agent
# 注意：pip 源使用腾讯云镜像（mirrors.cloud.tencent.com）加速依赖安装（云端构建已验证可用）。
# 但【基础镜像必须用 Docker Hub 官方 python:3.13-slim】——云端构建机可正常拉取官方镜像，
# 而腾讯云 CCR 的 library/python 仓库里并不存在 3.13-slim，用 CCR 地址会导致 build_failed。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存：只有 requirements.txt 变才重装）
# 使用腾讯云 PyPI 镜像加速安装，避免云端构建时 PyPI 超时
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple/ -r requirements.txt

# 再拷源码（.dockerignore 已排除 .venv/.git/.env 等敏感与冗余文件）
COPY . .

# 运行时不带 dev reload；起步单进程 uvicorn 即可，后续可按需加 gunicorn 多 worker
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
