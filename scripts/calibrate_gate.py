# calibrate_gate.py —— 门控阈值标定：用「可答题」与「越界题」**两类一起**定阈值
#
# 为什么必须有它：
#   防幻觉是这个项目的最高红线，而门控（分数低于阈值 → 返回空上下文 → 模型走
#   「暂未收录」兜底）是唯一闸门。但阈值不能凭感觉定，也不能只看可答题——
#   只看可答题会一路把阈值调低（永远不会误判空），闸门形同虚设。
#   必须同时看两个方向：
#     · 可答题被误判空 → **拒答**（该答的答不了）
#     · 越界题被判放行 → **幻觉**（库里没有的东西，模型拿着无关材料硬答）
#
# ★ 标定集必须**覆盖多个题型**。实测教训：只拿 318 正文题标出来的 0.555，
#   换到表格/专有名词题上就出现了假拒答（1/30）。所以 --testset 可以给多个文件。
#
# 用法（项目根目录）：
#   PYTHONIOENCODING=utf-8 python scripts/calibrate_gate.py --rerank \
#       --testset 测试集/factual_qa_test.json 测试集/factual_qa_test_ext.json \
#       --oos 测试集/out_of_scope_test.json
#   ... --limit 80                # 抽样快跑
#
# ⚠️ 换 embedding / reranker、或改动 rerank 的**输入形状**之后必须重跑
#    （分数分布会整体平移，旧阈值直接作废）。

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 与 eval_retrieval.py **共用**同一套「期望文档命中」判据，否则「算它可答、又说它没命中」
from eval_retrieval import expected_doc, _same_doc  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="门控阈值标定（可答题 vs 越界题）")
    p.add_argument("--testset", nargs="+",
                   default=[os.path.join(ROOT, "测试集", "factual_qa_test.json")],
                   help="可答题集（可给多个，会合并；建议 318 主集 + 扩展集一起）")
    p.add_argument("--oos", default=os.path.join(ROOT, "测试集", "out_of_scope_test.json"))
    p.add_argument("--qdrant", default=os.environ.get("QDRANT_URL", "local"))
    p.add_argument("--limit", type=int, default=None, help="每个可答集只跑前 N 题（抽样）")
    p.add_argument("--rerank", action="store_true", help="用 rerank 分标定（否则用 dense 余弦分）")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--json", default=None, help="把逐题分数/排名存成 JSON")
    return p.parse_args()


def setup(args):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from qdrant_client import QdrantClient
    from agent.tools.search_regulations import tool as vector
    from agent.tools.search_regulations import tool as rag
    if args.qdrant in ("local", "memory", ":memory:"):
        vector.set_client(QdrantClient(":memory:") if args.qdrant != "local"
                          else QdrantClient(path=os.path.join(ROOT, "qdrant_local")))
    return rag


def measure(rag, q: str, use_rerank: bool, top_k: int, expect: str | None):
    """量一题的「最高分」与「期望文档的排名」。

    ★ 必须把门控放开（high=0.0 / low=-1.0）：否则低于阈值的题会返回**空上下文**，
      分数和排名都拿不到——那就变成「用阈值去量阈值」了。
      放开门控量到的是**上界**（假设门控不误伤）；真实 R@k 只会更低，
      差额正是「门控误判空」造成的，两张表一起看才完整。
    """
    kw = (dict(rerank_high=0.0, rerank_low=-1.0) if use_rerank
          else dict(crag_high=0.0, crag_low=-1.0))
    r = rag.retrieve(q, top_k=top_k, use_reranker=use_rerank, use_llm_grader=False, **kw)
    cs = r.get("contexts") or []
    key = "rerank_score" if use_rerank else "score"
    vals = [c.get(key) for c in cs if c.get(key) is not None]
    best = max(vals) if vals else None
    rank = None
    if expect:
        rank = next((i for i, c in enumerate(cs, 1)
                     if _same_doc(expect, c.get("source") or "")), None)
    return best, rank, r


def dist(name: str, vs: list) -> None:
    v = sorted(x for x in vs if x is not None)
    miss = sum(1 for x in vs if x is None)
    if not v:
        print(f"  {name}: 全部无分数（{miss} 题）")
        return
    def q(p):
        return v[min(len(v) - 1, int(len(v) * p))]
    print(f"  {name}: n={len(v)}（{miss} 题无分数）  min {v[0]:.3f}  p1 {q(.01):.3f}  "
          f"p5 {q(.05):.3f}  p25 {q(.25):.3f}  中位 {q(.5):.3f}  max {v[-1]:.3f}")


