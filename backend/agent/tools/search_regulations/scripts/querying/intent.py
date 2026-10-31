# -*- coding: utf-8 -*-
"""意图判定与**路由**（**可复用模块**：方案 6 的窄带软化 与 方案 4 的辅助查询共用）。

★ **两层判据**（2026-10-08 用户人核要求，必须照此写，不得混用）：
  · **第一层 · 场景判据**：通用学业词（学分/成绩/挂科/专业…）+ 第一人称。
    它只回答「**用户是不是在自述情况**」，**不得单独定族** ——
    因为「学分」在培养方案题里也到处都是，拿它定族会大面积误触发。
  · **第二层 · 定族判据**：必须命中该族的**路由专名**（见 route_templates.json）。
    学业预警另有一条**复合专名**：(门数|累计学分|重修) ∧ (学分|必修|不及格)
    —— 因为「门数」「累计学分」单独太泛，必须与新词同现才算定族。

★ 路由模板是**数据、人核产物**（`backend/data/route_templates.json`），不是代码。
  读不到/为空 → **不路由**（失败开放，退回主路）。
"""
import json
import os
import re
from pathlib import Path
from core.paths import BACKEND_ROOT

_BACKEND = BACKEND_ROOT
ROUTE_PATH = str(_BACKEND / "data" / "route_templates.json")

# ── 第一层：通用学业词（**只做场景判据**，不得单独定族）────────────────────────
SCENE_KW = [
    "学分", "绩点", "成绩", "分数", "门课", "门数", "必修", "选修",
    "挂科", "挂了", "不及格", "重修", "补考", "缓考",
    "学期", "学年", "入学", "毕业", "休学", "复学", "退学",
    "专业", "学院", "系", "班",
    "四级", "六级", "雅思", "托福", "考了",
]
_PRON = re.compile(r"我|咱|本人")
_YEAR = re.compile(r"大[一二三四五六]|研[一二三]|\d{4}\s*级")

# ── 流程词（方案 6 的窄带触发条件之一）────────────────────────────────────────
# ⚠️ 「怎么申请」**故意不收**（用户 2026-10-08 决定）：库里没有奖助文件，
#    一旦命中流程词被窄带提级，会把「未收录」变成「拿无关块硬答」。
FLOW_KW = [
    "流程", "程序", "步骤", "怎么办", "怎么办理", "如何办理", "怎么弄",
    "手续", "走什么", "哪些环节", "什么环节", "几个环节", "需要哪些材料",
    "要交什么", "交什么材料", "多久", "多长时间", "什么时候办",
]

_ROUTE = None       # 进程内缓存（null 表示还没读）


# ★ 含「大学」的**固定术语**：扫外校之前必须先剥掉它们。
#   2026-10-08 实测缺陷：`_RE_UNIV` 在「**认定大学**英语」里匹配到「认定大学」→
#   判成外校 → **整个「英语认定」族被拦**（318 里 8 道漏报、覆盖率 100%→79%）。
#   这些词里的「大学」是**术语的一部分**，不是机构名。
_FIXED_TERMS = ["大学英语", "大学体育", "大学生", "大学物理", "大学数学",
                "大学语文", "大学计算机", "大学外语"]


def _has_other_univ(q: str) -> bool:
    """查询里是否出现**外校** → 是则不路由（2026-10-08 用户批准的外校过滤）。

    ★ 复用 `coverage._RE_UNIV`（单一来源），但先做两步清理：
      ① **剥掉本校名**：`桂林电子科技大学` 自带「大学」，不剥会被自己误判成外校；
      ② **剥掉含「大学」的固定术语**（大学英语/大学体育…）：否则「认定大学英语」
         里的「认定大学」会被当成机构名，把整族拦掉（实测踩过）。
    ★ **只把「…大学」当外校**，不把「…学院」算进去 —— 本校的二级学院
      （外国语学院/计算机学院…）也长这样，收进来会误杀大量正当问句。
      代价：外校「…学院」型漏报 → 退回主路，**按纪律可接受**。
    """
    try:
        from agent.tools.search_regulations.scripts.evidence_ops.coverage import _RE_UNIV, _OURS
    except Exception:
        return False
    s = (q or "").replace(_OURS, "")
    for t in _FIXED_TERMS:
        s = s.replace(t, "·")
    for m in _RE_UNIV.finditer(s):
        if m.group(1).endswith("大学"):
            return True
    return False


def load_routes(path: str | None = None) -> dict:
    """读路由登记表 → {族名: {"强专名":[…], "弱专名":[…], "复合专名":{…}, "templates":[…]}}。

    **失败一律返回 {}**（文件缺失 / JSON 坏 / 结构不对）→ 不路由，退回主路。
    与 `refs.load_allowlist` 同思路：拷不到就自动关闭，不是报错、更不是瞎猜。
    """
    global _ROUTE
    p = path or ROUTE_PATH
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        fam = raw.get("families")
        if not isinstance(fam, dict):
            raise ValueError("route_templates.json 结构应为 {\"families\": {...}}")
        out = {}
        for name, v in fam.items():
            if not isinstance(v, dict):
                continue
            out[name] = {"强专名": list(v.get("强专名") or []),
                         "弱专名": list(v.get("弱专名") or []),
                         "复合专名": v.get("复合专名") or {},
                         "templates": list(v.get("templates") or [])}
        _ROUTE = out
        return out
    except Exception:
        _ROUTE = {}
        return {}


