#!/usr/bin/env bash
# 下载当前检索所需的 embedding 与 reranker；在激活项目虚拟环境后执行。
# 用法：bash scripts/download_bge.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="BAAI/bge-m3",
    local_dir="model/embedding_m3",
    ignore_patterns=["onnx/*", "imgs/*", "long.jpg"],
)
snapshot_download(
    repo_id="BAAI/bge-reranker-v2-m3",
    local_dir="model/rerank_m3",
    ignore_patterns=["assets/*"],
)
print("Models downloaded: model/embedding_m3, model/rerank_m3")
PY
