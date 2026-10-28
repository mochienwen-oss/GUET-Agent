# retrieval.py —— dense/BM25 → RRF → 去重 → 精排 → 文档限额 → 证据门
#
# 只负责「检索」这一半，读 indexing 建好的向量库。
import json
import math
import re
import time
from agent.core.request_control import check_cancelled
from agent.core.scope import business_scope_conflict, evidence_use_role
import unicodedata
from pathlib import Path

from qdrant_client import models

from core.config import settings

import logging           # 方案 6 的窄带提级**留痕**用（其余日志走调用方）
logger = logging.getLogger(__name__)

from . import gate_signals
from .evidence_gate import (
    POLICY_V1, GateDecision, GatePolicy, claim_anchor_supported, decide,
)
from .indexing import _get_client, _get_encoder, ensure_collection
from .query_plan import build_query_plan
from .trace import RetrievalTrace
from .rerank_scores import SCORE_VERSION, predict_scores, threshold_on_single_scale
from .table_selection import select_table_rows
from .target_records import select_target_record
from .evidence_units import reassemble

# ⚠️ query 侧指令前缀随模型而变，所以放进 config（见 settings.query_prefix）。
#   bge-m3 **不需要**指令（官方用法是无指令检索）；bge-large-zh-v1.5 那一类才需要
#   「为这个句子生成表示以用于检索相关文章：」。把某个模型的 convention 写死在这里，
#   换模型时就会静默地用错误的 convention 去检索。

# cRAG 三级评估的默认阈值（运行时可被后台 system_settings 覆盖）
DEFAULT_CRAG_HIGH = 0.6
DEFAULT_CRAG_LOW = 0.3

# reranker（cross-encoder 精排）三档默认阈值（sigmoid 后的 0~1 分数）
# ★ 用 bge-reranker-v2-m3 实测标定（scripts/calibrate_gate.py，318 可答 + 24 越界）：
#     可答题 best 分 min 0.555 / p5 0.622 / p25 0.711 / 中位 0.724
#     越界题 best 分 min 0.500 / 中位 0.522 / max 0.719
#   low = 0.555 取**可答题的最低分** → 误判空 0/318，同时把「话题完全不相干」的越界题判空。
#   ⚠️ 这两个数**只对 v2-m3 成立**。旧 reranker-base 的分布完全不同（且它装不下 800 字块），
#      换模型必须重跑 calibrate_gate.py。旧默认 0.3 在 v2-m3 下**永远不会触发**
#      （比所有分数都低）—— 那等于没有门控。
DEFAULT_RERANK_HIGH = 0.72
# ★ 2026-10-02 重标：0.555 → **0.54**。
#   0.555 是只在 **318 道正式措辞**上标的，而实测**口语化提问**会把分数整体压低
#   （「本科课堂包括哪两类？」0.703 → 「咱们本科的课堂，一共分成哪两类呀？」0.533）。
#   132 题普查（108 口语可答 + 24 越界）的完整权衡曲线：
#       t=0.5314  误伤可答 0/108   漏放越界 9/24
#       t=0.5400  误伤可答 1/108   漏放越界 6/24
#       t=0.5500  误伤可答 2/108   漏放越界 4/24
#       t=0.5550  误伤可答 3/108   漏放越界 4/24   ← 原值
#   取 0.54 = **明显降低误判空**（3→1，这是用户可见的缺陷：库里明明有却说没查到），
#   代价是漏放 4→6；而漏放的那几道多数由提示词的「越界请求 / 无信息兜底」规则兜住，
#   真正危险的「点名不存在的文件」另有确定性判据（见 `coverage.py`，零误伤）。
#   ⚠️ 真要再调，**必须重跑 `_work/_archive/v4/gate_survey.py` 那套普查**，别凭单题手感挪。
DEFAULT_RERANK_LOW = 0.54

# Current single-sigmoid scale. This removes independent low-score seeds only;
# query-level decisions and explicitly linked context use their existing rules.
MIN_CANDIDATE_RERANK_SCORE = 0.05

_reranker = None  # CrossEncoder | None


def _encode_query(query: str) -> list[float]:
    """对查询文本做向量化（前缀取 config.query_prefix，见上方说明）。"""
    encoder = _get_encoder()
    return encoder.encode(settings.query_prefix + query, normalize_embeddings=True).tolist()


def _reranker_max_length(model_name: str, want: int = 8192) -> int:
    """把 max_length 夹到模型的真实位置编码上限。

    ★ 原来硬编码 `max_length=1024`。bge-reranker-base 的 `max_position_embeddings`
      只有 **514**：小块还是 250 字时碰不到，换成 400~800 的块立刻越界。而越界的后果
      是**静默**的、且分两种：
        · CPU：抛 IndexError → 被 _rerank 调用处的 `except Exception` 吞掉
          → 悄悄退回 embedding 分数判档，指标变差但**不报错**；
        · CUDA：`device-side assert triggered`，直接把 CUDA context 弄坏，
          **之后所有 encode 都失败**（实测 rerank 评测就是这么整篇挂掉的）。
      所以这里读一次模型 config，把上限夹准。
    """
    try:
        import json
        import os

        path = os.path.join(model_name, "config.json")
        if os.path.isfile(path):
            conf = json.load(open(path, encoding="utf-8"))
        else:
            from huggingface_hub import hf_hub_download
            conf = json.load(open(hf_hub_download(model_name, "config.json"),
                                  encoding="utf-8"))
        n = conf.get("max_position_embeddings")
        if n:
            # 减 2 给 special token 留位（XLMRoberta 的 position id 从 padding_idx 起算）
            return max(128, min(int(n) - 2, want))
    except Exception:
        pass
    return want


def _get_reranker():
    """懒加载 cross-encoder 精排模型（sentence-transformers 已含，无新增依赖）。

    ★ 2026-10-02：加 fp16 装载开关。24G 卡上 vLLM 占 0.85（≈21.5G）、只剩 ~2.6G，
      而 v2-m3 的 fp32 权重就有 2.27G，加上 forward 激活必然 OOM —— 这就是精排
      一直被迫留在 CPU 上的原因（**每次检索 32 秒**）。fp16 把权重压到 1.13G。
    ⚠️ 分数与 fp32 **逐位相同**（实测 6 条跨区间样本 Δ=0.0000），阈值不用重标。
    """
    global _reranker
    if _reranker is None:
        import torch
        torch.set_num_threads(settings.inference_threads)
        from sentence_transformers import CrossEncoder
        kwargs = {"max_length": _reranker_max_length(settings.rerank_model)}
        if settings.rerank_device != "auto":
            kwargs["device"] = settings.rerank_device
        if settings.rerank_fp16:
            kwargs["model_kwargs"] = {"torch_dtype": "float16"}
        _reranker = CrossEncoder(settings.rerank_model, **kwargs)
    return _reranker


