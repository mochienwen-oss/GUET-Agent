# -*- coding: utf-8 -*-
"""Task 8A：**唯一**允许真实调用 LLM 的候选生成入口。

```text
读参数 → 装载场景 → 真实重放工具 → draft_candidate（唯一一次 LLM 调用） → write_candidates
```

★ **本文件不重复实现任何既有逻辑。** 风险分类、证据目录、evidence binding、来源渲染、
  `batch_id` 正则、场景/轨迹校验、manifest 哈希算法**一律复用 `compiler/` 的现有实现**；
  这里只做「编排 + 参数校验 + 一次 HTTP 往返」。

★ **只产 `pending`。** 没有 `--approve`、没有 `promote`、没有 `compile`，
  也不提供任何改 `review_status` 的开关 —— 人的裁决只能由人改候选 JSONL 里的那一格。

★ **三份产物，manifest 是唯一的审计入口**：`<batch>.candidates.jsonl` /
  `<batch>.redteam.jsonl` / `<batch>.manifest.json`。模型、提示词版本、采样参数、
  输入文件哈希、场景选择统计**全部并进 manifest**（经 `provenance=` 传入），
  不再另开第二份可漂移的真相源。

★ **同一 `batch_id` 不得重跑** —— 三份产物里任一已存在就拒绝，**没有 `--force`**。
  真实 LLM 的输出不保证逐字一致，覆盖等于把上一批的审计入口抹掉。

★ **产物里不出现** token / 绝对路径 / 时间戳 / 主机名 / 完整提示词 / 系统环境。
  API token 只从**按 provider 对应**的环境变量读（`GUET_VLLM_API_KEY` /
  `DEEPSEEK_API_KEY`，可为空），**不进命令行、不进日志、不进产物**。

★ **采样参数**（`SAMPLING` / `DEEPSEEK_SAMPLING`）：候选生成固定 `temperature=0` ——
  训练答案要的是**稳定、便于审核**的监督标签，不是复现生产回答的随机性。
  vLLM 那一份其余四项与线上 `llm_client` 同值；DeepSeek 那一份**只发它认的**
  （`temperature` + `max_output_tokens`），`top_k` / `repetition_penalty` 一律不发。
  请求体与 manifest 都从**同一个** `sampling_for(provider)` 取，两处不可能漂移。

★ **调用失败一律整体失败**：非 200 / 超时 / 缺 `choices` / 空 `content` 都抛
  `LlmCallFailed`，**不**把失败伪装成 `no_evidence`，也**不**留下半成品
  （`write_candidates` 只在全部场景处理完之后才被调用）。

Run:
  python scripts/generate_answer_candidates.py \
      --scenarios 训练集/scenarios/single_tool.jsonl \
      --out-dir 训练集/_work/phase6/candidates --batch-id trial-a \
      --model guet-qwen --prompt-version answer-candidate/1 \
      --vllm-url http://127.0.0.1:8000/v1 --max-scenarios 5
"""
import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _extra in (REPO / "backend", REPO / "训练集"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from compiler.answer_candidates import (  # noqa: E402
    PROMPT_VERSION, check_batch_id, draft_candidate, prompt_contract,
    prompt_contract_sha256, write_candidates)
from compiler.compile import _reject_duplicate_input_names  # noqa: E402
from compiler.exclusion import (  # noqa: E402
    load_exclusion, partition_training_specs)
from compiler.io import load_scenarios  # noqa: E402
from compiler.manifest import sha256_file  # noqa: E402
from compiler.models import TOOL_TERMINAL_BUCKETS  # noqa: E402
from compiler.replay import replay_spec  # noqa: E402

# ── 两个 provider ────────────────────────────────────────────────────────
#: `vllm`：本地 OpenAI 兼容接口，端点 `/chat/completions`（**保持既有路径不变**）。
#: `deepseek`：官方 Responses API，端点 `/responses`。
PROVIDER_VLLM = "vllm"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDERS = (PROVIDER_VLLM, PROVIDER_DEEPSEEK)

#: 批次角色。`training`（默认）会启用**考试集排除门**；`qualification` 只用于重跑
#: 资格考试本身（那时题目**就是**考试题，排除门必须停用），且停用会写进 manifest。
BATCH_ROLE_TRAINING = "training"
BATCH_ROLE_QUALIFICATION = "qualification"
BATCH_ROLES = (BATCH_ROLE_TRAINING, BATCH_ROLE_QUALIFICATION)

#: ★ **官方固定 API base** —— 不给命令行开关。给了就能被指到任意主机上，
#: 而这一批的合规性正建立在「它确实发给官方」这一点上。
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

#: 端点**类型**（写进 manifest，好让读的人不必猜哪套协议）。
ENDPOINT_CHAT_COMPLETIONS = "chat_completions"
ENDPOINT_RESPONSES = "responses"

#: 候选项的**同一份** JSON Schema —— 两个 provider 的请求形状不同，但校验的形状
#: 必须一模一样。抄两份的话，两边迟早漂移，而漂移的那一侧是**静默**的。
_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "used_evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["content", "used_evidence_ids"],
    "additionalProperties": False,
}

