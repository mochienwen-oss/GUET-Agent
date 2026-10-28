# eval_retrieval.py —— 用测试集量化检索质量（**不涉及 LLM**）
#
# 为什么必须有它：调「切分入库质量 / 向量模型 / rerank」时，靠肉眼看回答是调不动的——
# 模型问题会掩盖检索问题，检索问题也会被模型的胡说掩盖。这里直接算命中率。
#
# 用法（在项目根目录执行，默认用内嵌 Qdrant，无需 Docker）：
#   python scripts/eval_retrieval.py                  # 全量 318 题
#   python scripts/eval_retrieval.py --limit 60       # 抽样快速看
#   python scripts/eval_retrieval.py --rerank         # 叠加 bge-reranker 精排
#   python scripts/eval_retrieval.py --top-k 8
#   python scripts/eval_retrieval.py --audit          # 分块体检（不跑检索）
#   python scripts/eval_retrieval.py --save base.json # 存基线
#   python scripts/eval_retrieval.py --compare base.json   # 改完对比基线
#
# 指标：
#   Recall@k —— 期望文档出现在前 k 条里的比例（本题唯一的客观指标）
#   MRR      —— 期望文档首次出现排名的倒数均值
# 注意：期望文档**不在知识库**里的题会被单独统计（它们永远不可能命中，
#       混进指标里会把数字稀释得看不出改进）。

import argparse
import collections
import json
import os
import re
import sys
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)


def parse_args():
    p = argparse.ArgumentParser(description="测试集检索评测（不涉及 LLM）")
    p.add_argument("--testset", default=os.path.join(ROOT, "测试集", "factual_qa_test.json"))
    p.add_argument("--qdrant", default=os.environ.get("QDRANT_URL", "local"),
                   help="local=内嵌(无需Docker)，或 http://ip:6333")
    p.add_argument("--collection", default=None,
                   help="**默认读 settings.qdrant_collection**（env_file 已改绝对路径，必然正确）。"
                        "脚本内**禁止硬编码默认库名** —— 2026-10-08 事故：跑批脚本的 CWD "
                        "决定它读不读 .env，静默用了 config 默认的 school_docs（旧库），"
                        "一整轮 A/B 白跑。")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 题（抽样）")
    p.add_argument("--rerank", action="store_true", help="叠加 reranker 精排")
    p.add_argument("--audit", action="store_true", help="只做分块体检，不跑检索")
    p.add_argument("--save", default=None, help="把结果存成 JSON 基线")
    p.add_argument("--compare", default=None, help="和之前的基线 JSON 对比")
    return p.parse_args()


def setup_backend(args):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # ⚠️ 原先这里写死 os.environ.setdefault("EMBED_MODEL", "BAAI/bge-large-zh-v1.5")。
    #    环境变量优先级高于 config.py，换模型后它会把新模型**静默顶掉**——而入库与查询
    #    必须同一个模型，否则指标全废。模型统一由 backend/config.py 决定（现在默认 bge-m3）。
    #    （使用本地模型目录时天然规避了「裸名被补成 sentence-transformers/xxx → 404」。）
    if args.qdrant not in ("local", "memory", ":memory:"):
        os.environ["QDRANT_URL"] = args.qdrant
    # ★ 库标识**一律读 settings**（禁止脚本内硬编码默认库名）。
    #   显式写回环境变量是**故意的**：让"这次跑的是哪个库"在同一处可读、可回显，
    #   而不是散落在"settings 默认值 + .env 有没有被读到"两处推理里。
    from core import config
    os.environ["QDRANT_COLLECTION"] = args.collection or config.settings.qdrant_collection

    from agent.tools.search_regulations import tool as rag
    from agent.tools.search_regulations import tool as vector

    if args.qdrant in ("local", "memory", ":memory:"):
        from qdrant_client import QdrantClient
        if args.qdrant in ("memory", ":memory:"):
            vector.set_client(QdrantClient(":memory:"))
        else:
            vector.set_client(QdrantClient(path=os.path.join(ROOT, "qdrant_local")))
    return rag, vector


