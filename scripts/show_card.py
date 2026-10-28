# -*- coding: utf-8 -*-
"""show_card.py —— 直接打印一张「课程定位卡」，**不经过对话链路**。

用法：
    python scripts/show_card.py <专业关键词> <课程关键词>
    python scripts/show_card.py 计算机科学与技术 数据库系统原理

这是阶段 2 的检查点：底座抽出来了、卡片长什么样，先在这里看。
匹配是简化版（子串 + join_key）；正式匹配策略在阶段 3 的 `backend/tools/plan.py`。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")      # Windows 控制台默认 GBK，装不下 ⚠️/中文标点

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from build_plan_db import join_key                                       # noqa: E402

DB = os.path.join(ROOT, "backend", "data", "plan_db.json")


def load():
    with open(DB, encoding="utf-8") as f:
        return json.load(f)


def find_major(db, kw):
    hits = [m for m in db["majors"] if kw in m.get("major", "") or kw in m["file"]]
    return hits


def find_course(major, kw):
    k = join_key(kw)
    exact = [c for c in major["courses"] if join_key(c["name"]) == k]
    if exact:
        return exact
    return [c for c in major["courses"] if k and k in join_key(c["name"])]


def render(major, course):
    L = []
    L.append("### %s ・ %s" % (course["name"], major["major"]))
    L.append("")
    L.append("| 项 | 值 |")
    L.append("| --- | --- |")
    rows = [
        ("课程代码", course["code"]), ("课程性质", course["nature"]),
        ("学分", course["credits"]), ("总学时", course["hours"]),
        ("理论 / 实验 / 实践", " / ".join(
            "—" if v is None else str(v) for v in (course["theory"], course["lab"], course["practice"]))),
        ("开课学期", "第 %s 学期" % course["semester"] if course["semester"] else ""),
        ("必修 / 选修", "必修" if course["required"] else "选修"),
        ("开课部门", course["dept"]),
        ("所属模块", " › ".join(course["module_path"]) or course["module"]),
        ("课程地位", "核心课程" if course.get("is_core") else
                     ("主要实践环节" if course.get("is_practice") else "—")),
    ]
    for k, v in rows:
        L.append("| %s | %s |" % (k, v if v not in (None, "") else "—"))
    L.append("")
    inds = course.get("indicators") or []
    if inds:
        L.append("**支撑的毕业要求指标点**")
        L.append("")
        L.append("| 指标点 | 内容 |")
        L.append("| --- | --- |")
        for i in inds:
            L.append("| %s | %s |" % (i, major["indicator_text"].get(i, "")))
    else:
        L.append("**支撑的毕业要求指标点**：未在第六节矩阵中找到")
    L.append("")
    L.append("---")
    L.append("**%s**｜%s｜学制 %s｜%s｜毕业总学分 %s" % (
        major.get("major", "—"), major.get("college", "—"), major.get("duration") or "—",
        major.get("degree") or "—", major.get("total_credits") or "—"))
    if major["module_credits"]:
        L.append("")
        L.append("各模块学分明细：" + "、".join("%s %s" % (k, v) for k, v in major["module_credits"].items()))
    L.append("")
    L.append("（出处：%s 第 %s 行）" % (major["file"], course["src_line"]))
    return "\n".join(L)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    db = load()
    ms = find_major(db, sys.argv[1])
    if not ms:
        cand = sorted({m["major"] for m in db["majors"] if m.get("major")})
        sys.exit("找不到专业「%s」。候选（前 10）：%s" % (sys.argv[1], cand[:10]))
    major = ms[0]
    if len(ms) > 1:
        print("⚠️ 专业关键词命中 %d 份，取第一份：%s\n" % (len(ms), major["file"]))
    cs = find_course(major, sys.argv[2])
    if not cs:
        near = [c["name"] for c in major["courses"] if join_key(sys.argv[2])[:3] in join_key(c["name"])][:8]
        sys.exit("在「%s」里找不到课程「%s」。候选：%s" % (major["major"], sys.argv[2], near))
    if len(cs) > 1:
        print("⚠️ 课程关键词命中 %d 门，取第一门：%s\n" % (len(cs), "、".join(c["name"] for c in cs[:6])))
    print(render(major, cs[0]))


if __name__ == "__main__":
    main()