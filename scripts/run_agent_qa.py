# -*- coding: utf-8 -*-
"""跑 agent_qa_test.json 全量 40 题 —— plan 阶段 5 的端到端验收。

每题落盘三样：**问句 / 这轮调了哪些工具 / 最终答案**。

判两件事**分开判**（plan 5.2）：

  ① 第 1 轮工具选对没 —— 脚本自动判（对照 `want_tool`）
  ② 第 2 轮答案对不对 —— **人工**判。脚本只落盘，**不替人判对错**：
     判读原文没法机械代理（见记忆 `proxy-judgments-need-human-verification`）。

用法（服务器上，后端起着）：
    python run_agent_qa.py                       # 默认打 localhost:6006
    python run_agent_qa.py --out /tmp/qa.json
"""
import argparse
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://localhost:6006"


def login(username, password):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("token", "")


def ask(token, question):
    """调一次 /api/chat，边收 SSE 边攒 `calls` 与最终 `replace`。"""
    req = urllib.request.Request(
        BASE + "/api/chat",
        data=json.dumps({"message": question}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    calls, answer, err = [], "", ""
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            s = line.decode("utf-8", "ignore").strip()
            if not s.startswith("data:"):
                continue
            body = s[5:].strip()
            if body == "[DONE]":
                break
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                continue
            if obj.get("calls"):
                calls = obj["calls"]
            if obj.get("replace"):
                answer = obj["replace"]
            if obj.get("error"):
                err = obj["error"]
    return calls, answer, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(HERE, "agent_qa_test.json"))
    ap.add_argument("--out", default="/tmp/agent_qa_out.json")
    ap.add_argument("--user", default=os.environ.get("QA_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("QA_PASSWORD", ""))
    a = ap.parse_args()

    cases = json.load(open(a.cases, encoding="utf-8"))
    token = login(a.user, a.password)
    if not token:
        raise SystemExit("登录失败 —— 用户名/密码？")
    print("登录 OK，共 %d 题\n" % len(cases))

    rows, ok_tool = [], 0
    for c in cases:
        calls, answer, err = ask(token, c["question"])
        got = [x["name"] for x in calls]
        want = c.get("want_tool")
        hit = (want in got) if want else (not got)
        ok_tool += hit
        rows.append({"id": c["id"], "kind": c["kind"], "question": c["question"],
                     "want_tool": want, "got_tools": got, "tool_ok": hit,
                     # ★ 键名是 `args`，**不是** `arguments` —— `agent.py` 的 calls_log
                     #   写的是 `{"name","args","ms","hits"}`。读错键名会**恒得 null**，
                     #   于是「工具选对了但参数全错」这种情况在报告里看不出来
                     #   （实测踩过：29/30 两题参数全对，报告里却一直是 null）。
                     "call_args": [x.get("args") for x in calls],
                     "call_hits": [x.get("hits") for x in calls],
                     "answer": answer, "error": err})
        mark = "✅" if hit else "❌"
        print("%2d %-6s %s 期望 %-18s 实得 %s" % (
            c["id"], c["kind"], mark, want or "不调", got or "（未调）"))

    json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n① 工具选对 %d / %d" % (ok_tool, len(cases)))
    print("② 答案对错**要人工判** —— 落盘在 %s（每题含 question / got_tools / answer）" % a.out)
    # 空答案单独拎出来：那是「跑了但没产出」，比答错更该先看
    blank = [r["id"] for r in rows if not r["answer"] and not r["error"]]
    errored = [(r["id"], r["error"]) for r in rows if r["error"]]
    if blank:
        print("⚠️ 空答案（跑了没产出）：%s" % blank)
    if errored:
        print("⚠️ 报错：%s" % errored)


if __name__ == "__main__":
    main()