def _rerank_doc_text(c: dict) -> str:
    """rerank 的**文档侧**输入：《标题》+ 章节归属 + 块原文。

    ★ 不能只喂块原文。65 份培养方案是同一套模板生成的，同学分要求的块**逐字几乎相同**
      （「毕业总学分不低于170学分。其中:通识必修课程46学分,通识选修课程5学分…」
      在多份方案里一字不差），只给原文时 reranker 分不出「这块来自哪个文档」，
      只能照单接收 RRF 带偏的顺序——实测 ext 集 5 道表格题全被
      《2025级智慧交通专业培养方案》抢走（期望是《交通工程专业培养方案》）。
      带上标题与章节归属，reranker 才有「这份文档是不是被问的那份」这个判据。

    ⚠️ heading_path 常为空（表格块基本没有），此时只剩标题——仍然够用，
      因为「来自哪份文档」这个信息全在标题里。
    ⚠️ 与 `vector._index_document` 的向量化输入保持同一形状（`《标题》\\n路径\\n原文`），
      这样 rerank 与检索看到的是同一种「文档身份」表达。
    """
    src = c.get("source") or ""
    text = c.get("snippet") or ""
    if not settings.rerank_include_title:
        return text                       # A/B 用的旧形状：只喂原文
    hp = " / ".join(c.get("heading_path") or [])
    head = f"《{src}》"
    if hp:
        head += f"\n{hp}"
    return f"{head}\n{text}"


def _rerank(query: str, chunks: list[dict]) -> None:
    """用 cross-encoder 对「query + 文档侧文本（标题+章节+原文）」精排，
    写回 chunk["rerank_score"]（sigmoid 0~1）。"""
    if not chunks:
        return
    model = _get_reranker()
    pairs = [[query, _rerank_doc_text(c)] for c in chunks]
    for pair in pairs:
        tokens = model.tokenizer(*pair, truncation=False)["input_ids"]
        if len(tokens) > model.max_length:
            raise ValueError(f"完整结构块超过 reranker 上限 {model.max_length} tokens，禁止静默截断")
    for c, (logit, score) in zip(chunks, predict_scores(model, pairs)):
        c.update(rerank_logit=logit, rerank_score=score, rerank_score_version=SCORE_VERSION)


def _chunk_from_point(p, score=None) -> dict:
    """Qdrant 命中点 → 上下文候选。"""
    pl = p.payload or {}
    return {
        "document_id": pl.get("document_id"),
        "chunk_idx": pl.get("chunk_idx"),
        "source": pl.get("source", ""),
        "snippet": pl.get("text", ""),
        "text": pl.get("text", ""),
        "heading_path": pl.get("heading_path") or [],
        "block_type": pl.get("block_type") or "",
        "score": round(score, 4) if score is not None else None,
        **{k: pl[k] for k in ("chunk_id", "chunking_version", "source_sha256", "source_span",
                             "parent_id", "section_parent_id", "previous_id", "next_id",
                             "row_number", "table_fields", "review_flags") if k in pl},
    }


def _related_evidence(seed: dict, selector: dict) -> list[dict]:
    """Read at most two matching payloads to detect duplicate relation identities."""
    values = {key: seed[key] for key in
              ("document_id", "source_sha256", "chunking_version")}
    if "row_number" in selector:
        values["parent_id"] = seed["parent_id"]
    values.update(selector)
    records, offset = _get_client().scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=models.Filter(must=[models.FieldCondition(
            key=key, match=models.MatchValue(value=value)) for key, value in values.items()]),
        limit=2, with_payload=True, with_vectors=False)
    if offset is not None:
        raise ValueError("关系身份存在多条记录，不能静默选择")
    return [_chunk_from_point(point) for point in records]


def _document_evidence(seed: dict) -> list[dict]:
    from types import SimpleNamespace
    from . import indexing
    return [_chunk_from_point(SimpleNamespace(payload=row))
            for row in indexing._get_doc_records(seed['document_id'])]


def _table_evidence(seed: dict) -> list[dict]:
    must = [models.FieldCondition(key=key, match=models.MatchValue(value=seed[key]))
            for key in ("document_id", "source_sha256", "chunking_version", "parent_id")]
    must.append(models.FieldCondition(key="block_type", match=models.MatchValue(value="table")))
    records, offset = _get_client().scroll(settings.qdrant_collection,
        scroll_filter=models.Filter(must=must), limit=65, with_payload=True, with_vectors=False)
    if offset is not None or len(records) > 64:
        raise ValueError("目标表格读取超过64行上限")
    return [_chunk_from_point(point) for point in records]


def _shingles(text: str, n: int = 5, step: int = 3) -> set:
    """字符 n-gram 集合（步长 3，省一半计算量）。用于近似判重，不用于任何检索打分。"""
    return {text[i:i + n] for i in range(0, max(0, len(text) - n), step)}


def _dedupe_near(chunks: list[dict], threshold: float) -> list[dict]:
    """培养方案之间做**近似**折叠；规章制度和同文档块一律保留。

    ★ 为什么需要（2026-10-03 实测）：`_dedupe_by_text` 只认**逐字相等**，
      而 65 份培养方案是同一套模板生成的 —— 同一段学分说明在各方案里只差几个
      专业名和数字，**逐字不相等**，于是每一份都各占一个候选位。实测：

        · 全库 3393 个向量里 **3116（92%）来自培养方案**（65 份），规章制度只剩 277；
        · 318 题集当天 5 道未命中里的 **4 道**，top-6 **全部**是各专业培养方案的
          同一段模板文字，彼此相似度 > 0.8；
        · 正确的规章制度文档在召回池里的名次是 **93 / 73 / 33 / 15** ——
          全部在精排窗口（40）之外。

      ⚠️ 所以这是**召回深度**问题，不是排序问题：精排救不了没被召回的文档。
      把每路召回 20 → 120 再按 0.6 折叠后，期望文档名次降到 **85 / 31 / 8 / 32**
      （前三条落进精排池，原为 0 条）。

    ⚠️ 折叠**保留每条近重组的第一个**（RRF 名次最高者）。这对"同一套模板"是对的，
      但若某一组的**正确答案**恰好排在组内靠后，它会连同组一起被丢掉 ——
      所以这项改动**必须**在同语料上 A/B 看 R@1，不能凭"看着合理"就上。
      （这正是它默认关闭、只用环境变量 NEAR_DUP_THRESHOLD 打开的原因。）
    """
    if not threshold or threshold <= 0:
        return chunks
    def _is_major_plan(c: dict) -> bool:
        title = (c.get("source") or "").strip()
        return (len(title) > 5 and title[:4].isdigit() and title[4] == "级"
                and "培养方案" in title)

    out: list[dict] = []
    seen: list[tuple] = []          # (shingle 集合, 集合大小, document_id)
    for c in chunks:
        # 近重复折叠的原始目标只有批量模板化培养方案。不同规章即使正文高度相似，
        # 适用系列、数字或条件也可能不同，不能互相替代；同一方案内的相似章节同理。
        if not _is_major_plan(c):
            out.append(c)
            continue
        sh = _shingles(c.get("text") or "")
        if not sh:
            out.append(c)
            continue
        n = len(sh)
        doc_id = c.get("document_id")
        # ★ 长度预筛：近似块的 shingle 数必然接近，先跳过长度差 >30% 的对手
        #   （集合交集是这里唯一的热点，实测 240 条候选全比一遍要 ~1.5s/查询）。
        if not any(other_doc != doc_id and abs(n - m) <= max(4, 0.3 * n)
                   and len(sh & p) / len(sh | p) >= threshold
                   for p, m, other_doc in seen):
            out.append(c)
            seen.append((sh, n, doc_id))
    return sorted(out, key=lambda c: c.get("rrf") or 0, reverse=True)


def _ckey(c: dict) -> tuple:
    return (c.get("document_id"), c.get("chunk_idx"))


