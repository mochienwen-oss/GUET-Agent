# reference_follow.py —— 跨文档引用跟随（阶段 2b）
# （2026-10-13 阶段2 自 `backend/refs.py` 原样迁入，只搬位置、不动判据）
#
# 要解决的问题：语料里大量条款以「按《X办法》执行」的方式**外链**到另一份文件。
#   检索命中这条引用句时，被引用的《X》**未必**同时被召回（它的向量与问句可能不相似），
#   于是模型只看到「按《X》执行」这半句 —— 要么答不上，要么**照着文件名编内容**。
#
# ★★ 核心设计：**正则只是生成器，白名单才是运行时真相**。
#   为什么不能运行时跑正则直接跟：
#   ① 书名号里的东西**大量不是文档** —— 课程名（《机械设计》）、外校文件
#      （《XX大学学生管理规定》）、表格名。正则分不出来：`coverage._DOC_SUFFIX` 的尾词
#      过滤只是**降低**误伤（它的注释里就记着《问卷设计与抽样调查》那类实伤），不是消除。
#   ② 跟随是**已核验事实**，不是推断。本轮实测的教训（铁律 6）：
#      「关键字定位实测 9/14 找错款」—— 抽出来的判据**未经人核一律不许启用**。
#   所以：`extract_refs()` 扫全语料建**候选队列** → 人逐条读原文确认「这份文件里确实
#   引用了《X》」→ 把 X 的**归一名**写进 `backend/data/ref_allowlist.json`
#   → 运行时**只查这个名单**，正则一行都不跑。
#
# ★★ 白名单里存的是**名字**，不是 document_id。
#   为什么（这条踩过就会静默跟错文档）：document_id 是**建库时分配的序号**，重新入库
#   （换语料 / 重跑 ingest / 换 collection）后**全变**。把 id 固化进人手核过的文件，
#   重库一次就变成「按名字查到了、id 指向别的文档」——**跟错文档比不跟更糟**：
#   模型会拿一份不相干的文件冒充依据（正是红线要防的编造）。所以运行时按名字
#   （`coverage.load_corpus_index` 的 `ids`）解析，重库后照样对。
#   ⚠️ 副作用：白名单的键必须与 `coverage._norm` 同口径，写错键 = **静默失效**
#      （查不到、不报错）→ 用 `python -m refs --check` 自检（在服务器上跑，能连 Qdrant 时）。
#
# ⚠️ 白名单是**新文件，部署必须一起拷**（`backend/data/ref_allowlist.json`）。
#    拷不到 = 空白名单 = 跟随与「缺失提示」**整个自动关闭**（铁律 12：失败开放）。
#    ⚠️ 与 `backend/` 下其它模块不同，它**不进代码发布流**（容器镜像 / 发布包别漏）。
#
# 档位：一跳、最多 2 份文档、最多补 4 块（≈1600 字）。
#   —— 上限刻意压得很小：被引用的常常是整份办法（1~4 万字），全塞进去会把
#      `max_context_chars` 预算吃光，反而把**用户真正问的那份**挤掉。
#   ⚠️ 「取被引用文档的哪几块」是个**未标定的选择**（现在是最朴素的「按序取前几块」）——
#      它是执行顺序第 7 步（A/B）该验的项，不是拍脑袋能定的值。

import json
import os
import re
from pathlib import Path
from core.paths import BACKEND_ROOT

from core.config import settings

_BACKEND = BACKEND_ROOT
ALLOWLIST_PATH = str(_BACKEND / "data" / "ref_allowlist.json")

# ── 生成器判据（**离线**抽候选队列用；运行时不用，见模块头）──────────────────
# 「按/按照/参照/依据/见/遵照/根据 + 《书名号》」。`见` 单字容易误伤（见《…》），
# 但它正是「详见《X》」这类外链的常用引导词，保留；误伤由人核兜（队列里看得见原文）。
_RE_REF = re.compile(r"(?:按|按照|参照|依据|见|遵照|根据)\s*《([^》]{2,60})》")