def routes() -> dict:
    return _ROUTE if _ROUTE is not None else load_routes()


def scene_flags(query: str) -> dict:
    """第一层：场景判据。返回 {pron, entity, hits}。**不涉及任何族**。"""
    q = query or ""
    hits = [k for k in SCENE_KW if k in q]
    if _YEAR.search(q):
        hits.append(_YEAR.search(q).group(0))
    return {"pron": bool(_PRON.search(q)), "entity": bool(hits), "hits": hits}


def detect_topics(query: str, routes_: dict | None = None, max_topics: int = 2) -> dict:
    """第二层：定族。返回 {topics, dropped, hits}。

    **只选最可能的 1-2 族**（`max_topics`）：按命中专名数排序取前 N；
    超过 N 的**不静默丢**，放进 `dropped` 供留痕/审计（多族同现时全捞，
    但不做「唯一猜测」—— 也不把四族模板全量上）。
    """
    q = query or ""
    rr = routes_ if routes_ is not None else routes()
    # ★ 外校过滤（用户批准）：查询出现外校名 → 一律不路由。
    if _has_other_univ(q):
        return {"topics": [], "dropped": [], "hits": {}, "blocked": "外校"}
    scored, hits = [], {}
    for name, spec in rr.items():
        strong = [k for k in spec.get("强专名", []) if k in q]
        weak = [k for k in spec.get("弱专名", []) if k in q]
        comp = spec.get("复合专名") or {}
        a = [k for k in (comp.get("A") or []) if k in q]
        b = [k for k in (comp.get("B") or []) if k in q]
        h = list(strong)
        if a and b:                       # 复合专名命中 → 等价于一条强专名
            h = h + ["复合:" + "∧".join(a + b)]
        # ★★ 弱专名**只在同族强专名（含复合）命中时才计入** ——
        #    这是 2026-10-08 硬门槛失败（「公示」触发转专业、「认定」触发英语认定到
        #    别族问句）的统一修法，不做个案打补丁。
        if h and weak:
            h = h + ["弱:" + w for w in weak]
        if not h:
            continue
        hits[name] = h
        scored.append((len(h), sum(len(x) for x in h), name))
    scored.sort(reverse=True)
    picked = [n for _, _, n in scored[:max_topics]]
    dropped = [n for _, _, n in scored[max_topics:]]
    return {"topics": picked, "dropped": dropped, "hits": hits, "blocked": ""}


def detect(query: str) -> dict:
    """合并两层 → 一个字典（方案 6 与方案 4 都只用这一个入口）。

    返回 {flow, scenario, topics, dropped, hits, aux_queries}。
    `scenario` = 第一人称 ∧ 业务实体（**不含族**）；
    `topics`  = 命中路由专名的族（**定族只认专名**）。
    """
    q = query or ""
    sf = scene_flags(q)
    td = detect_topics(q)
    rr = routes()
    aux = []
    for t in td["topics"]:
        aux += [x for x in (rr.get(t, {}).get("templates") or []) if x]
    return {
        "flow": any(k in q for k in FLOW_KW),
        "scenario": sf["pron"] and sf["entity"],
        "topics": td["topics"], "dropped": td["dropped"], "blocked": td.get("blocked", ""),
        "hits": {**{t: td["hits"][t] for t in td["hits"]},
                 **({"flow": [k for k in FLOW_KW if k in q]} if any(k in q for k in FLOW_KW) else {}),
                 "scene": sf["hits"][:6]},
        "aux_queries": aux,
    }


def matches(query: str, tags=("flow", "scenario")) -> bool:
    d = detect(query)
    return any(d.get(t) for t in tags)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    load_routes()
    for q in (sys.argv[1:] or [
        "我大二，材料成型及控制工程专业，想转到自动化专业。",
        "一学期门数 和 累计学分 两把尺子冲突时按哪个算？",
        "我已经被列入拟接收名单了，后面还要多久？",
        "我六级考了 480 分，上个学期考的。",
        "国家励志奖学金怎么申请？",
        "帮我查一下我室友张三这学期的各科成绩。",
    ]):
        d = detect(q)
        print("%-36s\n    scenario=%-5s flow=%-5s topics=%-14s dropped=%s\n    aux=%s"
              % (q[:36], d["scenario"], d["flow"], d["topics"], d["dropped"],
                 (d["aux_queries"][0][:46] + "…") if d["aux_queries"] else "(无)"))