def _rrf_fuse(lists: list[list[dict]]) -> list[dict]:
    """RRF 融合多路排序：score = Σ 1/(k + rank)，按融合分降序返回。

    ★ 为什么**不用** Qdrant 的 `Fusion.RRF`：那样只会拿回一个融合分，**dense 余弦分就丢了**。
      而判档（低档判空）需要「库里到底有没有相关内容」这个信号，它只能由余弦分给——
      RRF 分是「名次倒数之和」（量级 0.0x），拿它去比 crag 阈值会把**所有**题判空。
      自己融合既保住了余弦分，也让融合过程和 k 值可读可调。

    两路的并集里，先出现的那一路的版本胜出——dense 放第一路，于是并集候选都带余弦分；
    sparse 独有的候选 `score=None`（稀疏点积与余弦不同量纲，混用会误判档）。
    """
    ranks: dict = {}
    for lst in lists:
        for r, c in enumerate(lst, 1):
            ranks[_ckey(c)] = ranks.get(_ckey(c), 0.0) + 1.0 / (settings.rrf_k + r)
    by_key: dict = {}
    for lst in lists:
        for c in lst:
            by_key.setdefault(_ckey(c), c)
    out = sorted(by_key.values(), key=lambda c: ranks[_ckey(c)], reverse=True)
    for c in out:
        c["rrf"] = round(ranks[_ckey(c)], 6)
    return out


class _RetrievedChunks(list):
    """召回列表，同时携带本轮辅助路由聚焦到的文档。

    仍是 `list`，所以既有调用方和测试无需改接口；附加属性只在本次 `retrieve()`
    内消费，避免为了拿 focus 再执行一遍辅助查询。
    """

    focus_document_id: int | None = None


def _route_title_key(value: str) -> str:
    """把人核路由模板和文档标题归一到可比较的制度名。"""
    value = unicodedata.normalize("NFKC", value or "")
    match = re.search(r"《([^》]+)》", value)
    if match:
        value = match.group(1)
    value = re.sub(r"[（(](?:试行|\d{4}年修订)[）)]", "", value)
    value = value.replace("桂林电子科技大学", "")
    return re.sub(r"\s+", "", value)


def _with_aux_focus(rows: list[dict], aux: list[dict]) -> _RetrievedChunks:
    out = _RetrievedChunks(rows)
    if not aux:
        return out
    # 路由模板是人核资产，模板明确写出制度全名时，标题匹配比“top-N 多数”更强：
    # 同系列制度正文高度相似，aux 前几名可能分别来自教师/自然/社会/实验系列，
    # 多数投票会失效；但匹配制度名仍能给出可审计的唯一 focus。
    for item in aux:
        title = _route_title_key(item.get("source") or "")
        aux_query = _route_title_key(item.get("aux_query") or "")
        if len(title) >= 8 and title in aux_query:
            out.focus_document_id = item.get("document_id")
            return out
    from collections import Counter
    counts = Counter(x.get("document_id") for x in aux[:settings.aux_focus_topn]
                     if x.get("document_id") is not None)
    if counts:
        did, count = counts.most_common(1)[0]
        if count >= settings.aux_focus_need:
            out.focus_document_id = did
    return out


def _aux_recall(query: str, trace: RetrievalTrace | None = None) -> list[dict]:
    """方案 4：按意图路由**追加一路规范化查询**的召回结果（≤ `aux_recall_n`）。

    ★ 返回的块作为**单一 aux 票仓**进 `_rrf_fuse` —— 票仓固定 dense/sparse/aux **三个**，
      与命中族的数量**解耦**：同族多条模板在这里先合并去重，不让族数改变融合权重。
    ★ 辅助查询用**语料自己的措辞**（制度全名 + 条内专名），这正是口语化原查询缺的。

    失败开放（**每一层都失败开放**）：开关关 / 未命中意图 / 路由表读不到 /
    编码或检索异常 → 返回 `[]` → 调用方走纯主路，与旧行为逐字相同。
    """
    started = time.perf_counter()
    try:
        if not settings.aux_route:
            if trace is not None:
                trace.record_candidates("aux", [])
            return []
        from . import intent
        d = intent.detect(query)
        qs = d.get("aux_queries") or []
        if not qs:
            if trace is not None:
                trace.record_candidates("aux", [])
            return []
        # 留痕（审计用）：原题 / 命中词 / 触发族 / 被上限丢弃的族
        logger.info("aux_route: topics=%s dropped=%s hits=%s q=%.40s",
                    d["topics"], d["dropped"], d["hits"], query)
        n = settings.aux_recall_n
        for t in d["topics"]:                    # 按主题可覆盖
            n = int((settings.aux_recall_n_by_topic or {}).get(t, n))
        client = _get_client()
        out, seen = [], set()
        for aq in qs:
            for p in client.query_points(collection_name=settings.qdrant_collection,
                                         query=_encode_query(aq), using="dense",
                                         limit=n, with_payload=True).points:
                c = _chunk_from_point(p, p.score)
                k = (c.get("document_id"), c.get("chunk_idx"))
                if k in seen:                    # 路内合并去重（同族多模板会重叠）
                    continue
                seen.add(k)
                c["aux"] = True
                c["aux_query"] = aq
                out.append(c)
        result = out[:n]
        if trace is not None:
            trace.record_candidates("aux", result)
        return result
    except Exception as exc:
        if trace is not None:
            trace.record_candidates("aux", [])
            trace.record_degradation("aux", "fallback", str(exc))
        return []
    finally:
        if trace is not None:
            trace.record_timing("aux", (time.perf_counter() - started) * 1000)


def aux_route_focus(query: str) -> int | None:
    """**多信号放行**用的运行时信号：aux 路这一跳是否**聚焦到同一份文档**。

    ★ 为什么不是「正解名次」：运行时**看不到哪块是正解**。能看见的是 ——
      规范化的制度查询打出去之后，返回的块**是否收敛在同一份制度上**
      （同一 document_id 在 aux 路 top-N 里出现 ≥ `aux_focus_need` 次）。
      这正是「这条路认得我们要的那份文件」的可观测代理。
    ★ 三层都要满足，缺一不放行：**开关 on ∧ 强专名命中（topics 非空）∧ aux 聚焦**。
    失败开放：任何一层不满足/异常 → None（不放行，回到原判空）。
    """
    try:
        if not (settings.gate_multi_signal and settings.aux_route):
            return None
        from . import intent
        d = intent.detect(query)
        if not d.get("topics"):
            return None
        aux = _aux_recall(query)
        if not aux:
            return None
        from collections import Counter
        c = Counter(x.get("document_id") for x in aux[:settings.aux_focus_topn])
        if not c:
            return None
        did, cnt = c.most_common(1)[0]
        return did if cnt >= settings.aux_focus_need else None
    except Exception:
        return None


def _document_filter(document_id):
    """Minimal Qdrant payload filter for one real document_id."""
    try:
        wanted = int(document_id)
    except (TypeError, ValueError):
        return None
    return models.Filter(must=[
        models.FieldCondition(key="document_id",
                              match=models.MatchValue(value=wanted))
    ])


def _only_document(chunks: list[dict], document_id) -> list[dict]:
    """Fail-closed post-filter: keep only chunks from the scoped document."""
    wanted = str(document_id)
    return [chunk for chunk in (chunks or [])
            if str(chunk.get("document_id")) == wanted]


def _scope_result(rows, document_id):
    if document_id is None:
        return rows
    out = _RetrievedChunks(_only_document(list(rows or []), document_id))
    out.focus_document_id = int(document_id)
    return out