#: vLLM 0.9.1 的 OpenAI 兼容接口原生支持 `response_format=json_schema`。
#: ★ 这里只约束传输形状；字段语义仍由 `DraftAnswer` 与候选准入门严格校验。
#: 请求体与 manifest 共用这一份定义，不允许一边启用、一边漏记。
DRAFT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer_candidate",
        "strict": True,
        "schema": _DRAFT_SCHEMA,
    },
}

#: DeepSeek Responses API 的结构化输出：挂在请求体的 `text.format` 上
#: （Responses API 的语法与 chat.completions 的 `response_format` 不同）。
DEEPSEEK_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "answer_candidate",
    "strict": True,
    "schema": _DRAFT_SCHEMA,
}

#: **候选生成**的采样参数 —— 与线上回答**故意不同**：
#: 训练答案要的是**稳定、便于人工审核**的监督标签，不是复现生产回答的随机性，
#: 所以温度固定 `0`（贪心）。其余四项与线上 `llm_client` 同值，免得连风格都换了尺子。
#: ★ 这是**唯一**一份采样定义：请求体与 manifest 都从它取，两处不可能漂移。
#: ★ Task 8B 的 `base-qwen` 与 `guet-qwen` 用**完全相同**的这一份。
SAMPLING = {
    "temperature": 0.0,
    "max_tokens": 1024,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.05,
}

#: DeepSeek 的采样：**只发它认的**。`top_k` / `repetition_penalty` 不在官方
#: Responses API 的参数表里 —— 发了要么报错、要么被静默忽略，两种都不该出现
#: （被忽略更坏：manifest 会记下一套**没生效**的参数）。
#: `max_output_tokens` 是 Responses API 的字段名（不是 `max_tokens`）。
#:
#: ★ 8192（2026-10-17 逐场景实测 + 用户裁决）：
#:   `deepseek-flash` 的**推理内容与答案正文共用**这一个输出预算，
#:   `usage.output_tokens == output_tokens_details.reasoning_tokens` 时正文就是 0 字。
#:   1024 → 长回答截断；4096 → 27/29 通过，tq-024 与 tq-alias-a 仍被推理吃满
#:   （3972 / 4095 reasoning）。8192 下两条都完成（4329 / 5060），其余最大 2544。
#:   实测推理量**不随上限膨胀**（16384 时反而更少），所以 8192 是最小够用值。
#:   只改**完成预算**，不改提示词、不改答案口径 —— 但它是考试条件的一部分，
#:   所以冻结包的 `providers.deepseek.sampling` 随之更新（**重冻结**过）。
DEEPSEEK_SAMPLING = {
    "temperature": 0.0,
    "max_output_tokens": 8192,
}