# 跟随上限（见模块头）
_MAX_DOCS = 2
_MAX_ADDED = 4
_MAX_CHARS = 1600

_ALLOW: dict | None = None
_ALLOW_MTIME: float | None = None
_IDS: dict | None = None       # {归一名: document_id}，**进程内建一次**（同 rag._coverage_index）


def ref_follow_enabled() -> bool:
    """开关：优先 `config.settings.ref_follow`，读不到退环境变量 `REF_FOLLOW=0`，都读不到按开。

    ★ 与 `flows.flow_expand_enabled` 同形：函数而非模块常量，因为本模块被
      `_work/tutor/*`（backend 目录外、import 不到 config）与 backend 两处引用。
    """
    try:
        return bool(settings.ref_follow)
    except Exception:
        pass
    return os.environ.get("REF_FOLLOW", "1") not in ("0", "false", "False")


def _norm(s: str) -> str:
    """名字归一 —— **复用 `coverage._norm`**（单一来源）。

    ★ 绝不在这里另写一套：`coverage._norm` 与 `citations.short_title` 同口径
      （去校名前缀 / 去尾部括号 / 去空白与书名号）。两套归一 = 同一份文件在
      白名单里查得到、在判空里查不到（或反之），是本仓库 `clean_path`/`short_title`
      那类历史事故的标准形态。
    取不到 coverage（import 失败）时退回**保守归一**：只去书名号与空白。
    """
    try:
        from agent.tools.search_regulations.scripts.evidence_ops.coverage import _norm as cov_norm
        return cov_norm(s)
    except Exception:
        return re.sub(r"[\s《》]", "", s or "")


def _looks_like_doc(name: str) -> bool:
    """像不像公文名（而不是课程名/术语）。

    ★ 复用 `coverage._DOC_SUFFIX`：那 25 个尾词是**已标定**的判据，且它的注释里
      记着误伤实例（《问卷设计与抽样调查》《最优化方法》三个课程名把可答题判空）。
      这里同口径，免得「引用跟随」把课程名当文件去库里捞。
    """
    try:
        from agent.tools.search_regulations.scripts.evidence_ops.coverage import _DOC_SUFFIX
    except Exception:
        _DOC_SUFFIX = ("办法", "规定", "细则", "条例", "章程", "方案", "通知")
    t = _norm(name)
    return len(t) >= 4 and t.endswith(_DOC_SUFFIX)


def _refs_in(text: str) -> list[str]:
    """正文里**像文档**的书名号引用（已归一、去重、按序）。"""
    out, seen = [], set()
    for m in re.finditer(r"《([^》]{2,60})》", text or ""):
        raw = m.group(1).strip()
        if not _looks_like_doc(raw):
            continue
        t = _norm(raw)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_refs(text: str) -> list[str]:
    """从正文里抽出被外链的文档名（**生成器**，只建队列，不据此下结论）。

    返回**原始**书名号内容（未归一），按出现顺序去重 —— 人核时看原文，不看归一名。
    ⚠️ 与运行时用的 `_refs_in` **刻意不同**：这里**不做尾词过滤**。
      尾词过滤是为了运行时**降低误伤**（宁可不跟）；而生成器若也过滤，人核队列里
      就看不到「被引用的其实是课程名」这类反例 —— 判据的准确性就没人验了（铁律 8：
      代理判据只建队列，判定读原文）。
    """
    out, seen = [], set()
    for m in _RE_REF.finditer(text or ""):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def load_allowlist(path: str | None = None) -> dict:
    """读白名单 → `{归一名: {"note": str}}`（键是**归一名**，见模块头）。

    **失败一律返回 `{}`**（读不到 / JSON 坏 / 结构不对）—— 空白名单 = 整个 2b 静默关闭
    （铁律 12：新开关读不到要退回旧行为，不是抛错、更不是按正则硬跟）。
    带 mtime 缓存：白名单是**运行时可换**的文件（人核后直接覆盖），改了就重读 ——
    它不像 Qdrant 索引那样贵，没必要钉死在进程生命周期里。
    """
    global _ALLOW, _ALLOW_MTIME
    p = path or ALLOWLIST_PATH
    try:
        mt = os.path.getmtime(p)
    except OSError:
        _ALLOW, _ALLOW_MTIME = {}, None
        return {}
    if _ALLOW is not None and _ALLOW_MTIME == mt:
        return _ALLOW
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        names = raw.get("names") if isinstance(raw, dict) else None
        if not isinstance(names, dict):
            raise ValueError('ref_allowlist.json 结构应为 {"names": {归一名: {...}}}')
        out = {k: (v if isinstance(v, dict) else {}) for k, v in names.items() if k}
        _ALLOW, _ALLOW_MTIME = out, mt
        return out
    except Exception:
        _ALLOW, _ALLOW_MTIME = {}, None
        return {}