def _scoped_decision(signals, policy):
    """The normal gate plus the scoped completeness guard.

    A correct document title is not evidence by itself: if the gate would
    expose evidence without a supported claim anchor, scoped recovery closes.
    """
    decision = decide(signals, policy)
    if (decision.status in ("supported", "uncertain")
            and not claim_anchor_supported(signals, policy)):
        return GateDecision("irrelevant", "scoped_claim_not_supported",
                            decision.policy_version,
                            decision.contributing_signals, False, False)
    return decision


def _retrieve_chunks(query: str, limit: int,
                     trace: RetrievalTrace | None = None, *,
                     sparse_query: str | None = None,
                     document_id=None) -> list[dict]:
    """宽召回：dense（语义）+ sparse（词权重）双路，RRF 融合。

    sparse 路抓的是**精确词面**：「桂电教〔2025〕28号」这类文号、「博士后」这类专有名词，
    单路 dense 对这些是弱项。另一个副作用是压培养方案泛滥——65 份同模板培养方案没有
    对应词面，宽泛查询里它们在 sparse 路的名次自然靠后，融合后整体位次被拉下来。

    每项 {source, document_id, chunk_idx, snippet, text, heading_path, score, rrf}。
    块本身就是生成单元（结构感知递归切片，400~800 字），不再有父块；
    snippet 与 text 是同一份原文（保留 snippet 是为了不破坏 rag_test.py 与后台
    「检索调试」页的字段约定）。
    """
    client = _get_client()
    started = time.perf_counter()
    doc_filter = _document_filter(document_id) if document_id is not None else None
    if document_id is not None and doc_filter is None:
        return _RetrievedChunks()
    density = client.query_points(collection_name=settings.qdrant_collection,
                                  query=_encode_query(query), using="dense",
                                  query_filter=doc_filter,
                                  limit=limit, with_payload=True)
    dense = [_chunk_from_point(p, p.score) for p in density.points]
    if trace is not None:
        trace.record_candidates("dense", dense)
        trace.record_timing("dense", (time.perf_counter() - started) * 1000)
    aux = []
    if not settings.use_sparse:
        if trace is not None:
            trace.record_candidates("bm25", [])
            trace.record_candidates("fused", dense)
        return _scope_result(_with_aux_focus(dense, aux), document_id)
    aux = [] if document_id is not None else _aux_recall(query, trace)
    sparse_started = time.perf_counter()
    try:
        from . import bm25
        vocab = bm25.load_vocab()
        if vocab is None:
            if trace is not None:
                trace.record_candidates("bm25", [])
                trace.record_degradation("bm25", "vocab_missing")
                trace.record_candidates("fused", dense)
            return _scope_result(dense, document_id)
        idx, vals = bm25.query_vector(sparse_query or query, vocab)
        if not idx:
            if trace is not None:
                trace.record_candidates("bm25", [])
                trace.record_degradation("bm25", "query_out_of_vocabulary")
                trace.record_candidates("fused", dense)
            return _scope_result(_with_aux_focus(dense, aux), document_id)
        sparse = [_chunk_from_point(p, None)      # 稀疏点积与余弦不同量纲 → 不写进 score
                  for p in client.query_points(
                      collection_name=settings.qdrant_collection,
                      query=models.SparseVector(indices=idx, values=vals),
                      query_filter=doc_filter,
                      using="sparse", limit=limit, with_payload=True).points]
        if trace is not None:
            trace.record_candidates("bm25", sparse)
    except Exception as exc:
        # sparse 不可用（词表缺失 / collection 没有 sparse 命名向量）不能让检索整个挂掉，
        # 退回单路 dense —— 与 reranker 不可用时回退到 embedding 判档是同一个思路。
        # ⚠️ 这里也要**带上 aux**，否则方案 4 会被 sparse 的故障静默吃掉。
        fused = _rrf_fuse([dense] + ([aux] if aux else [])) if aux else dense
        if trace is not None:
            trace.record_candidates("bm25", [])
            trace.record_degradation("bm25", "fallback", str(exc))
            trace.record_candidates("fused", fused)
        return _scope_result(_with_aux_focus(fused, aux), document_id)
    finally:
        if trace is not None:
            trace.record_timing("bm25", (time.perf_counter() - sparse_started) * 1000)
    # ★ 方案 1：sparse 质量门（默认关）。判据与实测依据见 config.sparse_fuse_gate。
    #   ⚠️ **只在两条都满足时**才退回单路：sparse 条数薄 **且** dense 有把握。
    #      任一读不到/异常 → 落到下面的 `_rrf_fuse`，即**失败开放**（保持旧行为）。
    #   ⚠️ 已知代价：**sparse 是唯一含正解的那一路**时，跳过它就丢了正解
    #      （A/B 里专门统计 `false skip`，见 `_work/_archive/v4/false_skip_scan.py`）。
    if settings.sparse_fuse_gate and len(sparse) < settings.sparse_min_n:
        _bd = max((c.get("score") or 0) for c in dense) if dense else 0.0
        if _bd >= settings.dense_conf:
            fused = _rrf_fuse([dense] + ([aux] if aux else [])) if aux else dense
            if trace is not None:
                trace.record_degradation("fusion", "sparse_quality_gate")
                trace.record_candidates("fused", fused)
            return _scope_result(_with_aux_focus(fused, aux), document_id)
    # ★ 方案 4：命中意图时 aux 作为**第三个票仓**进融合（族数与权重解耦）。
    fused = _rrf_fuse([dense, sparse] + ([aux] if aux else []))
    if trace is not None:
        trace.record_candidates("fused", fused)
    return _scope_result(_with_aux_focus(fused, aux), document_id)


def _dedupe_by_text(chunks: list[dict]) -> list[dict]:
    """同一段文本只保留分数最高的一条。

    培养方案这类「同模板批量转换」的文档，开头块逐字相同，会算出完全一样的相似度
    并排挤掉真正相关的内容（实测 5 个上下文槽位被 3 个同分无关块占满）。
    这里按小块原文去重，再进父块聚合。
    """
    best: dict = {}
    for c in chunks:
        key = (c.get("snippet") or "").strip()
        if not key:
            continue
        cur = best.get(key)
        if cur is None or (c.get("score") or 0) > (cur.get("score") or 0):
            best[key] = c
    return list(best.values())


def _cap_per_doc(ranked: list[dict], top_k: int) -> list[dict]:
    """取前 top_k，但同一份文档最多占 settings.max_chunks_per_doc 个槽位。

    ★ 实测「重复占用槽位 53.4%」——一份文档的多个相邻块挤满 top-k，模型拿到的是同一段
      内容的几个副本，其余文档全被挤掉。限额让 5 个槽位至少覆盖 3 份文档。

    ⚠️ 它治不了**跨文档**泛滥（65 份同模板培养方案各自贡献一个块，仍然能占满窗口）——
      那是 rerank 升级与稀疏召回（专有名词/文号精确匹配）要解决的问题。
      这里只治「同一份文档重复占位」。

    取不满 top_k 时不回填：1 段好内容胜过 5 份近似副本（且 MAX_CONTEXT_CHARS 预算
    有限，回填等于用冗余挤掉别的内容）。代价是单文档问题的上下文变少，可由
    eval_retrieval 的 Recall@k 与「整窗只有 1 份文档的题」两个指标一起看。
    """
    cap = settings.max_chunks_per_doc
    if not cap or cap <= 0:
        return ranked[:top_k]
    out: list[dict] = []
    seen: dict = {}
    for c in ranked:
        d = c.get("document_id")
        if seen.get(d, 0) >= cap:
            continue
        seen[d] = seen.get(d, 0) + 1
        out.append(c)
        if len(out) >= top_k:
            break
    return out