def main() -> int:
    args = parse_args()
    rag = setup(args)

    ans: list[dict] = []
    for f in args.testset:
        part = json.load(open(f, encoding="utf-8"))
        if args.limit:
            part = part[:args.limit]
        for x in part:
            x.setdefault("_kind", "正文" if "type" not in x else x["type"])
            ans.append(x)
    oos = json.load(open(args.oos, encoding="utf-8"))

    print(f"分数来源：{'rerank 分' if args.rerank else 'dense 余弦分'}"
          f"　│　可答题 {len(ans)} 题（{' + '.join(os.path.basename(f) for f in args.testset)}）"
          f"　│　越界题 {len(oos)} 题\n")

    a_rec, o_rec, details = [], [], []
    for x in ans:
        exp = expected_doc(x.get("source") or "")
        best, rank, r = measure(rag, x["question"], args.rerank, args.top_k, exp)
        a_rec.append({"q": x["question"], "kind": x["_kind"], "best": best, "rank": rank,
                      "tier": r.get("tier")})
        details.append({"question": x["question"], "kind": "answerable", "type": x["_kind"],
                        "best": best, "rank": rank, "expected": exp,
                        "tier": r.get("tier"), "reason": r.get("reason")})
    for x in oos:
        best, _, r = measure(rag, x["question"], args.rerank, args.top_k, None)
        o_rec.append({"q": x["question"], "cat": x.get("category"), "best": best})
        details.append({"question": x["question"], "kind": "oos",
                        "type": x.get("category"), "best": best,
                        "tier": r.get("tier"), "reason": r.get("reason")})

    # ---------------------------------------------------------------- 分数分布
    dist("可答题 best 分", [r["best"] for r in a_rec])
    dist("越界题 best 分", [r["best"] for r in o_rec])

    a_ok = [r["best"] for r in a_rec if r["best"] is not None]
    o_ok = [r["best"] for r in o_rec if r["best"] is not None]
    if not a_ok or not o_ok:
        print("\n⚠️ 某一类没有分数，无法标定（检查检索是否正常）")
        return 1

    # ---------------------------------------------------------------- 阈值扫描
    lo, hi = min(min(a_ok), min(o_ok)), max(max(a_ok), max(o_ok))
    rows = []
    for i in range(41):
        T = lo + (hi - lo) * i / 40
        fa = sum(1 for x in a_ok if x < T)
        leak = sum(1 for x in o_ok if x >= T)
        rows.append((T, fa, leak))
    # 约束：合并集误判空必须为 0（红线）→ 在 fa==0 里取 leak 最小的 T
    ok_rows = [r for r in rows if r[1] == 0]
    pick = min(ok_rows, key=lambda r: r[2]) if ok_rows else min(rows, key=lambda r: r[1] + r[2])
    T = pick[0]
    print(f"\n{'阈值 T':>8}{'合并误判空':>12}{'越界放行':>10}")
    print("-" * 46)
    for t, fa, leak in rows:
        mark = "   ← 建议" if abs(t - T) < 1e-9 else ""
        print(f"{t:>8.3f}{fa:>8}/{len(a_ok):<4}{leak:>7}/{len(o_ok):<3}{mark}")
    print(f"\n★ 建议阈值 T = {T:.3f}：合并集误判空 {pick[1]}/{len(a_ok)}"
          f"（红线要求 0），越界放行 {pick[2]}/{len(o_ok)}")

    # ---------------------------------------------------------------- 两张表（用户要的）
    print(f"\n{'=' * 62}\n表一：各题型**误判空**@T={T:.3f}（越低越好）\n{'=' * 62}")
    by = collections.defaultdict(lambda: [0, 0])
    for r in a_rec:
        if r["best"] is None:
            continue
        by[r["kind"]][0] += 1
        if r["best"] < T:
            by[r["kind"]][1] += 1
    for k, (n, bad) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        flag = "  ⚠️" if bad else "  ✅"
        print(f"  {k:<12}{bad}/{n}{flag}")

    print(f"\n{'=' * 62}\n表二：各越界类别**放行**@T={T:.3f}（越低越安全）\n{'=' * 62}")
    byo = collections.defaultdict(lambda: [0, 0])
    for r in o_rec:
        if r["best"] is None:
            continue
        byo[r["cat"]][0] += 1
        if r["best"] >= T:
            byo[r["cat"]][1] += 1
    for k, (n, bad) in sorted(byo.items(), key=lambda kv: -kv[1][1]):
        print(f"  {k:<12}{bad}/{n}")

    # ---------------------------------------------------------------- 各题型分数分布（对照用）
    print(f"\n{'=' * 62}\n各题型 best 分分布\n{'=' * 62}")
    print(f"  {'题型':<12}{'n':>4}{'min':>8}{'p5':>8}{'p25':>8}{'中位':>8}{'max':>8}")
    for k in sorted(by):
        vs = sorted(r["best"] for r in a_rec if r["kind"] == k and r["best"] is not None)
        if not vs:
            continue
        def q(p, _v=vs):
            return _v[min(len(_v) - 1, int(len(_v) * p))]
        print(f"  {k:<12}{len(vs):>4}{vs[0]:>8.3f}{q(.05):>8.3f}{q(.25):>8.3f}"
              f"{q(.5):>8.3f}{vs[-1]:>8.3f}")

    # ---------------------------------------------------------------- R@k（放开门控 vs 加门控）
    # ★ 真实可用数字看**加门控**那一栏：放开门控量分数是测量手段，不是线上行为。
    #   两者之差 = 门控误判空造成的排名损失（gated_rank = None 当 best < T）。
    print(f"\n{'=' * 62}\n合并可答集 R@k：放开门控（上界） vs 加门控@T={T:.3f}（真实）\n{'=' * 62}")
    def rk(k, gated):
        ok = 0
        for r in a_rec:
            if not r["rank"] or r["rank"] > k:
                continue
            if gated and (r["best"] is None or r["best"] < T):
                continue
            ok += 1
        return ok / len(a_rec) if a_rec else 0.0
    def mr_(gated):
        s = 0.0
        for r in a_rec:
            if not r["rank"]:
                continue
            if gated and (r["best"] is None or r["best"] < T):
                continue
            s += 1 / r["rank"]
        return s / len(a_rec) if a_rec else 0.0
    print(f"  {'':<12}{'R@1':>8}{'R@3':>8}{'R@k':>8}{'MRR':>8}")
    print(f"  {'放开门控':<12}{rk(1, False):>8.1%}{rk(3, False):>8.1%}"
          f"{rk(args.top_k, False):>8.1%}{mr_(False):>8.3f}")
    print(f"  {'加门控':<12}{rk(1, True):>8.1%}{rk(3, True):>8.1%}"
          f"{rk(args.top_k, True):>8.1%}{mr_(True):>8.3f}")
    print(f"\n  按题型 R@1（加门控）：")
    for k in sorted(by):
        sub = [r for r in a_rec if r["kind"] == k]
        if not sub:
            continue
        c = sum(1 for r in sub if r["rank"] == 1
                and r["best"] is not None and r["best"] >= T)
        print(f"    {k:<12} {c}/{len(sub)}")

    # ---------------------------------------------------------------- 分布是否可分
    print()
    if max(o_ok) >= min(a_ok):
        print(f"⚠️ 两类分数区间**重叠**（越界题最高 {max(o_ok):.3f} ≥ 可答题最低 {min(a_ok):.3f}）："
              f"单一绝对阈值无法完全分离。")
        print("   这意味着无论 T 取多少，都必然在「拒答」和「幻觉」之间做取舍——")
        print("   门控拦得住「话题不相干」，拦不住「话题沾边但库里没有这个事实」。")
        print("   剩下那部分要靠：SYSTEM_PROMPT 的无信息兜底话术、use_llm_grader 对中档二次裁决、")
        print("   以及代码级 [n] 越界校验。")
    else:
        print("✅ 两类分数区间分离，绝对阈值可用。")

    if args.json:
        json.dump({"threshold": T, "score_key": "rerank_score" if args.rerank else "score",
                   "answerable": a_rec, "oos": o_rec, "details": details},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n✅ 逐题分数/排名已存 → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