def corpus_ids() -> dict:
    """`{归一名: document_id}`，**与门控判空同一套索引**（`coverage.load_corpus_index`）。

    ★ **进程内只建一次**（同 `rag._coverage_index` 的做法）：整库 scroll 是几百毫秒到
      一秒的开销，而 2b 每轮要它两次（跟随 + 缺失检查）。失败返回 `{}`（**不跟随**）——
      ⚠️ 失败时**故意不用旧缓存**：Qdrant 不可用多半意味着库正在重建，这时拿旧 id
      去跟就是「跟错文档」，比不跟更糟（见模块头）。
    ★ **重新入库后要重启服务**（或调 `reset_cache()`）—— document_id 会全变。
      `start_all.sh` 的流程本就是入库后重启，这一步不用额外加。
    """
    global _IDS
    if _IDS is not None:
        return _IDS
    try:
        from agent.tools.search_regulations.scripts.evidence_ops import coverage
        _IDS = coverage.load_corpus_index().get("ids") or {}
    except Exception:
        return {}
    return _IDS


def reset_cache() -> None:
    """丢掉进程内缓存（重入库 / 换 collection 后调用；测试也用）。"""
    global _IDS
    _IDS = None


def missing_refs(chunks: list[dict]) -> list[str]:
    """上下文里**被引用但语料中不存在**的文件名 → 交给模型明说「这份没有收录」。

    ★ 为什么这条本身就是正确行为（不是缺陷）：语料实测**有多份被外链的配套文件不在库**
      （退役复学免修成绩认定办法、考勤办法、实验教学工作条例、学校免试规定）。
      模型看到「按《考勤办法》处理」却又没有《考勤办法》时，**最可能的就是把文件名
      当依据编一段内容**。明确告知缺哪一份，是把「编造」换成「如实告知」的唯一手段。

    存在性检查与 `coverage.absent_reference` **同索引、同判据**（归一名 + 双向子串）——
    两处若各查一套，「提问里点名的文件」与「资料里引用的文件」会出现一个拦一个放。
    失败开放：白名单/索引取不到 → 返回 `[]`（不提示，退回旧行为）。
    """
    if not ref_follow_enabled() or not chunks:
        return []
    if not load_allowlist():
        return []          # 白名单没就位 = 2b 整个关闭（含缺失提示），见模块头
    ids = corpus_ids()
    if not ids:
        return []
    names = set(ids)
    out, seen = [], set()
    for c in chunks:
        for t in _refs_in(c.get("text") or c.get("snippet") or ""):
            if t in seen:
                continue
            seen.add(t)
            # ★ 双向子串（与 coverage.absent_reference 同判据）：`《桂林电子科技大学章程》`
            #   与语料名 `桂林电子科技大学章程（2023年核准稿）` 互不为子串。
            if not any(t and (t in n or n in t) for n in names):
                out.append(t)
    return out


