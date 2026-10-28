# -*- coding: utf-8 -*-
"""工具层单测（独立脚本，非 0 退出即失败）。

只测**纯函数性**的工具（`query_course` / `compare_course` / `query_empty_classroom`）。
`search_regulations` 要拖起整条检索链（torch 等），不在这一层测。

2026-10-13 阶段2：判据改走 `agent.runtime.registry` —— 它是唯一的执行 / 渲染 /
schema 门面。旧的 `backend/tools/` 已删除；返回值由裸 dict 变成 `ToolResult`，
取数走 `.data`，渲染走 `registry.render`。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from agent.runtime import registry                                          # noqa: E402

FAILED = []


def check(label, cond, detail=""):
    print("  %-56s %s%s" % (label, "✓" if cond else "✗", "" if cond else "  " + str(detail)[:90]))
    if not cond:
        FAILED.append(label)


def call(name, args):
    return registry.call(name, args)


print("== query_course：精确名 ==")
r = call("query_course", {"major": "计算机科学与技术", "course": "数据库系统原理A"}).data
c = r.get("course") or {}
check("命中 数据库系统原理A", c.get("name") == "数据库系统原理A", r)
check("学分 3.0", c.get("credits") == 3.0, c.get("credits"))
check("总学时 48.0", c.get("hours") == 48.0, c.get("hours"))
check("带 sources（文件+行号）", bool(r.get("sources", {}).get("line")), r.get("sources"))
check("带指标点", len(r.get("indicators") or []) > 0, r.get("indicators"))

print("== query_course：口语名（后缀最短优先）==")
r2 = call("query_course", {"major": "计算机科学与技术", "course": "数据库系统原理"}).data
check("落到 数据库系统原理A（不是…实验）",
      (r2.get("course") or {}).get("name") == "数据库系统原理A", r2.get("course"))
check("把同名课列进 alternatives",
      "数据库系统原理实验" in (r2.get("alternatives") or []), r2.get("alternatives"))

print("== query_course：不存在的课 → 候选而不是空 ==")
res3 = call("query_course", {"major": "计算机科学与技术", "course": "量子力学导论"})
r3 = res3.data
check("返回 error", r3.get("error") == "course_not_found", r3)
check("带候选", bool(r3.get("candidates")), r3.get("candidates"))
check("不是空字符串", r3.get("candidates") != [], r3.get("candidates"))

print("== query_course：专业名对不上 ==")
r4 = call("query_course", {"major": "霍格沃茨魔法学院", "course": "魔药学"}).data
check("返回 major_not_found", r4.get("error") == "major_not_found", r4)
check("带专业候选", bool(r4.get("candidates")), r4.get("candidates"))

print("== compare_course：未开设的专业也要出一行 ==")
res5 = call("compare_course", {"course": "交通工程学",
                               "majors": ["交通工程", "计算机科学与技术"]})
r5 = res5.data
rows = {x["major"]: x for x in (r5.get("rows") or [])}
check("行数 = 请求专业数 2", len(rows) == 2, r5.get("rows"))
check("交通工程 开设", rows.get("交通工程", {}).get("found") is True, rows.get("交通工程"))
check("计算机科学与技术 **未开设行仍在**",
      "计算机科学与技术" in rows and rows["计算机科学与技术"].get("found") is False,
      rows.get("计算机科学与技术"))

# ★ 2026-10-13 改了判据：冻结 schema 里 `majors` 是**必填**，Registry 会先校验参数再执行。
#   旧 `backend/tools/` 不校验，所以「不给专业列表」那条路当时跑得通 —— 现在跑不通了，
#   这是**收紧**不是回退（计划 Task 3 Step 4 明确要求 required + 至少两个专业）。
print("== compare_course：缺专业列表 → 参数校验挡下、不执行 ==")
for label, args in (("缺 majors", {"course": "交通工程学"}),
                    ("只给一个专业", {"course": "交通工程学", "majors": ["交通工程"]}),
                    ("专业名空串", {"course": "交通工程学", "majors": ["交通工程", " "]})
                    ):
    bad = call("compare_course", args)
    check("%s -> invalid_args 且不执行" % label,
          bad.status == "tool_failed" and bad.error.code == "invalid_args", bad.error)

print("== 观察文本渲染（训练与线上共用）==")
obs = registry.render(call("query_course", {"major": "交通工程", "course": "交通工程学"}))
check("含【课程定位卡】", "【课程定位卡】" in obs, obs[:80])
check("含指标点段", "支撑的毕业要求指标点" in obs, obs[:200])
obs2 = registry.render(res5)
check("对比表含「未开设」", "未开设" in obs2, obs2[:200])
obs3 = registry.render(res3)
check("失败渲成【未找到】", "【未找到】" in obs3, obs3[:80])

print("== 未注册的工具名 ==")
bad_name = call("no_such_tool", {})
check("报错而非静默空",
      bad_name.status == "tool_failed" and bad_name.error.code == "unknown_tool",
      bad_name.error)

print("\n%d 项，失败 %d" % (len(FAILED) + 0, len(FAILED)))
sys.exit(1 if FAILED else 0)