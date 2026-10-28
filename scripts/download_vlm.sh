#!/usr/bin/env bash
# download_vlm.sh —— 本地下载 Qwen2.5-VL-7B-Instruct 视觉模型（入库解析用），走 hf-mirror
#
# 用法（本地 Windows Git Bash）：
#   bash scripts/download_vlm.sh          # 默认 AWQ 量化版（6.45GB，vLLM 部署推荐）
#   bash scripts/download_vlm.sh bf16     # bf16 原版（15.45GB，AWQ 在新卡上跑不动时的兜底）
#
# 产出：
#   model/VLM/       Qwen/Qwen2.5-VL-7B-Instruct-AWQ
#   model/VLM_bf16/  Qwen/Qwen2.5-VL-7B-Instruct
#
# 上传到服务器后，compose 里 vlm 服务的 --model 指向对应目录即可。
set -u

export HF_ENDPOINT=https://hf-mirror.com
# ⚠️ 必须关掉 Xet：HF 大文件走 cas-server.xethub.hf.co，hf-mirror 不代理 → 401 Unauthorized
export HF_HUB_DISABLE_XET=1

HF=/c/Users/Lenovo/AppData/Roaming/Python/Python313/Scripts/hf.exe
ROOT="D:/guet-agent-微调/model"
RETRIES=20

VARIANT="${1:-awq}"
case "$VARIANT" in
    awq)  REPO="Qwen/Qwen2.5-VL-7B-Instruct-AWQ"; DIR="$ROOT/VLM" ;;
    bf16) REPO="Qwen/Qwen2.5-VL-7B-Instruct";     DIR="$ROOT/VLM_bf16" ;;
    *)    echo "用法: bash scripts/download_vlm.sh [awq|bf16]"; exit 1 ;;
esac

mkdir -p "$DIR"
echo "仓库: $REPO"
echo "目标: $DIR"
echo "源  : $HF_ENDPOINT"
echo "----------------------------------------"

for i in $(seq 1 "$RETRIES"); do
    echo ">>> 第 $i 次尝试"
    if "$HF" download "$REPO" --local-dir "$DIR"; then
        echo ">>> 完成"
        break
    fi
    echo ">>> 中断，10 秒后续传重试..."
    sleep 10
done

echo ""
echo "===== 结果 ====="
du -sh "$DIR" 2>/dev/null
ls -lh "$DIR"