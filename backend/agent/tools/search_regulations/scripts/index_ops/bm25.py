# bm25.py —— 自建 BM25 词权重，作为混合召回的 sparse 路
#
# 为什么自己算，而不用 bge-m3 自带的稀疏输出：
#   实测本机跑不动 —— dense + sparse 两份 bge-m3 同时驻留会把 8G 显存 / 15G 内存吃穿
#   （进程直接消失，连异常都没有）；退到 CPU 则是 2.3s/块、全量要 **117 分钟**。
#   而混合召回要的只是「精确词面」这一个信号，BM25 给得更准也更便宜：
#   全量分词是秒级，不需要第二个模型。实施清单原本就把这条路列为等效选项。
#
# 两个刻意的设计：
#   1. **文号整块成词**：`桂电教〔2025〕28号` 先抽成单个 token。这是最需要精确匹配的
#      一类查询词，而 jieba 会把「桂电教」切碎、把文号退化成「电教/号/2025/28」几个泛词。
#   2. **词表与 IDF 落盘**（`model/bm25_vocab.json`）：入库与查询**必须**用同一套 idf，
#      否则两边权重不可比、点积不再是 BM25。落盘也让重复入库的结果确定。
#
# 打分方式（拆成两半，点积即 BM25）：
#   doc 侧  w_d(t) = idf(t) · tf·(k1+1) / (tf + k1·(1-b+b·dl/avgdl))
#   query 侧 w_q(t) = 1            （查询词只表示「出现了」）
#   → dot(w_q, w_d) = Σ BM25 词项得分

import json
import math
import os
import re
from pathlib import Path
from core.paths import PROJECT_ROOT

_K1 = 1.5
_B = 0.75

# The vocabulary is a project-level index artifact.  The Phase-2 module move
# deepened this file by four directories; the artifact itself was not moved.
_DEFAULT_VOCAB = str(PROJECT_ROOT / "model" / "bm25_vocab.json")


def vocab_path() -> str:
    """当前词表文件路径。

    ★ 词表是**全语料统计量**（idf / avgdl），必须与语料一一对应。
    所以集合名和词表路径要**成对切换**（阶段5A 裁决 三.5）：
    设 `BM25_VOCAB_PATH` 就换词表；不设则沿用项目级默认 `model/bm25_vocab.json`，
    行为与本函数引入前**逐字一致**（默认关 = 旧路径）。
    """
    from core.config import settings
    return settings.bm25_vocab_path or _DEFAULT_VOCAB

# 文号：**机关简称 + 〔年份〕序号号**。允许半角/全角括号与中间空格。
#
# ⚠️ 机关必须锚定**已知简称表**，不能用 `[一-鿿]{2,8}` 去泛配。泛配是实测踩到的坑：
#   文档正文常写成「桂林电子科技大学校教(2025)5号」，泛配会把「电子科技大学校教」
#   整 8 个字吃成机关前缀 → 文档侧 token 变成 `电子科技大学校教(2025)5号`，
#   而查询侧（用户只写「校教〔2025〕5号」）是 `校教〔2025〕5号` —— **同一个文号被切成
#   两个不同 token，精确匹配彻底失效**。后果实测：ext 集专有名词题 R@1 卡在 8/9，
#   那道「校教〔2025〕5号文件规定的是什么？」两路都排不进 top-30。
#   锚定简称表后，两侧都归一成 `校教(2025)5号`，逐字相同。
_ORGANS = ("桂电教", "桂电人", "桂电学", "桂电科", "桂电职改", "桂电党",
           "桂电研", "桂电学位", "桂电招生", "校教")
_RE_DOCNO_KNOWN = re.compile(
    r"(?:" + "|".join(_ORGANS) + r")\s*[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d{1,4}\s*号")