HTTP_TIMEOUT_SEC = 180.0

#: API token 的唯一来源 —— **按 provider 分开**，不回退、不互相借用。
API_KEY_ENVS = {PROVIDER_VLLM: "GUET_VLLM_API_KEY",
                PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY"}
#: 兼容旧引用（vLLM 那一个）。
API_KEY_ENV = API_KEY_ENVS[PROVIDER_VLLM]

def check_provider(provider) -> str:
    if provider not in PROVIDERS:
        raise ValueError("provider 必须是 %s 之一，实际 %r"
                         % (" / ".join(PROVIDERS), provider))
    return provider


# ★ 下面三个函数**按名字读模块全局**（而不是在导入时建一张 provider→对象 的映射表）：
#   映射表会把上面那几个常量**再存一份引用**，于是替换定义（`monkeypatch.setattr`）
#   之后读到的还是旧对象 —— 「冻结读的是当前定义」这条性质就断了。
#   运行时按名字取，读到的永远是当下的那一个。
def sampling_for(provider: str) -> dict:
    """这个 provider **实际会发**的采样参数（也是 manifest 要记的那一份）。"""
    return DEEPSEEK_SAMPLING if check_provider(provider) == PROVIDER_DEEPSEEK \
        else SAMPLING


def response_format_for(provider: str) -> dict:
    return DEEPSEEK_RESPONSE_FORMAT if check_provider(provider) == PROVIDER_DEEPSEEK \
        else DRAFT_RESPONSE_FORMAT


def endpoint_kind_for(provider: str) -> str:
    return ENDPOINT_RESPONSES if check_provider(provider) == PROVIDER_DEEPSEEK \
        else ENDPOINT_CHAT_COMPLETIONS


class LlmCallFailed(RuntimeError):
    """一次 LLM 往返失败。**整体失败**，不降级、不重试、不当成「查不到」。"""


def request_payload(prompt: str, model: str, *, provider: str = PROVIDER_VLLM,
                    sampling=None) -> dict:
    """**实际发给服务器**的请求体 —— 采样参数就从这里进 payload。

    ★ 与 manifest 里记的 `sampling` / `response_format` **同一份定义**
      （`sampling_for` / `response_format_for`），所以「记的是这套、发的是另一套」
      这种漂移在结构上就不可能发生。
    ★ 两个 provider 的**键名不同**：chat.completions 用 `messages` + `response_format`；
      Responses API 用 `input` + `text.format`。
    """
    provider = check_provider(provider)
    sampling = sampling_for(provider) if sampling is None else sampling
    if provider == PROVIDER_DEEPSEEK:
        payload = {"model": model, "input": prompt, "stream": False,
                   "text": {"format": copy.deepcopy(DEEPSEEK_RESPONSE_FORMAT)}}
    else:
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "stream": False,
                   "response_format": copy.deepcopy(DRAFT_RESPONSE_FORMAT)}
    payload.update(sampling)
    return payload


