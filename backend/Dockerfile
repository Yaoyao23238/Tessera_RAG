# syntax=docker/dockerfile:1
# Tessera 后端镜像（默认 CPU 档）
# GPU 部署：把下方 torch 安装行的索引换成 https://download.pytorch.org/whl/cu126，
# 并以 `docker run --gpus all` / compose deploy.resources 方式挂 GPU。
FROM python:3.13-slim

# opencv/docling 运行时系统库（libgl1、libglib2.0）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装 CPU 版 torch：PyPI 默认 torch 会拉 CUDA 全家桶（数 GB），CPU 档镜像不需要。
# 单独一层便于缓存；随后 pip install . 会识别 torch>=2.2 已满足。
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# pyproject.toml 的 packages.find 指向 app 包，两者需同时存在才能安装
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

EXPOSE 8766
ENV TESSERA_DATA_DIR=/data
# 模型/文档/索引全部落在 /data 卷；首次使用自动下载模型。
# 国内网络可附加：-e HF_ENDPOINT=https://hf-mirror.com -e HF_HUB_DISABLE_XET=1
VOLUME /data

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8766"]