# 兜底：机关不在简称表里（新机关/外来文号）时仍按泛配抽出来
_RE_DOCNO_ANY = re.compile(r"[一-鿿]{2,8}\s*[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d{1,4}\s*号")
_RE_DOCNO_CORE = re.compile(r"[（(]\s*\d{4}\s*[）)]\s*\d{1,4}\s*号")
# 只保留中文、字母、数字（丢掉标点/空白）；单个字符的英文/数字意义不大，留中文与词
_RE_KEEP = re.compile(r"^[一-鿿]+$|^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# 停用词：只收高频且无区分度的功能词。宁可少收——多收会把有效词面削掉。
_STOP = set("的 了 和 及 与 或 在 是 为 对 按 从 到 由 之 其 该 本 等 有 无 不 也 就 而 并 "
            "可以 应当 应该 必须 根据 依据 按照 关于 对于 以及 或者 一个 进行 "
            "第 条 章 节 款 项 规定 办法 通知".split())

_jieba = None


def _seg():
    global _jieba
    if _jieba is None:
        import jieba
        # 机关简称：不然「桂电教」会被切成「桂/电教」，文号在词面上就废了一半
        for w in ("桂电教", "桂电人", "桂电学", "桂电科", "桂电职改", "桂电党",
                  "桂电研", "桂电学位", "桂电招生", "校教", "桂电", "博士后",
                  "创新创业", "培养方案", "学分认定", "转专业", "辅修", "微专业"):
            jieba.add_word(w)
        _jieba = jieba
    return _jieba


def _canon_docno(s: str) -> str:
    """文号规范化：括号统一成半角圆括号、去掉所有空白。

    必须规范化，否则 `桂电教〔2025〕28号` 与 `桂电教(2025)28号` 是两个 token ——
    同一份文件在不同地方写法不同（标题里全角、正文里半角）就永远匹配不上。
    """
    s = re.sub(r"\s+", "", s)
    for a, b in (("〔", "("), ("〕", ")"), ("[", "("), ("]", ")"),
                 ("（", "("), ("）", ")")):
        s = s.replace(a, b)
    return s


def tokenize(text: str) -> list[str]:
    """分词 → 词元列表（含文号整块）。

    `cut_for_search` 而不是 `lcut`：会把长词再切出子词，检索召回更全
    （「培养方案」→ 也出「培养」「方案」）。
    """
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "")

    tokens: list[str] = []
    # ① 文号先整块抽出（**机关锚定简称表**），并从原文里挖掉，避免它被 jieba 切碎
    spans: list[tuple[int, int]] = []
    for m in _RE_DOCNO_KNOWN.finditer(s):
        tokens.append(_canon_docno(m.group(0)))
        spans.append(m.span())
    # 兜底：简称表覆盖不到的机关（新机关/外来文号）
    for m in _RE_DOCNO_ANY.finditer(s):
        # 与已知机关的命中**任何**重叠都跳过；否则同一个文号会额外产出一个
        # 只在文档侧存在的冗长 token（`电子科技大学校教(2025)5号`），白占词表位。
        if any(m.start() < b and a < m.end() for a, b in spans):
            continue
        canon = _canon_docno(m.group(0))
        tokens.append(canon)
        # ★ 额外发射**不带机关前缀的核心 token** `(年份)序号号`：
        #   机关写法千变万化（「桂电教」「学校教务处」…），核心部分却是稳定的。
        #   两侧都发射它，于是即便机关对不上也能在核心上匹配（用一点精确度换召回）。
        core = _RE_DOCNO_CORE.search(canon)
        if core:
            tokens.append(_canon_docno(core.group(0)))
        spans.append(m.span())
    for a, b in sorted(spans, reverse=True):          # 从后往前挖，避免位移
        s = s[:a] + " " + s[b:]
    # ② 其余交给 jieba
    for w in _seg().lcut_for_search(s):
        w = w.strip()
        if not w or w in _STOP or len(w) > 20:
            continue
        if _RE_KEEP.match(w):
            tokens.append(w)
    return tokens