def _expand_flow(chunks: list[dict]) -> list[dict]:
    """阶段 2a：命中流程章节的块，按文档补齐同节相邻块（判据见 flows.py，单一来源）。

    ★ **接在 `_cap_per_doc` 之后**，不是之前（与方案原话不同，理由如下）：
      `flows.expand_flow_sections` 补进来的块**没有 `rerank_score`**（它们没进精排），
      按分数排序必然落在队尾。若接在 `_cap_per_doc` **之前**，`_cap_per_doc` 会先按
      `max_chunks_per_doc=2` 把同文档的兄弟块剔掉、再按 top_k 截断 —— 补进来的块
      **一块都进不了上下文**（ranked 40 条里补 6 条，top_k=5 恒取不到）。
      而 2a 的**存在理由**恰恰是绕过那个「2 块/文档」的限额（见 flows.py 模块头：
      「第十六条 转专业工作流程」只带出 (一)(二) 两步，看不到 (六) 公示与报批）。
      触发条件不变：**只有命中的流程块本身进了 top-k** 才扩（补进来的块不触发再扩）。

    失败开放：`flows` 导入失败 / 取文档块失败 / 开关关，一律原样返回。
    """
    if not settings.flow_expand or not chunks:
        return chunks
    try:
        from . import flow_expand
        return flow_expand.expand_flow_sections(chunks)
    except Exception:
        return chunks


def _follow_refs(chunks: list[dict]) -> list[dict]:
    """阶段 2b：上下文里的「按《X》执行」若命中**白名单**，把《X》的块补进来。

    只查白名单，**不跑正则猜**（正则只是离线生成器，见 refs.py 模块头：人核过才算数）。
    """
    if not settings.ref_follow or not chunks:
        return chunks
    try:
        from . import reference_follow
        return reference_follow.follow_refs(chunks)
    except Exception:
        return chunks


def _missing_files(chunks: list[dict]) -> list[str]:
    """阶段 2b：上下文引用了**语料里没有**的文件 → 交给提示词明说「这份没有收录」。

    与门控判空同一套索引、同一套判据（`coverage.load_corpus_index`），免得
    「提问里点名的文件」与「资料里引用的文件」一个拦住一个放行。
    """
    if not settings.ref_follow or not chunks:
        return []
    try:
        from . import reference_follow
        return reference_follow.missing_refs(chunks)
    except Exception:
        return []


def _gate_band(query: str, best: float, low: float) -> bool:
    """方案 6：**流程词 ∧ 第一人称 ∧ 业务实体** 三合一命中，且 `best ∈ [low − band, low)`
    → 提为 medium。

    ★ **三合一是用户人核后收紧的判据**（2026-10-08）：只命中流程词不够
      （「转专业要走什么流程？」是纯条文问句，不该软化）；只命中场景也不够。
      三项同时命中才说明「用户在自述情况 **并且** 在问怎么办」——
      那正是门控最容易误判空、而库里确实有资料的那一类。
    ★ **绝不降全局阈值**：只在这一段窄带里动手 —— 带外零影响是它敢用的前提
      （降全局阈值会把越界题一起放进来，交接文档明令禁止）。
    失败开放：开关关 / 意图模块读不到 / 任何异常 → 不软化（保持旧行为）。
    ★ 提级**全留痕**（`gate_band: 提为 medium` 日志），A/B 时据此统计 false 提级。
    """
    try:
        if not settings.gate_scenario_band:
            return False
        if not (low - settings.gate_band_width <= best < low):
            return False
        from . import intent
        d = intent.detect(query)
        mode = (settings.gate_band_mode or "flow_and_scenario").strip()
        if mode == "scenario":
            ok = bool(d["scenario"])
        elif mode == "flow_or_scenario":
            ok = bool(d["flow"]) or bool(d["scenario"])
        else:                                              # 默认：三合一
            ok = bool(d["flow"]) and bool(d["scenario"])
        if ok:
            logger.info("gate_band[%s]: 提为 medium best=%.4f low=%.2f q=%.40s",
                        mode, best, low, query)
        return ok
    except Exception:
        return False


def _cap_per_doc_focus(ranked: list[dict], top_k: int,
                       focus_did, focus_cap: int) -> list[dict]:
    """`_cap_per_doc` 的**聚焦文档局部放宽**版（多信号放行专用）。

    ★ 为什么需要（2026-10-08 实测）：33 道判空题里 A28/A29 被放行了、**但正解不在窗** ——
      聚焦文档的块被 `max_chunks_per_doc=2` 挤掉。聚焦文档才是我们**确认要对的那一份**，
      在放行这一条路径上多给它 1~2 个槽位是合理的。
    ★ **带外零影响**：只有 `multi_signal` 放行分支调用本函数；其余路径仍走 `_cap_per_doc`，
      全局 `max_chunks_per_doc` **一个字节都不动**（318 基线因此逐位可复现）。
    ★ `focus_did=None` 或 `focus_cap<=0` → 退化成 `_cap_per_doc`（失败开放）。
    """
    cap = settings.max_chunks_per_doc
    if not focus_cap or focus_cap <= 0 or focus_did is None:
        return _cap_per_doc(ranked, top_k)
    if not cap or cap <= 0:
        return ranked[:top_k]
    # 只比全局 cap 多保留一块。重点是**预留**而非仅提高上限：如果聚焦文档的
    # 第三块排在全局第 6，普通遍历会在它出现前就填满 top_k，放宽上限仍然无效。
    reserve = min(top_k, max(cap + 1, cap), max(cap, focus_cap))
    focused = [c for c in ranked if c.get("document_id") == focus_did][:reserve]
    if not focused:
        return _cap_per_doc(ranked, top_k)

    chosen = {_ckey(c) for c in focused}
    others: list[dict] = []
    seen: dict = {}
    for c in ranked:
        if _ckey(c) in chosen or c.get("document_id") == focus_did:
            continue
        d = c.get("document_id")
        if seen.get(d, 0) >= cap:
            continue
        seen[d] = seen.get(d, 0) + 1
        others.append(c)
        if len(others) >= top_k - len(focused):
            break
    selected = focused + others
    order = {_ckey(c): i for i, c in enumerate(ranked)}
    return sorted(selected[:top_k], key=lambda c: order[_ckey(c)])


# ★★ 覆盖面判空（2026-10-02）：门控的**第二条腿**，与「相关度阈值」性质完全不同。
#   相关度分衡量"这块跟问题相不相关"，而判空想知道的是"库里有没有能回答的内容" ——
#   实测两者**不可互换**（6 个相关度信号的分布全部重叠：可答题最低 0.5325，
#   越界题最高 0.6984）。但有一类越界**可以确定性判定**：用户点名了一份具体文档或文号，
#   而它**根本不在语料里**（实测：`桂电教〔2099〕1号`、`《…宿舍空调使用管理规定》`）——
#   这时候任何回答都是编造。
#   实测代价：越界题拦住 2/24，**可答题误伤 0/372**（零误伤是它敢用的唯一理由）。
#   ⚠️ 索引建不起来时**失败开放**（不拦）：判空的代价是用户拿不到答案，
#      宁可漏拦，不可误拦。
_coverage_index = None
_gate_anchor_index = None


