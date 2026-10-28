# -*- coding: utf-8 -*-
"""参考资料预算 vs 训练覆盖 —— **启动时的护栏**。

## 为什么要有它（2026-10-10 事故）

线上 `MAX_CONTEXT_CHARS` 从 3200 提到 6000、又开 2a，喂给模型的 system 长过了
**训练时见过的长度**（训练集最长 system 4978 tokens，线上给到 5000+），模型开始
**复读**（「依据的依据的依据…」）。而这件事**没有任何报错**：

  · 配置回显只打「ctx_chars=6000」，看不出这个 6000 有没有超出训练覆盖；
  · 检索、精排、接口状态码全正常；
  · 从现象上看像"模型坏了"，实际是"喂的东西它没见过"。

定位它花了整整一晚（最后靠**抓包代理**抄下后端真实请求，再和直连逐字 diff 才对上）。
所以这里把那条不变式**写进机器**：

    线上预算 ≤ 训练数据落盘时用的预算

## 机制

`backend/data/ctx_budget.json` 由训练流水线落盘时生成（见
`训练集/_work/tutor/ctx_budget.py`），记的是**这批训练数据按什么预算造的**。
本模块在启动时比对：

  · 缺文件            → 跳过（`README` 里说明：老部署不放这个文件也能起）
  · 线上 ≤ 声明       → 通过，横幅里打一行数字
  · 线上 >  声明       → 默认**打 ERROR**（横幅里也标出来）；
                        `CTX_BUDGET_STRICT=1` 时**直接抛**，拒绝启动

## 为什么默认不阻断

铁律 12：新机制读不到/出问题时**不许让服务挂掉**（生产要能起来）。
但"默认只打日志"正是本项目最贵的那类静默失败 —— 所以这里做了两件事补偿：
  ① 这行日志打在**启动横幅紧邻处**，任何人 `tail` 一眼就能看见；
  ② 单元测试/评测跑批时把 `CTX_BUDGET_STRICT=1` 打开，**当场失败**，不让污染结果。

⚠️ 它断言的是**预算（字符）**，不是 token。原因：token 取决于当次检索到的内容，
启动时算不出来。字符不涨 ⇒ token 不会系统性涨，方向保守、够用。
训练侧真正的 token 覆盖也一并打出来供人对照。
"""
import json
import logging
import os

from core.paths import BACKEND_ROOT

logger = logging.getLogger("uvicorn.error")

# 尾部指令 + note 的量级（build_messages 里拼在【参考资料】之后的那两段）。
# 只用于**打印参考**，不参与断言 —— 断言走「预算 vs 预算」，那是确定性的。
_TAIL_OVERHEAD_CHARS = 210


def _manifest_path() -> str:
    here = str(BACKEND_ROOT)
    return os.path.join(here, "data", "ctx_budget.json")


def check(max_context_chars: int, system_prompt: str, strict: bool = False) -> bool:
    """返回是否通过。`strict=True` 且不通过时抛 RuntimeError。"""
    path = _manifest_path()
    if not os.path.isfile(path):
        logger.warning(
            "预算护栏 | 没有 %s —— **跳过**（老部署不放这个文件也照常起）。"
            "要启用：跑 训练集/_work/tutor/ctx_budget.py 生成它。", path)
        return True
    try:
        m = json.load(open(path, encoding="utf-8"))
    except Exception as e:                     # 文件坏了也不许拖垮启动
        logger.warning("预算护栏 | %s 读不动（%s）—— 跳过", path, e)
        return True

    declared = m.get("max_context_chars")
    train_max_sys = m.get("train_max_system_chars")
    train_max_tok = m.get("train_max_system_tokens")
    ok = (declared is not None) and (max_context_chars <= int(declared))

    # 线上理论上界（供人对照：训练样本极少塞满预算，所以它通常比"实测最长"大）
    online_ceil = max_context_chars + len(system_prompt or "") + _TAIL_OVERHEAD_CHARS

    if ok:
        logger.info(
            "预算护栏 | 线上 %d ≤ 训练声明 %d ✓ | 训练实测：最长 system %s 字 / %s tokens，"
            "线上理论上界 %d 字（%d 条，%s）",
            max_context_chars, declared, train_max_sys, train_max_tok,
            online_ceil, m.get("n_samples"), m.get("built_at"))
        return True

    msg = ("预算护栏 | ❌ **线上 %d > 训练声明 %d** —— 线上会喂出训练没见过的长度，"
           "模型可能复读/空答（2026-10-10 事故正是如此）。"
           "正确处置：按 %d 的预算重出数据 + 重训；"
           "只做临时实验则可设 CTX_BUDGET_STRICT=0 忽略本行。"
           "（训练实测最长 system %s 字 / %s tokens，%d 条，%s）"
           % (max_context_chars, declared, max_context_chars,
              train_max_sys, train_max_tok, m.get("n_samples"), m.get("built_at")))
    if strict:
        raise RuntimeError(msg)
    logger.error(msg)
    return False