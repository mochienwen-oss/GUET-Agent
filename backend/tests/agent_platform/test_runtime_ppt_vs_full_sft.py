# -*- coding: utf-8 -*-
"""Task 8B.4 裁决的**运行时侧对照**：训练样本与线上真实轨迹是同一条序列。

★ 本文件**不修改任何生产实现** —— 只用**假模型**（记录它实际收到的 `messages`）
  与**假 registry.call**（不连 Qdrant / vLLM）跑一遍真图，把两条序列摆在一起比。

★ 两条序列的角色名**本来就不同**，这是有意的（训练集用 `function_call` /
  `observation`，运行时不出现这两个角色名，由 `dataset_info.json` 的
  `function_tag` / `observation_tag` 在训练时装换）。所以比的是**语义槽位**：

    训练 `function_call`  ≡  运行时 `assistant`(带 tool_calls)
    训练 `observation`    ≡  运行时 `tool`

★ 裁决后的正确比法（**不是**「整个训练序列 == 运行时输入」）：

```text
运行时第 2 轮「模型输入」  ==  训练样本**去掉末尾 create_ppt 调用之后**的前缀
训练样本**末尾那一条**     ==  运行时第 2 轮「模型返回的 create_ppt 调用」
```

  前者是上下文，后者是监督目标 —— 两者不是一回事，混起来比会把「模型该生成什么」
  误当成「模型看到了什么」。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest backend/tests/agent_platform/test_runtime_ppt_vs_full_sft.py -q
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "训练集"))

from llm import llm_client  # noqa: E402

from agent.core.contracts import Source, ToolResult  # noqa: E402
from agent.runtime import graph, registry  # noqa: E402
from agent.tools.create_ppt import tool as ppt_tool  # noqa: E402
from agent.tools.create_ppt.tool import create_ppt_result  # noqa: E402
from compiler.compile import build_trajectory  # noqa: E402
from compiler.models import ScenarioSpec  # noqa: E402

#: ★ 用**生产那一句**，不用哨兵串 —— 运行时用的是它，两边要比就得同一个起点。
PROMPT = llm_client.SYSTEM_PROMPT
SEEDS = ROOT.parent / "训练集" / "scenarios"
QUESTION = "帮我做一份讲学校学业预警制度的 PPT，给大一新生班会用"

SEARCH_ARGS = {"query": "学业预警的认定条件、帮扶措施与退出机制",
               "keywords": ["学业预警"]}
#: 假模型在第 2 轮返回的调用 —— 训练样本的末尾必须**逐字**等于它。
PPT_CALL_ARGS = {"goal": "向大一新生讲清学业预警制度：认定条件、帮扶措施、退出机制",
                 "audience": "大一新生与班主任"}

#: 语义槽位：把角色名折成两类可比的东西。
SLOT = {"system": "system", "user": "user", "function_call": "call",
        "observation": "obs", "tool": "obs"}


def _seed_spec(name="create_ppt.jsonl") -> ScenarioSpec:
    first = (SEEDS / name).read_text(encoding="utf-8").splitlines()[0]
    return ScenarioSpec.model_validate(json.loads(first))


def _fake_call(name, args=None, *, context=None):
    """假 registry.call —— 只造形状，不碰 Qdrant / 搜索引擎。"""
    if name == "create_ppt":
        return create_ppt_result(**(args or {}))
    if context is not None:  # Offline compiler replay has no runtime task context.
        assert context["task"].goal == QUESTION
    return ToolResult(
        tool="search_regulations", ok=True, status="success", data={},
        sources=[Source(doc_id="fixture", chunk_id="fixture:0",
                        title="《夹具测试规程》", heading_path=["第一章 夹具"],
                        text="夹具正文（非真实校内事实）", line=1, score=0.9)])


def _fake_ppt():
    async def _s(history, question, **kwargs):
        yield ("ppt", {"ppt_task": {"task_id": "t1", "stage": "plan"}})
        yield ("done", {"contexts": [], "calls": [], "n_refs": 0})
    return _s


def _drive_runtime(monkeypatch, seen):
    """跑**真的** `run_stream`，把模型每一轮实际收到的 messages 记下来。

    模型行为对齐种子里的两步：第 1 轮 `search_regulations`，
    看过 observation 之后第 2 轮 `create_ppt`。
    """
    search = {"name": "search_regulations", "arguments": dict(SEARCH_ARGS)}

    async def fake_decide(messages):
        seen.append(list(messages))
        return {"type": "tool_calls", "calls": [dict(search)]}

    async def fake_answer(messages, tool_choice="auto"):
        seen.append(list(messages))
        if tool_choice == "none":
            yield ("delta", "（收尾）")
            return
        if any(m.get("role") == "tool" for m in messages):
            yield ("calls", [{"name": "create_ppt",
                              "arguments": dict(PPT_CALL_ARGS)}])
        else:
            yield ("calls", [dict(search)])

    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    monkeypatch.setattr(registry, "call", _fake_call)
    monkeypatch.setattr(ppt_tool, "reserve_task", lambda owner, conv: "t1")
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt())

    async def _go():
        return [item async for item in graph.run_stream([], QUESTION)]
    return asyncio.run(_go())


def _compiled_messages(seed="create_ppt.jsonl"):
    """真种子 + 假重放 → 训练样本的**消息部分**。"""
    spec = _seed_spec(seed)

    def fake_replay(tool, arguments):
        result = _fake_call(tool, arguments)
        return {"tool": tool, "status": result.status, "result": result,
                "observation": registry.render(result)}

    messages, _probed = build_trajectory(spec, system_prompt=PROMPT,
                                         replay=fake_replay)
    return messages


def _slots(messages):
    """把一串 messages 折成**语义槽位**序列（训练角色名 → 运行时角色名）。"""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            out.append("call" if m.get("tool_calls") else "answer")
        else:
            out.append(SLOT.get(role, role))
    return out


def _calls_of(message):
    """从一条调用轮里取出 `[(name, arguments_json)]` —— 两种形状都认。

    训练集用 `function_call`（内容是一个 JSON 数组），运行时用
    `assistant(tool_calls=…)`。两边都把参数序列化成 **`ensure_ascii=False` 的字符串**
    （`replay.call_message` 与 `graph.round_messages` **同一口径**），所以能逐字比。
    """
    if message.get("role") == "function_call":
        return [(one["name"], json.dumps(one["arguments"], ensure_ascii=False))
                for one in json.loads(message["content"])]
    return [(one["function"]["name"], one["function"]["arguments"])
            for one in (message.get("tool_calls") or [])]


# ══ 一、运行时真实形状（特征化，应绿）═══════════════════════════════════

def test_the_runtime_turn_ends_right_after_the_create_ppt_handoff(monkeypatch):
    """模型选了 `create_ppt` → 交子图 → **本轮结束**，没有再问一轮写正文。"""
    seen = []
    items = _drive_runtime(monkeypatch, seen)

    assert [ev for ev, _ in items] == ["ppt", "done"]
    assert items[-1][1]["decision"] == "ppt"
    # 模型只被问了**两次**：第 1 轮决策 + 看过 observation 后的第 2 轮。
    assert len(seen) == 2


def test_the_create_ppt_call_and_its_observation_never_reach_the_model(monkeypatch):
    """运行时不回流：子图拿的是**另起的一套状态**（`PptState`），
    主图消息列表里没有 `create_ppt`，也没有 `【PPT任务】` 那条 observation。"""
    seen = []
    _drive_runtime(monkeypatch, seen)
    blob = json.dumps(seen[-1], ensure_ascii=False)

    assert "create_ppt" not in blob
    assert "【PPT任务】" not in blob
    assert [m["role"] for m in seen[-1]] == ["system", "user", "assistant", "tool"]


def test_a_text_plus_call_round_silently_drops_the_create_ppt_call(monkeypatch):
    """★ **登记一条既有行为**（Task 8B.4 §3.1，**另立任务，本轮不改生产图**）。

    看过 observation 的那一轮里，模型若**同时**说了一句话又喊 `create_ppt`，
    调用会被丢掉、PPT 任务**根本不会开始**。判据在 `agent_decide`
    （`graph.py:103-108`）：流式轮「有文本就以文本为准」，返回的 `tool_calls` 被显式清空。

    这条规则有它的道理（边写边喊的调用会把已经流出去的一段话晾在半空），
    但它同时意味着——**「末轮 assistant 写交付话术」这种结构会把 create_ppt 顶掉**，
    是裁决 A 的直接反证。**本测试只钉住现状，不主张这是正确行为。**
    """
    search = {"name": "search_regulations", "arguments": dict(SEARCH_ARGS)}

    async def fake_decide(messages):
        return {"type": "tool_calls", "calls": [dict(search)]}

    async def fake_answer(messages, tool_choice="auto"):
        if any(m.get("role") == "tool" for m in messages):
            yield ("delta", "好的，我这就为你制作这份 PPT。")
            yield ("calls", [{"name": "create_ppt",
                              "arguments": dict(PPT_CALL_ARGS)}])
        else:
            yield ("calls", [dict(search)])

    started = []
    monkeypatch.setattr(graph.model, "decide", fake_decide)
    monkeypatch.setattr(graph.model, "answer", fake_answer)
    monkeypatch.setattr(registry, "call", _fake_call)
    monkeypatch.setattr(ppt_tool, "reserve_task",
                        lambda owner, conv: started.append(conv) or "t1")
    monkeypatch.setattr(ppt_tool, "run_stream", _fake_ppt())

    async def _go():
        return [item async for item in graph.run_stream([], QUESTION)]
    items = asyncio.run(_go())

    assert started == [], "create_ppt 竟然被执行了 —— 这条特征化断言需要重新核对"
    assert [ev for ev, _ in items] == ["delta", "done"]
    assert items[-1][1]["decision"] != "ppt"


# ══ 二、训练样本与运行轨迹对齐（裁决后的正确比法）═══════════════════════

def test_the_runtime_second_round_input_is_the_compiled_prefix(monkeypatch):
    """★ **模型输入**那一半：运行时第 2 轮收到的 == 训练样本去掉末尾调用后的前缀。"""
    seen = []
    _drive_runtime(monkeypatch, seen)
    runtime_input = seen[-1]

    compiled = _compiled_messages()
    assert compiled[-1]["role"] == "function_call", "末尾应当是 create_ppt 的调用"
    prefix = compiled[:-1]

    assert _slots(runtime_input) == _slots(prefix), (
        "运行时输入：%s\n训练前缀：%s" % (_slots(runtime_input), _slots(prefix)))
    # 逐条内容也要对得上，不只是槽位形状。
    assert runtime_input[0]["content"] == prefix[0]["content"]     # system（生产那句）
    assert runtime_input[1]["content"] == prefix[1]["content"]     # user
    assert _calls_of(runtime_input[2]) == _calls_of(prefix[2])     # search 调用
    assert runtime_input[3]["content"] == prefix[3]["content"]     # search 的 observation


def test_the_compiled_sample_ends_with_the_call_the_runtime_model_makes(monkeypatch):
    """★ **监督目标**那一半：训练样本末尾 == 运行时模型第 2 轮返回的 create_ppt 调用。

    ★ 这一条就是原分析的错误之处 —— 末尾那个 `function_call` **不是**下一轮输入上下文，
      它是**这一轮要监督模型生成的输出**，所以必须留在样本里。
    """
    seen = []
    _drive_runtime(monkeypatch, seen)
    compiled = _compiled_messages()

    assert _calls_of(compiled[-1]) == [("create_ppt",
                                        json.dumps(PPT_CALL_ARGS,
                                                   ensure_ascii=False))]


def test_nothing_is_compiled_after_the_create_ppt_call():
    """★ 终态调用之后**什么都不写**：没有 observation，也没有末轮文本 assistant。"""
    compiled = _compiled_messages()

    assert [m["role"] for m in compiled] == [
        "system", "user", "function_call", "observation", "function_call"]
    blob = json.dumps(compiled, ensure_ascii=False)
    assert "【PPT任务】" not in blob
    assert not any(m.get("role") == "assistant" for m in compiled)


# ══ 三、普通桶没有回归 ══════════════════════════════════════════════════

def test_ordinary_buckets_still_write_their_final_observation():
    """★ 非 tool-terminal 桶照旧把**每一步**的 observation 都写进去 —— 没有一刀切。"""
    compiled = _compiled_messages("single_tool.jsonl")

    assert [m["role"] for m in compiled] == [
        "system", "user", "function_call", "observation"]
    assert compiled[-1]["role"] == "observation"