def _gate_anchors():
    """Cached reviewed anchor index (corpus titles/docnos + regulation entities).

    Failure-open: an unavailable index disables anchor/conflict signals rather
    than rejecting every query.
    """
    global _gate_anchor_index, _coverage_index
    if _gate_anchor_index is not None:
        return _gate_anchor_index
    try:
        from . import coverage
        if _coverage_index is None:
            _coverage_index = coverage.load_corpus_index()
        entities = []
        catalog = Path(__file__).resolve().parents[4] / "data" / "entity_catalog.json"
        if catalog.is_file():
            data = json.loads(catalog.read_text(encoding="utf-8"))
            groups = (data.get("entities") or {})
            for entity_type, rows in groups.items():
                field = "canonical_title" if entity_type == "regulation" \
                    else "canonical_name"
                entities.extend(item.get(field) for item in rows or [] if item.get(field))
        _gate_anchor_index = gate_signals.build_anchor_index(
            sources=_coverage_index.get("raw") or (),
            docnos=_coverage_index.get("docnos") or (),
            entities=entities)
    except Exception:
        _gate_anchor_index = gate_signals.AnchorIndex()
    return _gate_anchor_index


def _bm25_available(trace: RetrievalTrace | None) -> bool:
    if not settings.use_sparse:
        return False
    degraded = any(item.get("stage") == "bm25"
                   and item.get("code") in ("vocab_missing", "fallback")
                   for item in (trace.degradations if trace is not None else ()))
    return not degraded


def _absent_reference(query: str) -> str:
    global _coverage_index
    try:
        from . import coverage
        if _coverage_index is None:
            _coverage_index = coverage.load_corpus_index()
        return coverage.absent_reference(query, _coverage_index)
    except Exception:
        return ""


