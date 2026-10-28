# merge_lora.py —— 将 LoRA 适配器合并回基座模型，导出完整权重（一次性预处理）
# 用法：python scripts/merge_lora.py --base <基座路径> --lora <lora路径> --out <输出路径>
# 说明：合并需约 28G 显存（fp16）。若显存不足，改 --device cpu 用内存合并（约 28G RAM，更慢）。
#
# ⚠️ 精度选择 --dtype：
#   V100 / P100 等 Volta、Pascal 卡 **不支持 bf16**，必须用 float16（默认值）；
#   A100 / A10 / 4090 等 Ampere 及以后可用 bfloat16。
import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

_DTYPES = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}


def _ensure_chat_template(out_dir: str, tokenizer) -> None:
    """把 chat template 显式写回 tokenizer_config.json。

    新版 transformers 的 save_pretrained 会把模板单独存成 chat_template.jinja，
    并在 tokenizer_config.json 里省略该字段；而 vLLM 0.6.x 用的 transformers 4.46
    只认 config 里的 chat_template，否则 /v1/chat/completions 直接报
    "you must provide a chat template if the tokenizer does not define one"。
    """
    import json
    import os

    cfg_path = os.path.join(out_dir, "tokenizer_config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("chat_template"):
        return

    tpl = getattr(tokenizer, "chat_template", None)
    if not tpl:
        jinja = os.path.join(out_dir, "chat_template.jinja")
        if os.path.exists(jinja):
            with open(jinja, encoding="utf-8") as f:
                tpl = f.read()
    if not tpl:
        print("⚠️  未找到 chat template：vLLM 需用 --chat-template 手动指定，否则无法用 /chat/completions")
        return

    cfg["chat_template"] = tpl
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"✅ 已把 chat template 写回 tokenizer_config.json（{len(tpl)} 字符）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="基座模型路径，如 Qwen2.5-14B-Instruct")
    parser.add_argument("--lora", required=True, help="LoRA 适配器路径（含 adapter_config.json）")
    parser.add_argument("--out", required=True, help="合并后权重输出目录")
    parser.add_argument("--device", default="auto", help="auto 用 GPU，cpu 用内存合并")
    parser.add_argument("--dtype", default="float16", choices=sorted(_DTYPES),
                        help="导出精度；V100 等老卡必须用 float16（默认）")
    args = parser.parse_args()

    dtype = _DTYPES[args.dtype]
    print(f"加载基座模型: {args.base}（dtype={args.dtype}）")
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=dtype,
        device_map=args.device,
        trust_remote_code=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)

    print(f"加载 LoRA: {args.lora}")
    model = PeftModel.from_pretrained(model, args.lora)

    print("合并 LoRA 权重...")
    model = model.merge_and_unload()

    # 显式写死 config.torch_dtype，避免下游（vLLM / transformers）沿用基座 config 里的 bfloat16
    model.config.torch_dtype = dtype

    print(f"保存到: {args.out}（dtype={args.dtype}）")
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    _ensure_chat_template(args.out, tokenizer)
    print("✅ 合并完成，可交给 vLLM 加载")


if __name__ == "__main__":
    main()