def _post_json(url: str, payload: dict, *, api_key: str | None,
               timeout: float, provider_label: str) -> dict:
    """一次 POST —— **任何**异常都收敛成 `LlmCallFailed`，不降级、不重试。"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as error:
        # ★ 只回 HTTP 状态码与响应体前 300 字（**不含**请求头、不含密钥）。
        detail = error.read()[:300].decode("utf-8", "ignore")
        raise LlmCallFailed("%s 返回非 200：%s %s"
                            % (provider_label, error.code, detail)) from error
    except Exception as error:                       # noqa: BLE001
        raise LlmCallFailed("%s 调用失败：%s: %s"
                            % (provider_label, type(error).__name__, error)) from error
    try:
        return json.loads(body)
    except ValueError as error:
        raise LlmCallFailed("%s 响应不是合法 JSON：%s"
                            % (provider_label, body[:300])) from error


def _fetch_chat_completions(prompt: str, *, url: str, model: str,
                            api_key: str | None, timeout: float) -> str:
    payload = request_payload(prompt, model, provider=PROVIDER_VLLM)
    data = _post_json(url.rstrip("/") + "/chat/completions", payload,
                      api_key=api_key, timeout=timeout, provider_label="vLLM")
    body = json.dumps(data, ensure_ascii=False)[:300]
    choices = data.get("choices") or []
    if not choices:
        raise LlmCallFailed("vLLM 响应没有 choices：%s" % body)
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise LlmCallFailed("vLLM 响应 message.content 为空（choices=%s）"
                            % json.dumps(choices[:1], ensure_ascii=False)[:300])
    return content


def final_output_text(data: dict) -> str:
    """Responses API 响应 → **最终结构化文本**。拿不到就抛。

    ★ **只读最终产物**：`output` 里 `type == "reasoning"` 的项一律**跳过** ——
      推理内容不是答案，写进候选就是把模型的草稿当监督标签。
    ★ 截断（`status != "completed"`）**算失败**：半截 JSON 既不该进候选，
      也不该被当成「模型答不出」。整体失败由上层负责。
    """
    status = data.get("status")
    if isinstance(status, str) and status != "completed":
        raise LlmCallFailed("DeepSeek 响应未完成（status=%s，incomplete=%s）"
                            % (status, json.dumps(data.get("incomplete_details"),
                                                  ensure_ascii=False)))
    top = data.get("output_text")
    if isinstance(top, str) and top.strip():
        return top.strip()
    parts = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue                       # ← reasoning 等一律不要
        for piece in item.get("content") or []:
            if isinstance(piece, dict) and piece.get("type") == "output_text" \
                    and isinstance(piece.get("text"), str):
                parts.append(piece["text"])
    text = "\n".join(part for part in parts if part.strip()).strip()
    if not text:
        raise LlmCallFailed("DeepSeek 响应没有最终结构化文本（顶层键：%s）"
                            % sorted(data))
    return text


def _fetch_deepseek(prompt: str, *, model: str, api_key: str | None,
                    timeout: float) -> str:
    if not (api_key or "").strip():
        # ★ 先于任何网络动作失败：带空 Authorization 去撞 401，只会白花一次往返。
        raise LlmCallFailed("DeepSeek 调用失败：%s 未设置"
                            % API_KEY_ENVS[PROVIDER_DEEPSEEK])
    payload = request_payload(prompt, model, provider=PROVIDER_DEEPSEEK)
    data = _post_json(DEEPSEEK_BASE_URL + "/" + ENDPOINT_RESPONSES, payload,
                      api_key=api_key, timeout=timeout, provider_label="DeepSeek")
    return final_output_text(data)


def fetch_draft(prompt: str, *, url: str | None = None, model: str,
                api_key: str | None = None, timeout: float = HTTP_TIMEOUT_SEC,
                provider: str = PROVIDER_VLLM) -> str:
    """把 prompt 发给模型，取回 `draft_candidate` 要的那段严格 JSON 文本。

    ★ 任何异常都收敛成 `LlmCallFailed` —— 上层只会看到「这一轮没有拿到答案」，
      而**不会**把它误当成模型输出了空内容。
    """
    provider = check_provider(provider)
    if provider == PROVIDER_DEEPSEEK:
        return _fetch_deepseek(prompt, model=model, api_key=api_key,
                               timeout=timeout)
    if not (url or "").strip():
        raise LlmCallFailed("vLLM 调用失败：缺少 --vllm-url")
    return _fetch_chat_completions(prompt, url=url, model=model,
                                  api_key=api_key, timeout=timeout)


def build_parser() -> argparse.ArgumentParser:
    """CLI 的全部参数 —— **没有** approve / promote / compile / review 开关。"""
    parser = argparse.ArgumentParser(
        description="生成 answer candidate（只产 pending；不审核、不晋升、不编译）")
    parser.add_argument("--scenarios", nargs="+", required=True, metavar="JSONL",
                        help="场景规格文件，可一次给多个")
    parser.add_argument("--out-dir", required=True,
                        help="候选产物目录（候选/redteam/manifest/运行记录都写这里）")
    parser.add_argument("--batch-id", required=True, dest="batch_id",
                        help="批次名；规则复用 compiler.answer_candidates.check_batch_id")
    parser.add_argument("--model", required=True, help="模型名，写进候选的 provenance")
    parser.add_argument("--prompt-version", required=True, dest="prompt_version",
                        help="提示词版本，写进候选的 provenance")
    parser.add_argument("--provider", choices=list(PROVIDERS),
                        default=PROVIDER_VLLM,
                        help="生成通道：vllm（默认，走 --vllm-url）或 "
                             "deepseek（官方 %s，端点 /responses）" % DEEPSEEK_BASE_URL)
    parser.add_argument("--batch-role", choices=list(BATCH_ROLES),
                        default=BATCH_ROLE_TRAINING, dest="batch_role",
                        help="training（默认）：启用**考试集排除门**，考试题一律不生成；"
                             "qualification：只用于重跑资格考试本身，排除门停用，"
                             "停用事实会写进 manifest")
    parser.add_argument("--vllm-url", dest="vllm_url", default=None,
                        help="vLLM 基础地址（如 http://127.0.0.1:8000/v1）；"
                             "provider=vllm 时必填。deepseek 走**固定**官方 base，"
                             "不提供覆盖开关")
    parser.add_argument("--max-scenarios", type=int, default=None, dest="max_scenarios",
                        metavar="N",
                        help="试产上限：按 scenario_id 升序排好当前批次角色允许的"
                             "场景后取前 N 条；不给则不限")
    return parser


def _require_text(name: str, value) -> str:
    if not (value or "").strip():
        raise ValueError("--%s 不能为空或纯空白" % name.replace("_", "-"))
    return value.strip()


def _select(specs, max_scenarios, *, batch_role):
    """挑出可生成的场景 → 按 `scenario_id` 升序 → 取前 N 条。

    ★ 生成门与晋升门分离（design §3.1）：
      `training` 允许 `pending` + `reviewed`（pending 只生成 pending candidate），
      `rejected` 一律剔除；`qualification` 仍只允许 `reviewed`。
    ★ 排序在截断**之前**，且按 `scenario_id`（装载时已保证全局唯一，因而全序）——
      同一份输入连跑两次，抽到的**永远是同一批**。
    ★★ **tool-terminal 桶（`create_ppt`）也在这里剔除** —— 它**没有末轮文本答案**，
      走的是「直接用 reviewed 场景 + 工具重放编译成 tool-terminal 样本」那条路
      （Task 8B.4 裁决）：**不问模型、不生成候选、不进 redteam**，
      也**不算成** pending / rejected / 生成失败。
    """
    allowed = ({"reviewed"} if batch_role == BATCH_ROLE_QUALIFICATION
               else {"pending", "reviewed"})
    eligible = sorted((one for one in specs if one.review_status in allowed),
                      key=lambda one: one.scenario_id)
    skipped = [one for one in specs if one.review_status not in allowed]
    tool_terminal = [one for one in eligible
                     if one.bucket in TOOL_TERMINAL_BUCKETS]
    answer_bearing = [one for one in eligible
                      if one.bucket not in TOOL_TERMINAL_BUCKETS]
    selected = (answer_bearing if max_scenarios is None
                else answer_bearing[:max_scenarios])
    return selected, skipped, tool_terminal


def batch_products(out_dir, batch_id: str) -> list:
    """这一批的**三份正式产物**路径（在不在由调用方判断）。"""
    folder = Path(out_dir)
    return [folder / ("%s.%s.jsonl" % (batch_id, key))
            for key in ("candidates", "redteam")] + \
        [folder / ("%s.manifest.json" % batch_id)]


def check_batch_is_new(out_dir, batch_id: str) -> None:
    """★ **同一 `batch_id` 不得重跑** —— 真实 LLM 的输出不保证逐字一致。

    任一产物已存在就**在工具重放与 LLM 调用之前**整体拒绝。
    **不提供 `--force`**：静默覆盖等于把上一批的审计入口抹掉，
    要重来就**换个批次名**（或人工清掉那三份）。
    """
    taken = [path.name for path in batch_products(out_dir, batch_id) if path.exists()]
    if taken:
        raise ValueError(
            "批次 %s 已存在产物 %s —— 同一 batch_id 不得重跑（真实 LLM 输出不保证"
            "逐字一致，覆盖会抹掉上一批的审计入口）。请换一个 batch_id，"
            "或人工清掉那三份后再跑。" % (batch_id, taken))


def generate(*, scenario_paths, out_dir, batch_id: str, model: str,
             prompt_version: str, draft, replay=None, tools=None,
             max_scenarios=None, provider: str = PROVIDER_VLLM,
             batch_role: str = BATCH_ROLE_TRAINING) -> dict:
    """跑一轮候选生成。**全部校验先于任何工具重放与 LLM 调用。**

    `draft` 的形状就是 `draft_candidate` 要的那个（`prompt -> JSON 文本`）；
    生产路径传 `fetch_draft`，测试传替身。
    """
    # ── 1 参数门：**在任何重放 / 任何网络调用之前** ───────────────────
    batch_id = check_batch_id(batch_id)              # 复用，不复制正则
    model = _require_text("model", model)
    prompt_version = _require_text("prompt_version", prompt_version)
    provider = check_provider(provider)
    if batch_role not in BATCH_ROLES:
        raise ValueError("batch_role 必须是 %s 之一，实际 %r"
                         % (" / ".join(BATCH_ROLES), batch_role))
    if not callable(draft):
        raise ValueError("draft 必须可调用")
    if max_scenarios is not None and max_scenarios < 0:
        raise ValueError("--max-scenarios 不能为负（实际 %r）" % max_scenarios)

    paths = [Path(one) for one in scenario_paths]
    # ── 2 两道静态门，同样先于重放 / 网络 ─────────────────────────────
    # 重名会让 manifest 里的**逻辑名称**产生歧义（两份不同的 single_tool.jsonl）。
    # 复用 `compile.py` 的那一份实现，不另写第二套规则。
    _reject_duplicate_input_names(paths)
    check_batch_is_new(out_dir, batch_id)

    # 工具名由 Registry 校验（`tools` 只是测试替身入口）。
    specs = load_scenarios(paths, tools=tools)

    # ★ **考试集排除门**：在挑场景、重放、调模型之前就剔除。
    #   排除按「scenario_id / 问句逐字相同 / provenance 含考试 candidate_id」三条机械判据，
    #   跨 id 的问句重复最危险 —— 它会让考试题悄悄进入训练候选。
    #   ★ 只有 `batch_role=qualification`（重跑资格考试本身）才停用；那一格会写进
    #     manifest —— 任何停用都是**可见**的，不会变成静默的后门。
    if batch_role == BATCH_ROLE_QUALIFICATION:
        eligible_specs, excluded_specs = list(specs), []
        exclusion_block = {"enabled": False,
                           "reason": "batch_role=qualification（重跑资格考试本身）"}
    else:
        exclusion = load_exclusion()
        eligible_specs, excluded_specs = partition_training_specs(specs, exclusion)
        exclusion_block = {
            "enabled": True,
            "pack_version": exclusion["pack_version"],
            "source_manifest_sha256": exclusion["source_manifest_sha256"],
            "scenario_ids": len(exclusion["scenario_ids"]),
            "candidate_ids": len(exclusion["candidate_ids"]),
            "input_total": len(specs),
            "excluded_count": len(excluded_specs),
            "excluded": excluded_specs,
        }

    reviewed_total = sum(1 for one in eligible_specs
                         if one.review_status == "reviewed")
    selected, skipped, tool_terminal = _select(eligible_specs, max_scenarios,
                                               batch_role=batch_role)

    candidates, redteam = [], []
    for spec in selected:
        probed = replay_spec(spec, step_fn=replay)
        outcome = draft_candidate(spec, probed, batch_id=batch_id, model=model,
                                  prompt_version=prompt_version, draft=draft,
                                  allow_pending=(batch_role
                                                 == BATCH_ROLE_TRAINING))
        if outcome.candidate is not None:
            candidates.append(outcome.candidate)
        else:
            redteam.append(outcome.redteam)

    # ── 3 走到这里说明没有一次调用失败 —— 才建目录、才写盘 ─────────────
    out_dir = Path(out_dir)
    provenance = {
        # ★ 「实际发出去的是什么」必须逐格记清：provider、端点**类型**、模型、
        #   采样、响应 schema。少记一格，读 manifest 的人就会拿另一条通道的参数
        #   去理解这一批（旧 manifest 只有 vLLM 那套，正是这种误读的来源）。
        "provider": provider,
        "endpoint": endpoint_kind_for(provider),
        "model": model,
        "prompt_version": prompt_version,
        # ★ 提示词**身份**：文本的规范摘要，与版本号两条轴。manifest 只记摘要，
        #   **不记 prompt 正文**。
        "prompt_contract_sha256": prompt_contract_sha256(),
        # ★ 就是 `request_payload` 用的那一份，不是另抄一遍的「声明值」。
        "sampling": dict(sampling_for(provider)),
        "response_format": copy.deepcopy(response_format_for(provider)),
        "batch_role": batch_role,
        # ★ 排除门的去向逐条记账：排除了谁、因为哪条判据、对应哪一道考试题。
        #   停用时也会留下 `enabled: false` 与理由。
        "qualification_exclusion": exclusion_block,
        "inputs": [{"name": path.name, "sha256": sha256_file(path)}
                   for path in paths],
        "scenarios": {"total": len(eligible_specs), "reviewed": reviewed_total,
                      "selected": len(selected),
                      "selected_ids": [one.scenario_id for one in selected],
                      # ★ 生成门的去向按 review_status 逐格记账：
                      #   training 下 pending 可入选、rejected 被跳过；
                      #   qualification 下 pending 仍被跳过。
                      "eligible_by_review_status": dict(sorted(Counter(
                          one.review_status for one in eligible_specs).items())),
                      "selected_by_review_status": dict(sorted(Counter(
                          one.review_status for one in selected).items())),
                      "skipped_by_review_status": dict(sorted(Counter(
                          one.review_status for one in skipped).items())),
                      "skipped_pending": sum(1 for one in skipped
                                             if one.review_status == "pending"),
                      "skipped_rejected": sum(1 for one in skipped
                                              if one.review_status == "rejected"),
                      # ★ tool-terminal 是**独立一格** —— 它既不是 pending / rejected，
                      #   也不是「生成失败」：它**本来就不需要**文本答案。
                      "skipped_tool_terminal": len(tool_terminal),
                      "skipped_tool_terminal_ids": [one.scenario_id
                                                    for one in tool_terminal],
                      "max_scenarios": max_scenarios},
    }
    manifest = write_candidates(batch_id, candidates, redteam, out_dir,
                                provenance=provenance)
    return {"batch_id": batch_id, "model": model, "prompt_version": prompt_version,
            "provider": provider, "endpoint": endpoint_kind_for(provider),
            "batch_role": batch_role, "exclusion": exclusion_block,
            "scenarios": provenance["scenarios"],
            "candidates": len(candidates), "redteam": len(redteam),
            "out_dir": str(out_dir), "manifest": manifest}


def main(argv=None, *, replay=None, draft=None) -> dict:
    """CLI 入口。

    `replay` / `draft` 只给测试注入替身用；生产路径两者都为 `None`，
    于是走真实 `replay_spec`（解析 → 校验 → canonical 参数）与真实 `fetch_draft`。
    """
    args = build_parser().parse_args(argv)

    # ★ 参数门先过一遍（缺/空一律在重放与网络之前失败）。
    batch_id = check_batch_id(args.batch_id)
    model = _require_text("model", args.model)
    prompt_version = _require_text("prompt_version", args.prompt_version)
    # ★ 版本号必须**就是**代码里那一版契约。允许随便写的话，「这一批用的是
    #   answer-candidate/3」就只剩一句自述 —— 而 manifest 里真正能证明内容的
    #   是 `prompt_contract_sha256`，版本号与摘要对不上时不能放行。
    if prompt_version != PROMPT_VERSION:
        raise ValueError(
            "--prompt-version=%r 与当前代码里的提示词契约 %r 不一致：版本号是契约的"
            "名字，不匹配时写出的一批会被误读成另一个契约的产物。"
            % (prompt_version, PROMPT_VERSION))
    provider = check_provider(args.provider)
    vllm_url = None
    if provider == PROVIDER_VLLM:
        vllm_url = _require_text("vllm_url", args.vllm_url)
    if args.max_scenarios is not None and args.max_scenarios < 0:
        raise ValueError("--max-scenarios 不能为负（实际 %r）" % args.max_scenarios)

    if draft is None:
        api_key = os.environ.get(API_KEY_ENVS[provider]) or None   # 只从环境变量来
        if provider == PROVIDER_DEEPSEEK and not api_key:
            # ★ 密钥为空**在重放之前**就失败：否则会白跑一遍真工具、
            #   再拿一个空 Authorization 去撞 401。
            raise ValueError("%s 未设置 —— 不发起任何请求（密钥只从环境变量读，"
                             "不走命令行）" % API_KEY_ENVS[provider])
        draft = (lambda prompt: fetch_draft(prompt, url=vllm_url, model=model,
                                            api_key=api_key, provider=provider))

    summary = generate(scenario_paths=args.scenarios, out_dir=args.out_dir,
                       batch_id=batch_id, model=model,
                       prompt_version=prompt_version, draft=draft, replay=replay,
                       max_scenarios=args.max_scenarios, provider=provider,
                       batch_role=args.batch_role)
    counts = summary["scenarios"]
    print("[candidates] batch=%s provider=%s endpoint=%s model=%s prompt=%s"
          % (batch_id, provider, summary["endpoint"], model, prompt_version))
    print("[candidates] prompt_contract_sha256=%s" % prompt_contract_sha256())
    excl = summary["exclusion"]
    if excl.get("enabled"):
        print("[candidates] 角色=%s ｜ 考试集排除 %d/%d 条%s"
              % (args.batch_role, excl["excluded_count"], excl["input_total"],
                 "（含跨 id 的问句重复 %d 条）" % sum(
                     1 for one in excl["excluded"] if one["reason"] == "question")
                 if any(one["reason"] == "question" for one in excl["excluded"])
                 else ""))
    else:
        print("[candidates] 角色=%s ｜ 考试集排除门**停用**：%s"
              % (args.batch_role, excl["reason"]))
    print("[candidates] 场景 %d（核对后入选 %d / pending %d / rejected %d / "
          "tool-terminal 跳过 %d），候选 %d，redteam %d"
          % (counts["total"], counts["selected"], counts["skipped_pending"],
             counts["skipped_rejected"], counts["skipped_tool_terminal"],
             summary["candidates"], summary["redteam"]))
    print("[candidates] 产物目录：%s" % summary["out_dir"])
    return summary


if __name__ == "__main__":
    main()