def retrieve(query: str, top_k: int | None = None, *,
             prev_query: str | None = None,
             keywords: list[str] | None = None,
             crag_high: float = DEFAULT_CRAG_HIGH,
             crag_low: float = DEFAULT_CRAG_LOW,
             use_llm_grader: bool = False, use_reranker: bool = True,
             rerank_high: float = DEFAULT_RERANK_HIGH,
             rerank_low: float = DEFAULT_RERANK_LOW,
             extra_query: str | None = None,
             trace: RetrievalTrace | None = None,
             policy: GatePolicy = POLICY_V1,
             document_id=None,
             recovery_round: int = 0, request_task=None) -> dict:
    """结构感知递归切片 + 三档判档，可选 reranker 精排。

    宽度召回 → 文本去重 → （可选）精排 → 按分数三级评估 → 每文档限额取 top_k：
    - 高相关（最佳分 >= high）：返回 top_k 个块；
    - 低相关（最佳分 < low）：空上下文（模型回复"暂未收录"）；
    - 中相关：返回上下文交给模型自行判断，或以 LLM 评估器（use_llm_grader）裁决。

    输入为完整结构证据：正文条款或字段化表格行，来源父级由元数据溯源。
    当前仍有每文档限额（settings.max_chunks_per_doc）；不会自动把整张大表喂给模型。

    返回 {contexts: [{source, snippet, text, heading_path, score}], tier, reason, missing_files}。
    `missing_files`（阶段 2b）是**上下文里被引用、但语料中不存在**的文件名，
    由调用方透传给 `llm_client.build_messages`，让模型明说「这份没有收录」而不是照着
    文件名编内容（见 refs.missing_refs）。
    """
    retrieval_started = time.perf_counter()
    check_cancelled()
    from documents.document_availability import require_ready
    source_revision = require_ready()
    target_query = query
    focus_document_id = None
    table_selection = None
    target_record = None

    def _tier_of(decision) -> str:
        return {"supported": "high", "uncertain": "medium"}.get(decision.status, "low")

    def _gate(ranked_rows: list[dict], selected_rows: list[dict],
              best_score: float | None, absent_text: str = ""):
        """Collect runtime signals and run the pure evidence gate."""
        signals = gate_signals.collect(
            query=query,
            ranked=ranked_rows,
            selected=selected_rows,
            dense_rows=(trace.stages.get("dense") if trace is not None else None),
            bm25_rows=(trace.stages.get("bm25") if trace is not None else None),
            bm25_available=_bm25_available(trace),
            focus_document_id=focus_document_id,
            degradations=(trace.degradations if trace is not None else ()),
            anchors=_gate_anchors(),
            absent_reference=absent_text,
            keywords=plan.keywords,
        )
        return signals, (_scoped_decision(signals, policy) if scoped
                         else decide(signals, policy))

    def _out(contexts: list[dict], tier: str, reason: str,
             best_score: float | None = None, *, decision=None, signals=None) -> dict:
        """统一出口：**`missing_files` 一处计算**，免得 6 个 return 漏掉一个
        （漏掉的那条路径上「缺失提示」静默消失，且只在特定档位才复现）。"""
        nonlocal target_record
        require_ready(source_revision)
        if contexts and (decision is None or decision.evidence_exposable):
            contexts, target_record = select_target_record(target_query, eligible_ranked, contexts,
                top_k=top_k, max_chars=settings.max_context_chars, fetch_table=_table_evidence,
                fetch_related=_related_evidence,
                task=request_task,
                recovery_available=recovery_round == 0 and not extra_query)
        unit_audit = None
        if contexts:
            from documents.document_availability import require_source
            for row in contexts:
                require_source(row.get("source") or "", row.get("source_sha256"),
                               document_id=row.get("document_id"))
        if contexts and (decision is None or decision.evidence_exposable):
            def context_priority(row):
                if row.get("target_record"):
                    return -1
                title, path = row.get('source') or '', row.get('heading_path') or []
                if business_scope_conflict(query, title, heading_path=path):
                    return 4
                if row.get('evidence_relation'):
                    return 2
                role, _ = evidence_use_role(query, title, path)
                return {'primary': 0, 'candidate': 1, 'background': 3}[role]

            # Stable order before budgeting: background must not displace a basis.
            contexts = sorted(contexts, key=context_priority)
            contexts, unit_audit = reassemble(
                contexts, _related_evidence, max_chars=settings.max_context_chars,
                fetch_document=_document_evidence)
        if unit_audit and unit_audit["incomplete"] and decision is not None:
            decision = GateDecision("uncertain", "evidence_unit_incomplete", decision.policy_version,
                                    decision.contributing_signals, False, True)
            reason, tier = decision.reason, "medium"
        if trace is not None:
            if unit_audit:
                trace.evidence_units = unit_audit
            trace.record_candidates("final", contexts)
            if decision is not None:
                trace.record_gate(
                    decision.status, reason, best_score,
                    policy_version=decision.policy_version,
                    signals=(signals.to_dict() if signals is not None else None),
                    contributing_signals=list(decision.contributing_signals),
                    retry_allowed=decision.retry_allowed,
                    evidence_exposable=decision.evidence_exposable)
            else:
                # Legacy callers only (no evidence-gate decision).
                if contexts:
                    gate = "supported" if tier == "high" else "ambiguous"
                else:
                    gate = "empty" if reason == "empty" or reason.startswith("absent:") \
                        else "irrelevant"
                trace.record_gate(gate, reason, best_score)
            trace.record_timing("total", (time.perf_counter() - retrieval_started) * 1000)
        result = {"contexts": contexts, "tier": tier, "reason": reason,
                  "missing_files": _missing_files(contexts)}
        if unit_audit:
            result["evidence_units"] = unit_audit
        if table_selection is not None:
            result["table_selection"] = table_selection
        if target_record is not None:
            result["target_record"] = target_record
        if decision is not None:
            result["gate"] = decision.to_dict()
            # Private hand-off to the bounded corrective-query builder. It is
            # not exposed through ToolResult.data.
            result["signals"] = (signals.to_dict() if signals is not None else None)
        require_ready(source_revision)
        return result

    ensure_collection()
    # ★★ 阶段 2d 多轮检索接续（**反问链路的命门**）：第 2 轮常是「我大二，自动化专业，
    #   成绩中等」这类**补充句** —— 里面**没有「转专业」三个字**，单用它检索必跑偏
    #   （与上一轮命中的文档毫无词面交集 → dense/sparse 两路都召不到它）。
    #
    #   做法是方案说的「**先试最便宜的拼接**」：把上一轮问句前置拼进本轮，**零额外 LLM
    #   调用**。向量侧 bge-m3 与 sparse 侧 BM25 都按整串算，上一轮的主题词因此仍在。
    #   截到 120 字：上一轮问句通常很短，截断只为防御（有人把整段粘进对话框）。
    #
    #   ⚠️ 拼接**会稀释**当轮自足的问句（「那奖学金怎么评?」会带上上一轮的「转专业」）——
    #      这是本项要在 `multi_turn` 组上 A/B 的头号问题（「不接续 / 拼接 / 改写」三者对比），
    #      不是拍板能定的。开关 `MULTITURN_CARRY=0` 立刻回到「只用当轮原句」的旧行为。
    carry = prev_query if settings.multiturn_carry else None
    plan = build_query_plan(query, keywords, carry)
    query = plan.semantic_query
    if document_id is not None and (
            not policy.require_semantic_recovery
            or policy.version == POLICY_V1.version
            or int(recovery_round or 0) != 1):
        document_id = None
    scoped = document_id is not None
    if trace is not None:
        trace.set_effective_query(query, "multiturn_carry" if carry else None)
        if scoped:
            trace.set_effective_query(query, "document_scoped_recovery")
        trace.set_query_plan(plan.sparse_query, list(plan.keywords))
    # ★ 覆盖面前置判空：查询点名了语料里不存在的文档/文号 → 直接判空（确定性、零误伤）。
    #   放在最前面是有意的 —— 这类请求**连检索都不必做**，库里根本没有它。
    absent = _absent_reference(query)
    if absent:
        signals, decision = _gate([], [], None, absent)
        return _out([], "low", decision.reason, None,
                    decision=decision, signals=signals)
    top_k = top_k or settings.top_k
    expand_limit = max(top_k * 4, settings.recall_pool)  # 召回池：先宽召回再精排/评估，避免漏召回
    retrieve_limit = max(expand_limit, 1000) if scoped else expand_limit
    # 拉大召回池后先按文本去重：同模板文档的重复块会占满槽位（见 _dedupe_by_text）
    recalled = (_retrieve_chunks(query, limit=retrieve_limit, trace=trace,
                                 sparse_query=plan.sparse_query,
                                 document_id=document_id)
                if plan.keywords else
                _retrieve_chunks(query, limit=retrieve_limit, trace=trace,
                                 document_id=document_id))
    if extra_query and not scoped:
        # Bounded corrective recall: the normalized query only widens the pool;
        # it never replaces the original query and reranking still uses it.
        extra = _retrieve_chunks(extra_query, limit=expand_limit, trace=None)
        if extra:
            merged = _rrf_fuse([list(recalled), list(extra)])
            combined = _RetrievedChunks(merged)
            combined.focus_document_id = (getattr(recalled, "focus_document_id", None)
                                          or getattr(extra, "focus_document_id", None))
            recalled = combined
            if trace is not None:
                trace.record_candidates("corrective_fused", merged)
                if "corrective" not in trace.normalization_steps:
                    trace.normalization_steps.append("corrective")
    focus_document_id = getattr(recalled, "focus_document_id", None)
    check_cancelled()
    if scoped and focus_document_id is None:
        focus_document_id = int(document_id)
    if trace is not None:
        trace.set_focus_document_id(focus_document_id)
    chunks = _dedupe_by_text(recalled)
    if trace is not None:
        trace.record_candidates("exact_deduped", chunks)
    # ★ 跨文档**近似**折叠（见 _dedupe_near 的实测）：逐字去重抓不到"只差几个数字"
    #   的模板复制体，而不折叠它们就会各占一个候选位、把真正的文档挤出精排窗口。
    chunks = _dedupe_near(chunks, settings.near_dup_threshold)
    if trace is not None:
        trace.record_candidates("near_deduped", chunks)
    # ★ 精排池上限：深召回（RECALL_POOL 调大）后两路并集可能上千条，
    #   而精排是一次 forward 全部 pairs —— 实测一次喂 120 条直接 CUDA OOM
    #   （24G 卡上 vLLM 已占 21.5G）。截到 40 与改前的池子同规模，精排成本不变。
    if len(chunks) > settings.rerank_pool:
        chunks = chunks[:settings.rerank_pool]
    if trace is not None:
        trace.record_candidates("rerank_pool", chunks)
    if not chunks:
        signals, decision = _gate([], [], None)
        return _out([], "low", decision.reason, None,
                    decision=decision, signals=signals)

    def _best_dense():
        """候选里最高的 dense 余弦分。

        判档问的是「库里到底有没有相关内容」，这个信号只有余弦分能给——
        RRF 分是名次倒数之和（量级 0.0x），拿它比 crag 阈值会把所有题判空。
        """
        vals = [c["score"] for c in chunks if c.get("score") is not None]
        return max(vals) if vals else None

    if use_reranker:
        rerank_started = time.perf_counter()
        try:
            _rerank(query, chunks)
            ranked = sorted(chunks, key=lambda c: c.get("rerank_score") or 0, reverse=True)
            best = ranked[0].get("rerank_score")
        except (InterruptedError, TimeoutError):
            raise
        except Exception as e:
            # ★★ 2026-10-08：这里原来是**完全静默**的降级 —— `except Exception` 吞掉异常、
            #   悄悄改用 embedding 分数判档（crag 0.6/0.3），**结果照常产出**。
            #   后果实测：一次 636 次检索的 A/B 里精排降级，同一 318 题集算出
            #   R@5 95.91%、low 6 道（真值 99.37%、low 1 道）——**统计被污染却看不出来**。
            #   与「rerank_m3 只传了一半 → 生产静默无精排」是同一类病。
            #   两处修：
            #     ① **一定打日志**（生产也要能看见降级；原因写明异常类型与消息）；
            #     ② `settings.rerank_strict` 为真时**直接抛出**，让评测任务**显式中止**，
            #        禁止降级结果流入统计。生产保持失败开放（铁律 12），默认关。
            logger.warning("rerank 失败，降级为 embedding 判档（%s: %.160s）q=%.40s",
                           type(e).__name__, str(e), query)
            if settings.rerank_strict:
                raise
            if trace is not None:
                trace.record_degradation("rerank", "fallback", str(e))
            use_reranker = False
            ranked, best = chunks, _best_dense()
        finally:
            if trace is not None:
                trace.record_timing("rerank", (time.perf_counter() - rerank_started) * 1000)
    else:
        # ★ **不重排**：RRF 融合的顺序就是最终顺序。再按余弦分排一次会**丢掉 sparse 路的
        #   全部贡献**（稀疏独有的候选 score=None，排到最后），等于白做混合召回。
        ranked, best = chunks, _best_dense()
    if trace is not None:
        trace.record_candidates("reranked", ranked)

    # Filter before document caps/top-k, not after structural expansion: linked
    # context may have a low score or no independently computed score at all.
    eligible_ranked = ([row for row in ranked
                        if isinstance(row.get("rerank_score"), (int, float))
                        and math.isfinite(row["rerank_score"])
                        and row["rerank_score"] >= MIN_CANDIDATE_RERANK_SCORE]
                       if use_reranker else ranked)
    if trace is not None:
        trace.record_candidates("candidate_filtered", eligible_ranked)

    # ★ 阶段 2a → 2b 的**顺序是有意的**：先按流程章节补齐（expand 补进来的块正文里
    #   也可能带外链引用），再做跨文档跟随 —— 反过来会让 expand 补的块不被扫到。
    #   两者都接在 `_cap_per_doc` **之后**（理由见 _expand_flow；跟随同理：补进来的
    #   块没有精排分，接在前面会被 2 块/文档的限额剔光）。
    def _select_top() -> list[dict]:
        nonlocal table_selection
        if settings.gate_multi_signal and focus_document_id is not None:
            return _cap_per_doc_focus(eligible_ranked, top_k, focus_document_id,
                                      settings.multi_signal_focus_cap)
        baseline = _cap_per_doc(eligible_ranked, top_k)
        selected, table_selection = select_table_rows(
            query, eligible_ranked, baseline, top_k=top_k, per_doc=settings.max_chunks_per_doc,
            max_chars=settings.max_context_chars,
            min_score=(threshold_on_single_scale(policy.high_support)
                       if 0 < policy.high_support < 1 else float("inf")))
        if trace is not None and table_selection is not None:
            trace.table_selection = table_selection
        return selected

    if scoped:
        top = _only_document(_expand_flow(eligible_ranked[:top_k]), document_id)
    else:
        top = _follow_refs(_expand_flow(_select_top()))
    if trace is not None:
        # Telemetry only: the contexts selected before the final gate. It lets
        # offline impact scans reconstruct the legacy gate's evidence exactly.
        trace.record_candidates("selected", top)
    signals, decision = _gate(ranked, top, best)
    if scoped and trace is not None:
        trace.record_recovery({
            "recovery_attempted": True,
            "recovery_kind": "document_scoped",
            "original_query": trace.original_query,
            "scoped_document_id": str(int(document_id)),
            "scoped_document_title": (top[0].get("source") if top else None),
            "scoped_candidate_count": len(ranked),
            "selected_scoped_chunk_ids": [
                f"{c.get('document_id')}:{c.get('chunk_idx')}"
                for c in top if c.get("document_id") is not None
                and c.get("chunk_idx") is not None
            ],
            "recovery_decision": decision.status,
            "recovery_reason": decision.reason,
            "recovery_round": max(1, int(recovery_round or 1)),
            "evidence_binding": [{
                "doc_id": str(c.get("document_id")),
                "chunk_id": f"{c.get('document_id')}:{c.get('chunk_idx')}",
                "source": c.get("source") or "",
                "heading_path": list(c.get("heading_path") or []),
            } for c in top],
            "policy_version": decision.policy_version,
        })

    # Legacy optional grader (production frozen off). It may only refine an
    # uncertain decision; the deterministic gate remains the default judge.
    if use_llm_grader and decision.status == "uncertain":
        from llm import llm_client
        context_text = "\n\n".join(f"《{c['source']}》\n{c['text']}" for c in top)
        try:
            if llm_client.grade_relevance(query, context_text):
                decision = GateDecision("supported", "grader_yes",
                                        decision.policy_version,
                                        decision.contributing_signals, False, True)
            else:
                decision = GateDecision("irrelevant", "grader_no",
                                        decision.policy_version,
                                        decision.contributing_signals, False, False)
        except Exception:
            if trace is not None:
                trace.record_degradation("grader", "fallback")
            decision = GateDecision("uncertain", "grader_error",
                                    decision.policy_version,
                                    decision.contributing_signals, True, False)

    if (table_selection and table_selection["omitted_chunk_ids"]
            and decision.status in ("supported", "uncertain") and decision.evidence_exposable):
        decision = GateDecision("uncertain", "table_enumeration_incomplete", decision.policy_version,
                                decision.contributing_signals, False, True)
    contexts = top if decision.status in ("supported", "uncertain") else []
    return _out(contexts, _tier_of(decision), decision.reason, best,
                decision=decision, signals=signals)


