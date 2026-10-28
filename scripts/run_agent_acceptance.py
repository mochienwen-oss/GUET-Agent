# -*- coding: utf-8 -*-
"""跑 agent 验收集（`测试集/agent_acceptance.json`）—— 端到端，**四项分开判**。

## 判什么

| 判据 | 怎么判 | 为什么单列 |
|---|---|---|
| `tool_ok` | 期望工具在实得工具里（`want_tool=None` 则要求**没调**） | 第 1 轮的路由 |
| `args_ok` | `want_args` 每一项都对得上实得参数 | ★ 只判工具名会**假绿**：参数全错也报过 42/42（memory `agent-acceptance-must-check-args`） |
| `hit_ok` | 需要资料的题，`hits > 0` | 分得清「模型答错」和「库里根本没查到」 |
| `behav_ok` | `must_include` 都在、`must_not` 都不在 | 第 12 号待办的正脸：**有数据不许说未收录** |

`judge == "human"` 的题不机械判行为对错，只落盘原文 —— 判读没法机械代理
（memory `proxy-judgments-need-human-verification`）。

## 多轮

每题先建一个会话，`question` + `turns` 依次发；判据看**最后一轮**。

用法（服务器上，后端起着）：
    PYTHONIOENCODING=utf-8 python scripts/run_agent_acceptance.py --out /tmp/acc.json
"""
import argparse
import collections
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://localhost:6006"
NORM = lambda s: re.sub(r"\s+", "", str(s or ""))


def login(user, password):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("token", "")


def new_conv(token):
    req = urllib.request.Request(
        BASE + "/api/conversations", data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["id"]


def ask(token, question, conv_id):
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=json.dumps({"message": question, "conversation_id": conv_id}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    calls, answer, err = [], "", ""
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            s = line.decode("utf-8", "ignore").strip()
            if not s.startswith("data:"):
                continue
            b = s[5:].strip()
            if b == "[DONE]":
                break
            try:
                o = json.loads(b)
            except json.JSONDecodeError:
                continue
            if o.get("calls"):
                calls = o["calls"]
            if o.get("replace"):
                answer = o["replace"]
            if o.get("error"):
                err = o["error"]
    return calls, answer, err


def args_match(want, got):
    """`want` 的每一项都要在**某一个**实得调用里对上。

    字符串：去空白后全等；列表（如 `majors`）：**按集合比**，顺序不算错。
    """
    if not want:
        return None
    for c in got:
        a = c.get("args") or {}
        ok = True
        for k, v in want.items():
            av = a.get(k)
            if isinstance(v, list):
                if not isinstance(av, list) or \
                        sorted(NORM(x) for x in v) != sorted(NORM(x) for x in av):
                    ok = False
                    break
            elif NORM(v) != NORM(av):
                ok = False
                break
        if ok:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(
        os.path.dirname(HERE), "测试集", "agent_acceptance.json"))
    ap.add_argument("--out", default="/tmp/agent_acceptance_out.json")
    ap.add_argument("--user", default=os.environ.get("QA_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("QA_PASSWORD", ""))
    ap.add_argument("--only", default=None, help="只跑某个 behavior")
    a = ap.parse_args()

    cases = json.load(open(a.cases, encoding="utf-8"))
    if a.only:
        cases = [c for c in cases if c["behavior"] == a.only]
    token = login(a.user, a.password)
    if not token:
        raise SystemExit("登录失败 —— 用户名/密码？")
    print("登录 OK，共 %d 题\n" % len(cases))

    rows = []
    for c in cases:
        conv = new_conv(token)
        calls, answer, err = [], "", ""
        for q in [c["question"]] + list(c.get("turns") or []):
            calls, answer, err = ask(token, q, conv)
        got = [x["name"] for x in calls]
        want = c.get("want_tool")
        tool_ok = (want in got) if want else (not got)
        am = args_match(c.get("want_args"), calls)
        need_ctx = c["behavior"] not in ("不该调工具", "越界拒绝")
        hits = sum((x.get("hits") or 0) for x in calls)
        hit_ok = (not need_ctx) or hits > 0
        mi = [t for t in (c.get("must_include") or []) if NORM(t) not in NORM(answer)]
        mn = [t for t in (c.get("must_not") or []) if NORM(t) in NORM(answer)]
        behav_ok = (not mi) and (not mn)
        rows.append({**{k: c[k] for k in ("id", "kind", "behavior", "question")},
                     "turns": c.get("turns"), "want_tool": want,
                     "want_args": c.get("want_args"), "got_tools": got,
                     "call_args": [x.get("args") for x in calls],
                     "call_hits": [x.get("hits") for x in calls],
                     "tool_ok": tool_ok, "args_ok": am, "hit_ok": hit_ok,
                     "behav_ok": behav_ok, "missing": mi, "forbidden": mn,
                     "judge": c.get("judge", "auto"),
                     "answer": answer, "error": err})
        print("%3d %-14s %s%s%s%s  %s" % (
            c["id"], c["behavior"],
            "T" if tool_ok else "t", "-" if am is None else ("A" if am else "a"),
            "H" if hit_ok else "h", "B" if behav_ok else "b",
            (c["question"][:34])))
        sys.stdout.flush()

    json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    auto = [r for r in rows if r["judge"] == "auto"]
    per = collections.defaultdict(lambda: [0, 0, 0, 0, 0])

    def bump(r, field, idx, cond):
        per[r["behavior"]][0] += 1
        per[r["behavior"]][idx] += bool(cond)

    for r in rows:
        bump(r, "tool", 1, r["tool_ok"])
        if r["args_ok"] is not None:
            per[r["behavior"]][4] += 1
            per[r["behavior"]][2] += bool(r["args_ok"])
        bump(r, "hit", 3, r["hit_ok"])
    print("\n%-16s %6s %8s %8s %8s" % ("行为", "题数", "工具对", "参数对", "有命中"))
    for k, (n, t, a_, h, na) in per.items():
        print("%-16s %6d %8d %8s %8d" % (k, n, t, ("%d/%d" % (a_, na)) if na else "—", h))
    print("\n行为判据（只统计 judge=auto 的 %d 题）：通过 %d，不过 %d"
          % (len(auto), sum(1 for r in auto if r["behav_ok"]),
             sum(1 for r in auto if not r["behav_ok"])))
    for r in auto:
        if not r["behav_ok"]:
            print("   ! id=%-3s %-12s 缺=%s 禁=%s | %s" % (
                r["id"], r["behavior"], r["missing"], r["forbidden"], r["answer"][:52]))
    blank = [r["id"] for r in rows if not r["answer"] and not r["error"]]
    if blank:
        print("⚠️ 空答案：%s" % blank)
    print("\n落盘 %s —— 答案原文在里面，`judge=human` 的题要人读" % a.out)


if __name__ == "__main__":
    main()