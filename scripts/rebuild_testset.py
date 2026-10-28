# -*- coding: utf-8 -*-
"""rebuild_testset.py —— 测试集重构：**只换问法，不动事实**。

## 为什么必须重构

实测（2026-10-11，逐字比对，去空白后）：

| 测试集 | 题数 | 逐字出现在训练集里 |
|---|---|---|
| `factual_qa_test.json` | 318 | **318（100%）** |
| `factual_qa_test_ext.json` | 30 | **30（100%）** |
| `colloquial_test.json` | 108 | 54（「formal」那半边，即上面那批的同一批题） |
| `agent_qa_test.json` | 42 | 1 |

也就是说那套「主评测集」量的**不是泛化，是记忆**。而且这个污染 **v4 就有了**，
不是本轮引入的 —— 但既然要重跑，就必须先把它清掉，否则重跑出来的漂亮数字没意义。

## 做法：只改问句，答案/来源原样保留

事实（answer + source）是测试集的**被测对象**，一个字都不动；
只把问句换一种说法，让它与训练集**不再逐字重合**。

每道题生成 N 个候选，按三条挑第一个合格的：
  1. 去空白后**不在训练集**里（这是硬门）
  2. 与原句不同、且长度在 [0.45x, 2.2x] 之间（防止把限定词说没了）
  3. 与原句**至少共享一个 2-gram**（防止它自作主张换了另一件事问）

挑不出来的题**原样保留并计入报告** —— 宁可留着不动，也不许悄悄换成一个别的事实。

用法：
    python scripts/rebuild_testset.py --in 测试集/factual_qa_test.json \
        --out 测试集/factual_qa_test_v2.json --n 6
"""
import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPT = """你是出题人。下面有一道题，请**换一种问法**问同一件事。

要求：
1. 不许照抄原句的措辞，句式和用词要明显不同；
2. 事实、范围、限定词（年份 / 专业 / 文号 / 数字 / 部门）一个都不能改、不能丢；
3. 说人话，像学生或老师随口问的，别用书面腔；
4. **只输出这一句问句**，不要解释、不要加引号、不要编号。

原题：%s
来源：%s
"""


def norm(s):
    return re.sub(r"\s+", "", s or "")


def bigrams(s):
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def load_train_qs(path):
    out = set()
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        for m in json.loads(line)["messages"]:
            if m.get("role") == "user" and m.get("content"):
                out.add(norm(m["content"]))
    return out


def ask_vllm(model, url, question, source, temperature=0.9, max_tokens=80):
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "top_p": 0.9, "repetition_penalty": 1.05,
            "messages": [{"role": "user", "content": PROMPT % (question, source or "（未给）")}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        o = json.loads(r.read())
    t = (o["choices"][0]["message"].get("content") or "").strip()
    return t.strip().strip('"').strip("“”").split("\n")[0].strip()


def diff_ratio(new, old):
    return 1.0 - difflib.SequenceMatcher(None, norm(old), norm(new)).ratio()


def acceptable(new, old, train, min_diff):
    """硬门三条 + 差异度**只做下限**（下限之上的档位由调用方挑最优）。"""
    n, o = norm(new), norm(old)
    if not n or n == o:
        return False, "同句/空"
    if n in train:
        return False, "仍在训练集里"
    if not (0.45 * len(o) <= len(n) <= 2.2 * len(o)):
        return False, "长度离谱"
    if not (bigrams(new) & bigrams(old)):
        return False, "与原题无共同片段"
    if diff_ratio(new, old) < FLOOR:
        return False, "与原句太像(<%.2f)" % FLOOR
    return True, ""


FLOOR = 0.12      # 差异度的绝对下限：低于它连「换过说法」都算不上


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--train", default=os.path.join(ROOT, "训练集", "train_v7.jsonl"))
    ap.add_argument("--n", type=int, default=6, help="每题最多试几个候选")
    ap.add_argument("--qkeys", default="question",
                    help="问句字段名，逗号分隔（colloquial 只有 formal 半边要换）")
    ap.add_argument("--only-kind", default=None,
                    help="只重写 kind == 该值的条目（如 formal）")
    ap.add_argument("--model", default=os.environ.get("VLLM_MODEL", "guet-v2"))
    ap.add_argument("--url", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 题（冒烟用）")
    ap.add_argument("--min-diff", type=float, default=0.25,
                    help="与原句的最小字符差异比例（防止只换一个词的假改写）")
    a = ap.parse_args()

    train = load_train_qs(a.train)
    items = json.load(open(a.src, encoding="utf-8"))
    if not isinstance(items, list):
        items = items.get("items") or items.get("questions")
    if a.limit:
        items = items[:a.limit]

    done, kept, fails, loose = [], 0, [], []
    for it in items:
        if a.only_kind and it.get("kind") != a.only_kind:
            continue
        old = it.get("question")
        if not old or norm(old) not in train:
            kept += 1
            continue
        best, best_d = None, 0.0
        for _ in range(a.n):
            try:
                cand = ask_vllm(a.model, a.url, old, it.get("source"))
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                fails.append((old[:30], "vllm:%s" % str(e)[:50]))
                break
            ok, why = acceptable(cand, old, train, a.min_diff)
            if ok:
                d = diff_ratio(cand, old)
                if d >= a.min_diff:
                    best, best_d = cand, d
                    break
                if d > best_d:                      # 先攒着，全试完还没够格的再用它
                    best, best_d = cand, d
        if best is not None:
            it["question_src"] = old          # ★ 原句留着，供人对照复核
            it["question"] = best
            it["question_diff"] = round(best_d, 3)
            (loose if best_d < a.min_diff else done).append((old, best, best_d))
        else:
            fails.append((old[:30], "N 轮都没挑出合格候选（连 %.2f 都不到）" % FLOOR))
    print("改写 %d 题（其中 %d 题只到「次优档」，差异度 < %.2f）；"
          "未改写（本来就不在训练集）%d 题；失败 %d 题"
          % (done + len(loose), len(loose), a.min_diff, kept, len(fails)))
    if loose:
        print("   次优档样例（人读一眼，看事实有没有被改掉）：")
        for o, n, d in loose[:5]:
            print("      %.2f  %s  →  %s" % (d, o[:26], n[:30]))
    for f in fails[:10]:
        print("   ! %s  ← %s" % f)

    json.dump(items, open(a.dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("落盘 %s" % a.dst)
    left = [norm(it["question"]) for it in items
            if it.get("question") and norm(it["question"]) in train]
    print("★ 仍与训练集逐字重合：%d 题%s"
          % (len(left), "" if not left else "  ← 不合格，要用 --n 加大重试"))
    for q in left[:6]:
        print("    ! %s" % q[:50])
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())