def build_vocab(texts: list[str]) -> dict:
    """从全语料建词表 + IDF。

    ⚠️ **传入的必须是「块」，不是「整篇 md」**。N 与 df/avgdl 是打分公式里
    `dl`（本文档词元数）的同量纲统计量，而检索单位是块（约 3000 块，不是 90 个文件）。
    用文件统计会有一个实测出来的后果：表格题的查询词（「学分」「通识必修」「多少」）
    是全语料高频词，按文件算 df 时 idf 全挤在低值区（≈0.4），而 TF 项照常生效
    → **BM25 实际退化成按 TF 排序** → 把「提到这些词最多」的**别的专业培养方案**
    排上来压掉正确的那份。实测表格题 R@1 被拉低 16.7pp（无 rerank）/ 30.8pp（有 rerank）。
    按块统计后，判别词（专业名）的 idf 从 ~4 升到 ~7.6，高频词进一步压低，
    排序重新由判别词主导。
    """
    df: dict = {}
    total = 0
    for t in texts:
        toks = tokenize(t)
        # ⚠️ dl 与 avgdl 必须同定义：BM25 的 dl 是**总词元数**（含重复），
        #   所以 avgdl 也得用总词元数。原先这里写的是 `len(set(tokenize(t)))`
        #   （去重后的词元数），而 doc_vector 里 dl = len(toks) 是含重复的
        #   —— 于是 dl/avgdl 恒被放大约 4 倍，长度归一化整个失准。
        #   df 用去重后的（document frequency 的定义是「多少篇**含**该词」）。
        total += len(toks)
        for w in set(toks):
            df[w] = df.get(w, 0) + 1
    n = max(len(texts), 1)
    avgdl = max(total / n, 1.0)
    terms = {w: i for i, w in enumerate(sorted(df))}
    idf = [round(math.log(1 + (n - d + 0.5) / (d + 0.5)), 4) for d in
           (df[w] for w in sorted(df))]
    return {"terms": terms, "idf": idf, "avgdl": round(avgdl, 3), "n": n}


def save_vocab(vocab: dict, path: str | None = None) -> None:
    path = path or vocab_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    stage = str(path) + ".staging"
    with open(stage, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False)
    os.replace(stage, path)


def load_vocab(path: str | None = None) -> dict | None:
    path = path or vocab_path()
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def doc_vector(text: str, vocab: dict):
    """一块文本 → (indices, values)，用于写 Qdrant 的 sparse 向量。

    返回空（[]、[]）表示这块没有任何可检索词元——调用方应当跳过 sparse 向量，
    而不是写一个全零的稀疏向量（全零向量在点积里恒为 0，白白占位）。
    """
    toks = tokenize(text)
    if not toks:
        return [], []
    tf: dict = {}
    for w in toks:
        tf[w] = tf.get(w, 0) + 1
    dl = len(toks)
    avgdl = vocab.get("avgdl") or 1.0
    idx_map, idf = vocab["terms"], vocab["idf"]
    norm = _K1 * (1 - _B + _B * dl / avgdl)
    indices, values = [], []
    for w, c in tf.items():
        i = idx_map.get(w)
        if i is None:                      # 建词表之后才出现的词：入库侧忽略
            continue
        denom = c + norm
        if denom <= 0:
            continue
        indices.append(i)
        values.append(round(idf[i] * c * (_K1 + 1) / denom, 5))
    return indices, values


def query_vector(text: str, vocab: dict):
    """查询 → (indices, values)，值为 1.0（出现即计入）。

    与 doc_vector 的点积正好是 Σ BM25 词项得分——这就是把 BM25 拆成两半的做法。
    查询里的未见词直接丢掉（idf 无定义），这也是 BM25 的常规处理。
    """
    idx_map = vocab["terms"]
    seen = {idx_map[w] for w in set(tokenize(text)) if w in idx_map}
    idx = sorted(seen)
    return idx, [1.0] * len(idx)
