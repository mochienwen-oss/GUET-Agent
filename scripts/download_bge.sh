#!/usr/bin/env bash
# download_bge.sh —— 本地下载 RAG 用的两个小模型（embedding + reranker），走 hf-mirror
#
# 用法（本地 Windows Git Bash）：
#   bash scripts/download_bge.sh
#
# 产出：
#   model/embedding/  BAAI/bge-large-zh-v1.5   （约 1.3GB，粗排：查询/文档向量化）
#   model/rerank/     BAAI/bge-reranker-base   （约 1.1GB，精排：cross-encoder）
#
# 上传到服务器后，后端用 EMBED_MODEL / RERANK_MODEL 指向对应目录即可。
set -u

export HF_ENDPOINT=https://hf-mirror.com
# ⚠️ 必须关掉 Xet：HF 大文件走 cas-server.xethub.hf.co，hf-mirror 不代理 → 401 Unauthorized
export HF_HUB_DISABLE_XET=1

HF=/c/Users/Lenovo/AppData/Roaming/Python/Python313/Scripts/hf.exe
ROOT="D:/guet-agent-微调"
RETRIES=20

dl() {
    local repo="$1" dir="$2"
    shift 2
    mkdir -p "$dir"
    for i in $(seq 1 "$RETRIES"); do
        echo ">>> [$repo] 第 $i 次尝试"
        if "$HF" download "$repo" --local-dir "$dir" "$@"; then
            echo ">>> [$repo] 完成"
            return 0
        fi
        echo ">>> [$repo] 中断，10 秒后续传重试..."
        sleep 10
    done
    echo ">>> [$repo] 重试次数用尽，失败"
    return 1
}

# 注意：bge-large-zh-v1.5 仓库里【只有 pytorch_model.bin，没有 model.safetensors】，
#       所以这里不能排除 *.bin，否则拉下来是个空壳。
dl BAAI/bge-large-zh-v1.5 "$ROOT/model/embedding" \
    --exclude "README.md" \
    --exclude ".gitattributes"

# bge-reranker-base 两种权重都有，safetensors 更优；onnx/ 是给非 PyTorch 推理用的，没用。
dl BAAI/bge-reranker-base "$ROOT/model/rerank" \
    --exclude "onnx/*" \
    --exclude "pytorch_model.bin" \
    --exclude "README.md" \
    --exclude ".gitattributes"

echo ""
echo "===== 完成 ====="
du -sh "$ROOT/model/embedding" "$ROOT/model/rerank" 2>/dev/null