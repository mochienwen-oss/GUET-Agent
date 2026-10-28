# backend.Dockerfile —— FastAPI 后端镜像（检索用 CPU，生成统一走 API）
FROM python:3.11-slim

WORKDIR /app

# 先装依赖（利用层缓存）。backend 源码通过 docker-compose 挂载，便于改代码后重启生效。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]