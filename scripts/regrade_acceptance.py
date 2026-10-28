# -*- coding: utf-8 -*-
"""regrade_acceptance.py —— 用**修好的判据**重判已有的验收结果（不重跑）。

第一轮判据有三处毛病，都会把**答对的判成错的**：

1. **词面锚**：`must_include=["3.0"]`，而回答里写的是「3 学分」—— 数字必须按**数值**比。
2. **`must_not` 太宽**：拿「没查到」当禁词，可模型说的是
   「**我在学校规定里没查到明确说法哈～，资料里能查到的是这些：…**」——
   那是**带保留的部分作答**，不是拒答。禁词只该盯**纯拒答**。
3. **没看 hits**：`hits=0` 说明检索压根没给东西，模型说「没找到」是**对的行为**。
   把它算成模型失败，等于拿检索的锅扣模型的头。

重判输出**三档**：`答对` / `部分（带保留）` / `拒答` / `错`。

用法：python scripts/regrade_acceptance.py <acc_out.json>
"""
import json
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
NORM = lambda s: re.sub(r"\s+", "", str(s or ""))

# 纯拒答口径（模型自己承认「这儿没有」）。**只认这些**，不含「没查到明确说法」
# ——后者常出现在**先保留、后作答**的句型里。
HARD_NOINFO = ["暂时没有收录", "没有收录到", "还没有收录到", "目前还没有收录",
               "暂时没找到", "没找到官方依据", "没找到正式说法"]
SOFT_HEDGE = ["没查到明确说法", "没写到", "查不到", "没有明确"]


def num_eq(answer, tok):
    """`3.0` 与「3 学分」等价；`30%` 只比数。"""
    a = NORM(answer)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(%?)", str(tok))
    if not m:
        return NORM(tok) in a
    v, pct = float(m.group(1)), m.group(2)
    if pct and pct not in a:
        return False
    return any(abs(float(x) - v) < 1e-6 for x in re.findall(r"\d+(?:\.\d+)?", a))


def main():
    rows = json.load(open(sys.argv[1], encoding="utf-8"))
    per = defaultdict(lambda: defaultdict(int))
    verdict = []
    for r in rows:
        ans = r["answer"] or ""
        hard = [k for k in HARD_NOINFO if NORM(k) in NORM(ans)]
        soft = [k for k in SOFT_HEDGE if NORM(k) in NORM(ans)]
        miss = [t for t in (r.get("missing") or []) if not num_eq(ans, t)]
        if r["behavior"] == "未收录":
            v = "答对" if (hard or soft) else "错"
        elif r["behavior"] == "空教室指引卡":
            v = "答对" if hard or soft else "错"
        elif miss:
            v = "错"
        elif hard:
            # 库里有东西却给纯拒答 —— 这就是第 12 号待办的正脸
            v = "拒答" if (r["call_hits"] or [0]) and sum(r["call_hits"] or [0]) > 0 \
                else "拒答(检索空)"
        elif soft:
            v = "部分（带保留）"
        else:
            v = "答对"
        p = per[r["behavior"]]
        p["n"] += 1
        p[v] += 1
        p["tool"] += r["tool_ok"]
        if r["args_ok"] is not None:
            p["na"] += 1
            p["args"] += r["args_ok"]
        p["hit"] += r["hit_ok"]
        verdict.append((r["id"], r["behavior"], v, r["call_hits"], ans[:46]))

    print("%-16s %4s %5s %7s %7s %6s" % ("行为", "题数", "工具对", "参数对", "有命中", "答对"))
    tn = tt = tna = ta = th = tk = 0
    for k, v in per.items():
        args = ("%d/%d" % (v["args"], v["na"])) if v["na"] else "—"
        print("%-16s %4d %5d %7s %7d %6d" % (k, v["n"], v["tool"], args, v["hit"], v["答对"]))
        tn += v["n"]; tt += v["tool"]; th += v["hit"]; tk += v["答对"]
        tna += v["na"]; ta += v["args"]
    print("%-16s %4d %5d %7s %7d %6d" % ("合计", tn, tt, "%d/%d" % (ta, tna), th, tk))
    print()
    for tag in ("拒答", "部分（带保留）", "错", "拒答(检索空)"):
        xs = [x for x in verdict if x[2] == tag]
        if not xs:
            continue
        print("== %s（%d 条）==" % (tag, len(xs)))
        for i, b, v, h, a in xs[:12]:
            print("   id=%-4s %-12s hits=%-8s %s" % (i, b, h, a))
        print()


if __name__ == "__main__":
    main()