def follow_refs(chunks: list[dict], *, max_docs: int = _MAX_DOCS,
                max_added: int = _MAX_ADDED, max_chars: int = _MAX_CHARS) -> list[dict]:
    """上下文里引用了**白名单**中的文档 → 把**被引用文档**的块补进来（一跳，不递归）。

    只查白名单 + 名字→id 解析，**不跑正则、不猜**。补进来的块带 `ref_followed=True`
    以便 A/B 计数；不触发 2a（流程扩展）也不触发再一跳 —— 递归跟随会滚成雪球。

    失败开放：白名单空 / 取文档块失败 → 原样返回（宁可不跟，不可报错）。
    """
    if not ref_follow_enabled() or not chunks:
        return chunks
    allow = load_allowlist()
    if not allow:
        return chunks
    ids = corpus_ids()
    if not ids:
        return chunks
    try:
        from agent.tools.search_regulations.scripts.index_ops import indexing
    except Exception:
        return chunks

    out = list(chunks)
    have = {c.get("document_id") for c in out}
    docs_used, added, chars = 0, 0, 0

    for c in chunks:
        if docs_used >= max_docs or added >= max_added or chars >= max_chars:
            break
        for t in _refs_in(c.get("text") or c.get("snippet") or ""):
            if docs_used >= max_docs or added >= max_added or chars >= max_chars:
                break
            if t not in allow:
                continue              # ★ 未人核过的引用一律不跟（这就是白名单的全部作用）
            did = ids.get(t)
            if did is None:
                continue              # 名单里有、库里查不到：多半是键写错的静默失效 → --check 能抓
            if did in have:
                continue              # 本文档已在上下文里（用户问的就是它）→ 不必跟随
            try:
                sibs = indexing.get_document_chunks(did)
            except Exception:
                continue
            if not sibs:
                continue
            have.add(did)             # ★ 先登记再取块：同一份文档只跟一次
            docs_used += 1
            for s in sibs:
                if added >= max_added or chars >= max_chars:
                    break
                txt = s.get("text") or ""
                out.append(dict(s, **{
                    "document_id": did,
                    "chunk_idx": s.get("chunk_idx"),
                    "source": s.get("source") or t,
                    "snippet": txt,
                    "text": txt,
                    "heading_path": s.get("heading_path") or [],
                    "block_type": s.get("block_type") or "",
                    # 未进精排 → 无精排分。沿用引用方的分数只为排序稳定，
                    # **不参与任何判档**（判档在跟随之前就做完了）。
                    "score": c.get("score"),
                    "rrf": c.get("rrf"),
                    "ref_followed": True,
                }))
                added += 1
                chars += len(txt)
    return out


# ── 自检入口（在服务器上跑，要连 Qdrant）────────────────────────────────────
# `python -m refs --norm "《桂林电子科技大学本科生课程认定暂行办法》"` → 打印归一名（白名单的键）
# `python -m refs --check`  → 逐条报告白名单里**在库里找不到**的键（静默失效的唯一防线）
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if "--norm" in args:
        for a in args[args.index("--norm") + 1:]:
            print(f"{a}\t→\t{_norm(a)}")
        raise SystemExit(0)
    if "--check" in args:
        allow = load_allowlist()
        if not allow:
            print("白名单为空或读不到：", ALLOWLIST_PATH)
            raise SystemExit(1)
        ids = corpus_ids()
        if not ids:
            print("拿不到语料索引（Qdrant 不可用？）—— 无法自检")
            raise SystemExit(2)
        bad = [k for k in allow if not any(k in n or n in k for n in ids)]
        print(f"白名单 {len(allow)} 条；语料 {len(ids)} 份文档")
        for k in bad:
            print("  ✗ 库里找不到（键写错？）：", k)
        print("结论：%d 条失效" % len(bad))
        raise SystemExit(1 if bad else 0)
    print(__doc__ or "refs.py —— 跨文档引用跟随（阶段 2b）。用 --norm / --check。")