def _norm_title(s: str) -> str:
    """标题归一化，**只用于匹配**（不用于显示）。

    ⚠️ 必须做 NFKC：语料标题写的是「管理办法(修订)」（半角括号），
    测试集里写的是「管理办法（修订）」（全角括号）——不归一化会把已入库的文档
    误判成「缺失」。
    顺便去掉「章程..._01」这类导出后缀和标题里的空格。
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"_\d{1,3}$", "", s)
    return re.sub(r"\s+", "", s)


def _doc_key(title: str) -> str:
    """取出「比对用的文档名」。

    知识库里的 source 是**文件名**，常写成「关于印发《X》的通知（桂电教〔2025〕12号）」，
    而测试集里只写「X」。所以优先取书名号里的内容来比，没有书名号就用整串。
    另外很多文件名省略了「桂林电子科技大学」前缀，靠 _same_doc 的双向包含兜住。
    """
    m = re.search(r"《([^》]+)》", _norm_title(title))
    return _norm_title(m.group(1)) if m else _norm_title(title)


def _same_doc(a: str, b: str) -> bool:
    """判断两个标题指的是不是同一份文档（双向包含，容忍前缀/后缀差异）。"""
    ka, kb_ = _doc_key(a), _doc_key(b)
    return bool(ka) and bool(kb_) and (ka in kb_ or kb_ in ka)


def expected_doc(source: str) -> str:
    """测试集的 source 形如《桂林电子科技大学博士后管理工作实施办法》第三章 第十条 → 取书名号里的文档名"""
    m = re.search(r"《([^》]+)》", source or "")
    return (m.group(1) if m else (source or "")).strip()


def scroll_all(client, collection):
    out, offset = [], None
    while True:
        recs, offset = client.scroll(collection_name=collection, limit=2000,
                                     offset=offset, with_payload=True)
        out.extend(r.payload or {} for r in recs)
        if offset is None:
            return out


# ------------------------------------------------------------------ 分块体检
def audit(vector):
    from core.config import settings
    vector.ensure_collection()
    client = vector.client()
    payloads = scroll_all(client, settings.qdrant_collection)
    if not payloads:
        print("❌ 向量库是空的，先跑 scripts/ingest_local.py 灌数据")
        return

    per_doc = collections.defaultdict(lambda: {"smalls": 0, "parents": set(), "chars": 0})
    for pl in payloads:
        d = per_doc[pl.get("source") or "(无标题)"]
        d["smalls"] += 1
        d["parents"].add(pl.get("parent_id"))
        d["chars"] += len(pl.get("text") or "")

    print(f"\n{'=' * 84}\n分块体检：{len(per_doc)} 个文档，{len(payloads)} 个小块\n{'=' * 84}")
    print(f"{'小块':>5} {'父块':>5} {'总字数':>8}  文档")
    rows = sorted(per_doc.items(), key=lambda kv: kv[1]["smalls"])
    for name, d in rows:
        flag = "⚠️ " if d["smalls"] <= 2 else "  "
        print(f"{flag}{d['smalls']:>5} {len(d['parents']):>5} {d['chars']:>8}  {name[:60]}")

    thin = [n for n, d in per_doc.items() if d["smalls"] <= 2]
    print(f"\n小块数 ≤ 2 的文档：{len(thin)} 个（这些文档的切分很可能退化了，"
          f"多半是正文没有标题层级 → 只能整块或硬切）")


# ------------------------------------------------------------------ 检索评测
def evaluate(rag, args):
    data = json.load(open(args.testset, encoding="utf-8"))
    if args.limit:
        data = data[:args.limit]

    # 知识库里实际有哪些文档（从向量库读，反映真实情况而不是文件系统）
    from core.config import settings
    rag.ensure_collection()
    # ★ **关键配置回显**（2026-10-08 事故的固化措施）：一次跑批到底用了哪个库、哪台设备、
    #   什么阈值、哪些开关 —— 全部打在最前面，任何一处静默漂移都能一眼看见。
    #   事故教训：库标识错了的时候，指标只是"对不上"，**不会有任何报错**。
    _bool = lambda b: "on" if b else "off"
    print("配置回显 | collection=%s | embed=%s | rerank=%s/fp16=%s | "
          "top_k=%s rerank_pool=%s near_dup=%s | 阈值 low=%.2f high=%.2f | "
          "开关 2a=%s 2b=%s 2d=%s 方案1=%s 方案4=%s 方案6=%s"
          % (settings.qdrant_collection, settings.embed_device,
             settings.rerank_device, settings.rerank_fp16,
             settings.top_k, settings.rerank_pool, settings.near_dup_threshold,
             rag.DEFAULT_RERANK_LOW, rag.DEFAULT_RERANK_HIGH,
             _bool(settings.flow_expand), _bool(settings.ref_follow),
             _bool(settings.multiturn_carry), _bool(settings.sparse_fuse_gate),
             _bool(settings.aux_route), _bool(settings.gate_scenario_band)))
    kb = {pl.get("source") or "" for pl in scroll_all(rag.client(), settings.qdrant_collection)}
    kb = {t for t in kb if t}

    def in_kb(exp: str) -> bool:
        return any(_same_doc(exp, t) for t in kb)

    answerable = [x for x in data if in_kb(expected_doc(x["source"]))]
    missing = [x for x in data if not in_kb(expected_doc(x["source"]))]
    print(f"\n{'=' * 84}")
    print(f"测试集 {len(data)} 题　│　可答 {len(answerable)} 题　│　"
          f"期望文档缺失 {len(missing)} 题")
    print(f"{'=' * 84}")
    if missing:
        miss_docs = collections.Counter(expected_doc(x["source"]) for x in missing)
        print(f"\n⚠️  以下 {len(miss_docs)} 个文档不在知识库里，这 {len(missing)} 题永远不可能命中：")
        for n, c in miss_docs.most_common():
            print(f"      {c:3d} 题  《{n[:56]}》")
        print("   → 先补录这些文档（scripts/pdf2md.py），再回来看命中率\n")

    ranks, results = [], []
    by_diff = collections.defaultdict(lambda: {"n": 0, "hit": 0})
    for x in answerable:
        exp = expected_doc(x["source"])
        r = rag.retrieve(x["question"], top_k=args.top_k, use_reranker=args.rerank,
                         use_llm_grader=False)
        ctx = r.get("contexts") or []
        # 与 in_kb 用同一套判据，否则会出现「算它可答、又说它没命中」的自相矛盾
        rank = next((i for i, c in enumerate(ctx, 1)
                     if _same_doc(exp, c.get("source") or "")), None)
        ranks.append(rank)
        # 上下文多样性：按**归一化后**的文档名去重。
        # ⚠️ 必须归一化：语料里同一份校规存在半角/全角括号两个副本，不折叠的话
        #    两份会被算成两个「不同文档」，这个指标就跟 R@1 一样瞎了。
        seen = {_norm_title(c.get("source") or "") for c in ctx}
        seen.discard("")
        # ★ 跨文档**近似**内容占用 —— R@1 与 dup_slot_rate **都看不见**这一层，加它就是为了
        #   让隐藏问题可见：dup_slot_rate 按**归一化文档名**去重，而 65 份培养方案是
        #   **不同的文档名 + 同一段模板文字**（只差专业名和几个数字），于是
        #   「5 个槽位里 4 个是同一段话」这种窗口在两个指标上都报 0，看起来一切正常。
        #   2026-10-03 实测正是它掩盖了近重复族问题：318 题当天 5 道未命中里的 4 道，
        #   top-6 全是各方案的同段文字，而 R@1 / 重复占用都没有异常。
        n_near = 0
        for _i, _c in enumerate(ctx):
            _hi = rag.shingles(_c.get("text") or "")
            if not _hi:
                continue
            for _c2 in ctx[:_i]:
                if _c2.get("document_id") == _c.get("document_id"):
                    continue
                _hj = rag.shingles(_c2.get("text") or "")
                if _hj and len(_hi & _hj) / len(_hi | _hj) >= 0.6:
                    n_near += 1
                    break
        results.append({"question": x["question"], "expected": exp, "rank": rank,
                        "tier": r.get("tier"), "got": [c.get("source") for c in ctx],
                        "n_ctx": len(ctx), "n_distinct": len(seen), "n_near_dup": n_near})
        dd = by_diff[x.get("difficulty") or "?"]
        dd["n"] += 1
        dd["hit"] += 1 if rank else 0

    def recall(k):
        return sum(1 for r in ranks if r and r <= k) / len(ranks) if ranks else 0.0

    mrr = (sum(1 / r for r in ranks if r) / len(ranks)) if ranks else 0.0
    n = len(ranks)

    # 上下文多样性：R@1 只问「期望文档在不在」，看不出「同一份文档的两三个副本占满
    # top-k」这种冗余——实测有这样的窗口，而 rerank 分数只差 0.0006，精排也分不开。
    # 所以冗余要单独量，否则去重有没有效果根本无从判断。
    n_ctx_tot = sum(r["n_ctx"] for r in results)
    n_dis_tot = sum(r["n_distinct"] for r in results)
    n_near_tot = sum(r.get("n_near_dup", 0) for r in results)
    avg_ctx = n_ctx_tot / n if n else 0.0
    avg_distinct = n_dis_tot / n if n else 0.0
    dup_rate = (1 - n_dis_tot / n_ctx_tot) if n_ctx_tot else 0.0
    near_rate = n_near_tot / n_ctx_tot if n_ctx_tot else 0.0
    single = sum(1 for r in results if r["n_ctx"] and r["n_distinct"] == 1)

    print(f"可用题目 {n}　│　Recall@1 {recall(1):.1%}　Recall@3 {recall(3):.1%}　"
          f"Recall@{args.top_k} {recall(args.top_k):.1%}　MRR {mrr:.3f}\n")
    print(f"上下文多样性（top-{args.top_k}）：平均不同文档 {avg_distinct:.2f} / {avg_ctx:.2f} 槽位"
          f"　│　重复占用 {dup_rate:.1%}")
    # ★ 与"重复占用"分开报：那个按文档名去重，看不见"不同文档、同一段模板文字"。
    #   近重复族（65 份同模板培养方案）恰好落在这个盲区里。
    print(f"    跨文档近似内容占用 {near_rate:.1%}（不同文档但文字相似度 ≥0.6 的槽位）")
    if single:
        print(f"    ⚠️  整窗只有 1 份文档的题：{single} 题（{single / n:.1%}）"
              f"——这些题模型拿到的是同一段内容的多个副本，去重语料可直接改善")
    print()
    print("按难度：")
    for d in ("easy", "medium", "hard", "?"):
        v = by_diff.get(d)
        if v and v["n"]:
            print(f"    {d:7s} {v['hit']:3d}/{v['n']:3d}  {v['hit'] / v['n']:.1%}")

    miss = [r for r in results if not r["rank"]]
    if miss:
        print(f"\n未命中 {len(miss)} 题（前 25 条）：")
        for r in miss[:25]:
            print(f"  ❓ {r['question'][:52]}")
            print(f"      期望《{r['expected'][:40]}》　实际: "
                  f"{' | '.join((g or '?')[:24] for g in r['got'][:3]) or '（空）'}")

    if args.compare and os.path.exists(args.compare):
        base = json.load(open(args.compare, encoding="utf-8"))
        print(f"\n{'=' * 84}\n与基线对比（{args.compare}）\n{'=' * 84}")
        print(f"{'指标':<12}{'基线':>10}{'现在':>10}{'变化':>10}")
        for label, old, new in (("Recall@1", base["recall@1"], recall(1)),
                                ("Recall@3", base["recall@3"], recall(3)),
                                (f"Recall@{args.top_k}", base["recall@k"], recall(args.top_k)),
                                ("MRR", base["mrr"], mrr)):
            d = new - old
            print(f"{label:<12}{old:>10.3%}{new:>10.3%}{d:>+10.3%}")
        print(f"{'可答题数':<12}{base['n']:>10}{n:>10}{n - base['n']:>+10}")
        # 多样性指标是后加的，老基线里没有这几个键——有才比，别让对比炸掉
        if "dup_slot_rate" in base:
            for label, old, new in (("平均不同文档", base["avg_distinct_docs"], avg_distinct),
                                    ("重复占用槽位", base["dup_slot_rate"], dup_rate)):
                d = new - old
                print(f"{label:<12}{old:>10.3f}{new:>10.3f}{d:>+10.3f}")
            if "near_dup_slot_rate" in base:
                d = near_rate - base["near_dup_slot_rate"]
                print(f"{'近似内容占用':<12}{base['near_dup_slot_rate']:>10.3f}{near_rate:>10.3f}{d:>+10.3f}")
        else:
            print("（基线是加多样性指标之前存的，这几项无法对比）")

    if args.save:
        json.dump({"n": n, "recall@1": recall(1), "recall@3": recall(3),
                   "recall@k": recall(args.top_k), "mrr": mrr, "top_k": args.top_k,
                   "rerank": args.rerank,
                   "avg_distinct_docs": avg_distinct, "dup_slot_rate": dup_rate,
                   "near_dup_slot_rate": near_rate,
                   "single_doc_queries": single, "details": results},
                  open(args.save, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n✅ 基线已存 → {args.save}")


def main():
    args = parse_args()
    rag, vector = setup_backend(args)
    if args.audit:
        audit(vector)
    else:
        evaluate(rag, args)


if __name__ == "__main__":
    main()