# ★★ 2026-10-01 冻结检索参数（单一来源）：
#   后台「检索调试」页不再提供阈值/开关自定义，检索参数一律走下面这个入口，
#   聊天链路（routers/chat.py）与调试台（routers/admin.py）**共用它** —— 只此一处，
#   别处不许再各写一套（复制出来的迟早漂移，同 clean_path/short_title 的教训）。
#   · rerank 0.72 / 0.555 = 318 可答 + 24 越界上的标定值；
#   · 精排常开。
#
# ⚠️ 2026-10-01 实测：**LLM 评估器目前不可用，置 False**。两重故障：
#   ① `llm_client.grade_relevance` 的 `httpx.Timeout(connect=, read=)` 在 httpx 0.28.1 下
#      **每次抛 ValueError** → 中档题恒走 `except` → tier 恒 medium / reason=grader_error，
#      cRAG 评估**从未生效过**（318 基线就是在"评估器恒挂"下测的）；
#   ② 修好①之后更糟：评估器 prompt 要求「只回答一个词：是 或 否」，而**微调后的模型不照做**
#      （实测返回「**不能。**」「可以，我接着给你看。」「公选课要修多少学分」），
#      而判定用的是 `"是" in content` → 几乎恒为 False → **会把有答案的题判空**。
#   所以在评估器按「分类器」重新设计并通过验收之前，这里**必须保持 False**：
#   它关闭时 tier=medium 但**上下文照常返回**（安全兜底），开启时是**静默判空**（危险）。
#   想恢复评估器，得先解决②，再单独做一次 A/B —— 不能只修①就打开。
FROZEN_USE_RERANKER = True
FROZEN_USE_LLM_GRADER = False


def retrieve_frozen(query: str, prev_query: str | None = None,
                    trace: RetrievalTrace | None = None, *,
                    keywords: list[str] | None = None,
                    extra_query: str | None = None,
                    policy: GatePolicy = POLICY_V1,
                    document_id=None,
                    recovery_round: int = 0, request_task=None) -> dict:
    """生产检索入口：参数**冻结**为标定常量（后台不再可调）。

    `prev_query`（阶段 2d）：上一轮的**用户问句**，用于多轮检索接续（拼接，零 LLM 调用）。
    由 `routers/chat.py` 从 history 里取最后一条 user 消息传进来。
    `extra_query` 只用于一次已授权的确定性纠错召回；原问句保持不变。
    """
    task_args = {"request_task": request_task} if request_task is not None else {}
    return retrieve(query, top_k=settings.top_k, prev_query=prev_query,
                    keywords=keywords, extra_query=extra_query,
                    crag_high=DEFAULT_CRAG_HIGH, crag_low=DEFAULT_CRAG_LOW,
                    use_llm_grader=FROZEN_USE_LLM_GRADER, use_reranker=FROZEN_USE_RERANKER,
                    rerank_high=DEFAULT_RERANK_HIGH, rerank_low=DEFAULT_RERANK_LOW,
                    trace=trace, policy=policy, document_id=document_id,
                    recovery_round=recovery_round, **task_args)
