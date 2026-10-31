# coverage.py —— 「库里到底有没有这份东西」的**确定性**检查（门控的第二条腿）
#
# 为什么需要它（2026-10-02 普查结论）：
#   `rag.retrieve` 的门控只用 `best_rerank < 阈值` 判空 —— 而实测 6 个信号
#   （rerank / dense / rrf / 分差 / 两种词面接地）在「越界题」与「可答题」上**全部重叠**：
#   可答题最低 0.5325，越界题最高 0.6984。**相关度分数回答不了"库里有没有"这个覆盖性问题。**
#
#   但其中有一类越界是**可以确定性判定**的：用户**点名了一份具体文档或文号**，而它
#   根本不在语料里（实测样例：`《桂林电子科技大学学生宿舍空调使用管理规定》`、
#   `桂电教〔2099〕1号`）。这时候任何回答都是编造 —— 必须判空，且这个判据**零幻觉、
#   零误伤**（它只做字符串存在性检查，不做任何"理解"）。
#
#   ⚠️ 本模块**只做这一件事**，不试图覆盖"库外业务"（像「食堂几点开门」这种没有
#      点名实体的越界）—— 那类没有确定性信号，硬判只会误伤可答题
#      （普查里 `ov_max5` 单信号最优也漏放 16/24）。**宁可不拦，不可误拦。**
#
# 与 `citations.short_title` 共用同一套名字归一（单一来源，别处不许再写一套）。

import re

from agent.core.citations import short_title

# 书名号里的文档引用
_RE_WRAP = re.compile(r"《([^》]{2,60})》")
# ★ 只有**看起来像文档**的书名号内容才参与判空。为什么必须加这道过滤（2026-10-02 实测误伤）：
#   学生提问里的书名号**大量是课程名**，实测 `ref-C-010`：
#     「我是应用统计学专业的，想在第5学期一次选上《问卷设计与抽样调查》《最优化方法(双语教学)》
#       《数据挖掘及其应用》，这三门会撞学期吗？」
#   —— 三个都是课程名，语料里当然"没有这份文档"，于是被判空（**误伤**，答案其实就在
#   该专业培养方案里）。课程名与文件名在字面上没法区分，只能靠**尾词**判：
#   公文名几乎总以「办法/规定/细则/…」收尾，课程名不会。宁可漏拦，不可误拦。
_DOC_SUFFIX = ("办法", "规定", "细则", "条例", "章程", "方案", "通知", "规则", "规范",
               "意见", "指南", "条件", "制度", "准则", "标准", "决定", "公告",
               "批复", "答复", "函", "手册", "汇编", "计划", "目录", "清单")
# 公文文号：`桂电教〔2026〕13号` / `桂电教[2026]13号` / `桂电职改〔2022〕5号`
_RE_DOCNO = re.compile(r"[一-鿿]{2,8}[〔\[(]\s*(\d{4})\s*[〕\])]\s*(\d{1,3})\s*号")
# 长得像"外校"的机构名。**只用来兜底**，且必须排除本校。
_OURS = "桂林电子科技大学"
_RE_UNIV = re.compile(r"([一-鿿]{2,6}(?:大学|学院))")


def _norm(s: str) -> str:
    """名字归一：与 `citations` 同口径（去校名前缀、去尾部括号、去空白与书名号）。"""
    s = short_title(s or "").replace(_OURS, "")
    s = re.sub(r"[\s《》()（）〔〕\[\]·]", "", s)
    return s


def docno_of(src: str) -> str:
    """从文件名/正文里取文号，归一成 `年份-序号`；取不到返回空串。

    归一成 `年-号` 而不是保留原文，是因为同一文号在不同地方写作
    `桂电教〔2026〕13号` / `桂电教[2026]13号` —— 不归一会漏判成本校已有。
    """
    m = _RE_DOCNO.search(src or "")
    return f"{m.group(1)}-{int(m.group(2))}" if m else ""


def load_corpus_index(client=None) -> dict:
    """把语料里**全部**文档名与文号收成索引（从 Qdrant payload 的 `source` 去重）。

    返回 {"names": set[归一名], "docnos": set["年-号"], "raw": set[原始 source],
          "ids": {归一名: document_id}}。
    失败时抛异常，由调用方决定是否降级（**不许静默当"空语料"** —— 那会 100% 判空）。

    ★ `ids`（2026-10-08 加，**加性**字段，老调用方只看 names/docnos，不受影响）：
      供 `refs.py` 的跨文档引用跟随把「人名 → Qdrant 里的 document_id」。
      ⚠️ **为什么白名单里存的是"名字"而不是 id**：document_id 是**建库时分配的序号**，
      重新入库（换语料 / 重跑 ingest）后**全变** —— 把 id 固化进人手核过的白名单，
      重库一次就静默跟错文档（跟错文档比不跟更糟：模型会拿别的文件冒充依据）。
      按名字在运行时解析，重库后仍然对。
    """
    if client is None:
        from agent.tools.search_regulations.scripts.index_ops.indexing import _get_client
        from core.config import settings
        client = _get_client()
        coll = settings.qdrant_collection
    else:
        coll = None
        from core.config import settings
        coll = settings.qdrant_collection

    names, docnos, raw, ids = set(), set(), set(), {}
    offset = None
    while True:
        pts, offset = client.scroll(collection_name=coll, limit=1000,
                                    offset=offset, with_payload=["source", "document_id"],
                                    with_vectors=False)
        for p in pts:
            payload = p.payload or {}
            s = payload.get("source") or ""
            if not s:
                continue
            raw.add(s)
            n = _norm(s)
            names.add(n)
            did = payload.get("document_id")
            if n and isinstance(did, int) and n not in ids:
                ids[n] = did
            dn = docno_of(s)
            if dn:
                docnos.add(dn)
        if offset is None:
            break
    return {"names": names, "docnos": docnos, "raw": raw, "ids": ids}


def absent_reference(query: str, index: dict) -> str:
    """查询里若点名了一份**语料中不存在**的文档/文号 → 返回那个引用；否则返回 ""。

    只做「存在性」判断，不做任何语义理解 —— 这是它敢零误伤的原因。
    ⚠️ 包含关系用**双向子串**：`《桂林电子科技大学章程》` 与语料名
      `桂林电子科技大学章程（2023年核准稿）` 互不为子串，必须按「其一是另一个的子串」判，
      否则本校文件会被误判成"不存在"。
    """
    q = query or ""
    names, docnos = index.get("names") or set(), index.get("docnos") or set()

    for m in _RE_WRAP.finditer(q):
        t = _norm(m.group(1))
        if len(t) < 4:
            continue                      # 太短不当文档名（如《机械设计》是课程名）
        if not t.endswith(_DOC_SUFFIX):
            continue                      # ★ 不像公文名（多为课程名/术语）→ 放行，不据此判空
        if not any(t and (t in n or n in t) for n in names):
            return m.group(0)

    for m in _RE_DOCNO.finditer(q):
        key = f"{m.group(1)}-{int(m.group(2))}"
        if key not in docnos:
            return m.group(0)
    return ""
