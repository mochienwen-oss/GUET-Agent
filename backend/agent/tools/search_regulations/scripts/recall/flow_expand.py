# flow_expand.py —— 「流程 / 程序条款」判据的**单一来源**（阶段 2a 与阶段 1 抽条件共用）
# （2026-10-13 阶段2 自 `backend/flows.py` 原样迁入，只搬位置、不动判据；下面的
#  `rag.` / `vector.` 是迁入前的旧模块名，函数仍在 retrieval.py / indexing.py 里）
#
# 为什么单独成模块：两处要用同一套判据 ——
#   ① `rag._expand_flow_sections`（阶段 2a）：命中了流程章节的一块，就把整节补齐，
#      否则「第十六条 转专业工作流程」只召回 (一)(二) 两步，模型看不到 (六) 审批发文；
#   ② `_work/_archive/tutor/extract_conditions.py`（阶段 1）：抽「条件条款」做评测标尺。
#   两套判据漂移是这类项目最容易犯的错（本仓库历史上 clean_path / short_title 各写一套），
#   所以判据只写在这里，别处 import。
#
# ★★ 判据**按真语料校准**（2026-10-08，`_work/tutor/recon_headings.py` 扫 _md/ 90 份），
#    不是照方案原稿抄。原稿的判据在真语料上有**四处**站不住，实测如下：
#
#    ① 原判据「heading_path 任一路径元素命中 流程|程序|步骤|…」**漏判** ——
#       `vector._split_with_path` 里 `buf_path` 取的是**这一块第一个单元**的路径：
#       短条款会与前一块并进同一个 chunk，于是流程标题**不进 heading_path，只留在正文里**。
#       实测 `### 第十三条 博士后招收程序` 单独成一个 chunk 时 heading_path 是父章，
#       标题文本在 `text` 里。→ 所以判据必须**同时看 heading_path 与正文里的标题行**。
#
#    ② 原判据「正文有序标记 `^[（(]?[一二三…]{1,3}[)）]?[、.．]`」在真语料上**一条都不命中**：
#       本语料的分步写法是 `(一)每学期开学两周内,…` / `(一) 发布通知。` —— 括号后**直接接正文**，
#       没有 `、．` 之类分隔符。按原正则扫「学业预警第七条」「转专业第十六条」全部落空。
#       → 标记正则改为「行首括号数字 **或** 行首 数字/汉字 + 顿号/点」两种形态都收。
#
#    ③ 原判据的 `程序` 会被**培养方案课程名**污染：`程序设计`、`Java语言程序设计实验`、
#       `程序设计与问题求解`、以及毕业要求里的 `…全流程的成本构成`、`工艺流程优化`。
#       实测命中 72 条标题里绝大多数是课程名。→ 三道过滤（见 _looks_like_flow_label）：
#       词邻近 `设计` 的排除（吃掉「程序设计」族）、含 `。` 的排除（整句不是标签）、
#       含 `、` 的必须以流程词收尾（课程列表 / `全周期、全流程` 这类整句）。
#
#    ④ 原判据「正文 ≥3 条有序标记 + ≥2 条含责任方」**不能当流程判据用** ——
#       实测它命中 76 块，绝大多数是职称评审条件的「业绩成果条件」和《章程》的
#       「权利 / 义务」条：那是**条件列举**，不是流程。→ 降级为 `has_clause_list`，
#       只给阶段 1 的抽条件当候选队列，**不参与** `_is_flow_chunk` 的判定。
#
# ⚠️ 全部判据都是**代理判据**：它只负责建候选队列，代码**不据此下结论**。
#    启用前必须人核（铁律 6：关键字定位实测 9/14 找错款）。
#    本轮人核结论见 `_work/tutor/scan_flows.txt` 与本文件末尾的「人核台账」。

import os
import re

# ── 标题判据 ────────────────────────────────────────────────────────────────
# 「工作流程 / 工作程序 / 申请程序 / 出站程序 / 认定流程 / 步骤 / 办理…手续」
_RE_FLOW_HEAD = re.compile(r"流程|步骤|程序|办理.{0,6}手续")
# 「标签以流程词收尾」——用于放行含 `、` 但确实是流程标签的（见 _looks_like_flow_label）
_RE_FLOW_TAIL = re.compile(r"(?:流程|程序|步骤|手续)\s*[:：]?\s*$")

# 课程名污染：`程序设计` / `设计程序` —— 这两个词里出现的 流程/程序 一律不算流程章节。
# 实测污染样例：`(二)主要实践性教学环节:Java程序设计实验…`、`…程序设计、数据结构…`、
# `…设计程序与方法、产品系统设计…`。
_RE_COURSE_NAME = re.compile(r"程序设计|设计程序")

# 条文/节/句编号前缀（`第十三条` `第二节` `五、` `(三)` `1.` `1.1`）——
# 剥掉后再看流程词的位置，免得编号把它们推到 20 字之外。
_RE_ARTICLE_PREFIX = re.compile(
    r"^(?:第[一二三四五六七八九十百\d]{1,4}[条章节]"
    r"|[（(]\s*[一二三四五六七八九十百\d]{1,3}\s*[)）]"
    r"|\d+(?:\.\d+)*|[一二三四五六七八九十]+)\s*[、.．]?\s*"
)

# ── 正文判据（**不是**流程判据，见下方 has_clause_list）────────────────────
# 行首有序标记。★ 两种形态都收（见模块头 ② 的实测）：
#   · `(一)每学期…` / `（一）每学期…`  括号形态，闭括号后直接接正文
#   · `一、申请` / `1. 申请`              顿号/点形态
_RE_ORD = re.compile(
    r"^\s*(?:"
    r"[（(]\s*(?:[一二三四五六七八九十百]{1,3}|\d{1,3})\s*[)）]"
    r"|(?:[一二三四五六七八九十百]{1,3}|\d{1,3})\s*[、.．]"
    r")\s*\S"
)

# 「责任方」模式：分步条款里几乎每条都由某个主体发起动作（教务处/学院/学生…）。
# 用它把「有序列表」与「恰好带编号的普通段落」分开。
_RE_ACTOR = re.compile(
    r"学院|教务处|学生处|人事处|财务处|研究生院|国际合作与交流处|博管办|流动站"
    r"|学位评定|校长办公会|领导小组|学生工作部|培养单位|所在单位|有关部门"
    r"|学校|委员会|中心|办公室|学生本人"
)

# 流程词必须出现在标签**前部**（剥掉条文编号前缀后的前 20 字）。
# 实测用于剔除「整句里顺带提到手续」的条款标题：
#   `第三十七条退学的学生,应在两周内办理退学手续离校,档案、户口退回…`（手续在第 22 字 → 剔除）
# 而真流程标签的流程词都在前 16 字内：
#   `第十三条博士后招收程序`(5) `第七条学业预警工作流程`(5) `第四条 借用教室需办理的手续`(8)
#   `第十条学业退学工作流程学业退学与学业预警工作同期开展,…`(7，单位是条标题粘连正文)
_FLOW_HEAD_POS = 20


def _looks_like_flow_label(s: str) -> bool:
    """一段标题文本是否像「流程 / 程序 / 步骤」章节标签。

    ★ 判据是**按真语料校准**的（见模块头）。三条过滤，各有实测依据：
      ① 去掉条文编号前缀后再看（`五、课程认定流程` 的 `、` 是编号分隔符，不是列举）；
      ② 含 `。` 的一律不是标签 —— 那是被 mdify 写成标题的**整句**（培养方案毕业要求、
         `第三十七条…手续离校,档案、户口退回…` 这类粘连正文的条款）；
      ③ 含 `、` 的必须**以流程词收尾** —— 否则是课程列表（`…程序设计、数据结构…`）
         或 `全周期、全流程的成本构成` 这类整句。
    """
    s = (s or "").strip().lstrip("#").strip()
    if not s:
        return False
    if _RE_COURSE_NAME.search(s):
        return False
    if "。" in s:
        return False
    if "、" in s and not _RE_FLOW_TAIL.search(s):
        return False
    core = _RE_ARTICLE_PREFIX.sub("", s)
    m = _RE_FLOW_HEAD.search(core)
    return bool(m) and m.start() < _FLOW_HEAD_POS


# 独立成行小标签的长度上限（剥掉编号前缀后）。实测三例：
#   `认定程序`(4) `选拔程序`(4) `转专业工作程序`(7)
# 而误判样例都更长：`录取手续及程序等是否合乎国家招生规定;`(18)、
# `除按规定办理了延长退休手续者以外,…的;`(37)、表格行。
_INLINE_MAX_LEN = 12


def _looks_like_inline_label(s: str) -> bool:
    """正文里**独立成行的小标签**（非 `#` 标题），如 `(三)认定程序`。

    ★ 与 `_looks_like_flow_label` 分开是必要的：标题行带整句正文（`第十条学业退学
      工作流程…按以下程序处理:`）照样是流程标题，所以标题判据**不能**要求「短」；
      而正文行里多的是**整句条款**（`1. 除按规定办理了延长退休手续者以外…`），
      不要求短就会把几十条条款正文当标签。→ 正文行必须**又短又以流程词收尾**。
    """
    s = (s or "").strip()
    if not s or s.lstrip().startswith("#"):
        return False
    core = _RE_ARTICLE_PREFIX.sub("", s).strip()
    if not core or len(core) > _INLINE_MAX_LEN:
        return False
    if _RE_COURSE_NAME.search(core):
        return False
    return bool(_RE_FLOW_TAIL.search(core))


def _is_flow_chunk(c: dict) -> bool:
    """这个 chunk 是否属于「流程 / 程序 / 步骤」章节。

    ★ 只用**标签判据**，但标签有三种落点（真语料实测各占一部分）：
      A. `heading_path` 的元素         —— 如 `第三章 学士学位授予程序`
      B. 正文里的 **`#` 标题行**       —— 短条款与前一块并进同一 chunk 时，标题只留在正文里
      C. 正文里的**独立成行小标签**     —— 如大学英语的 `(三)认定程序`、国内交换的
                                          `(二)选拔程序`：它们根本不是 `#` 标题
                                        （实测全语料只有 3 条，全部为真）

      原方案还把「正文 ≥3 条有序标记 + ≥2 条含责任方」OR 进来 —— 实测**不可用**：
      它命中 76 块，绝大多数是**职称评审条件**文件的「业绩成果条件 / 学历(学位)、
      资历条件」以及《章程》的「权利 / 义务」条 —— 那是**条件列举**，不是流程。
      所以那条判据**降级**成 `has_clause_list`，只给阶段 1 的抽条件当候选队列用，
      不参与「是不是流程章节」的判定（见 _work/tutor/scan_flows.txt 的人核结论）。
    """
    path = c.get("heading_path") or []
    text = c.get("text") or c.get("snippet") or ""

    for el in path:
        if _looks_like_flow_label(el):
            return True
    for ln in text.split("\n"):
        # ★ 两条判据**按行型分流**，不能都用宽松的那条：
        #   `#` 标题行 / heading_path 用 `_looks_like_flow_label`（允许带整句正文）；
        #   普通正文行用 `_looks_like_inline_label`（必须又短又以流程词收尾）。
        #   若对正文行也用宽松判据，会把 `1. 除按规定办理了延长退休手续者以外…`
        #   这类**条款正文**全收进来（实测 6 处误判）。
        if ln.lstrip().startswith("#"):
            if _looks_like_flow_label(ln):
                return True
        elif _looks_like_inline_label(ln):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 人核台账（2026-10-08，铁律 6）—— 判据扫 _md/ 90 份的**全部**命中，
# 逐条读原文后确认。结论：**15 处真流程章节 / 13 份文档，零误判、零培养方案污染**。
# （扫描的 section 计数是 16 —— 转专业那节同时被 `第二节 转专业工作程序` 与
#   `第十六条 …工作流程` 两个标签命中，按语义去重后是 15。）
#
#   文档                                     章节
#   ─────────────────────────────────────────────────────────────────────────
#   博士后管理工作实施办法                   第十三条 博士后招收程序
#   博士后管理工作实施办法                   第二十六条 博士后出站程序
#   教职工进修管理办法                       第三章 教职工进修工作流程
#   “大学英语”课程替换及成绩认定办法         (三)认定程序          ← 原方案第八处
#   学士学位授予工作实施细则                 第三章 学士学位授予程序   ← 原方案**未列**
#   专业分流及转专业管理办法                 第二节 转专业工作程序 / 第十六条 工作流程
#   学业预警及学业退学实施细则               第七条 学业预警工作流程
#   学业预警及学业退学实施细则               第十条 学业退学工作流程
#   本科生课程认定暂行办法                   五、课程认定流程        ← 原方案**未列**
#   教室使用管理办法                         第四条 借用教室需办理的手续
#   赴国(境)外交流学习成绩管理办法           第六条 课程认定及学分、成绩记载的基本程序 ← 未列
#   国内交换培养管理办法                     (二)选拔程序            ← 原方案**未列**
#   学生转学工作实施细则                     第三章 工作程序
#   修读微专业、辅修专业、辅修学位管理办法   第三章 修读条件与申报程序 ← 原方案**未列**
#   创新创业拓展课程学分认定管理办法         第四章 认定流程
#
# ★ 对方案假设「真带条件与流程的只有约八处」的**修正**：假设**略低但方向对**——
#   实测 15 处 / 13 份文档。差额全在「原方案未列的 5 处」，且这 5 处都是真流程
#   （学士学位授予程序、课程认定流程、国境外交流课程认定程序、国内交换选拔程序、
#   微专业辅修申报程序）。**2a 的扩展上限（2 份文档 / 6 块）不必改**，
#   因为 2a 只对「检索命中的那几个流程块」扩，不是对全部 15 处扩。
#
# ⚠️ 已知**漏判 1 处**（不修，本轮记着）：大学英语的 `(三)认定程序` 之所以能抓到，
#    是因为它恰好独立成行；若同类子标签与正文粘在同一行（如 `(三)认定程序1.学生须在…`）
#    就会被漏掉。实测本语料只此一例独立成行，其余流程章节都有 `#` 标题或 heading_path。
# ══════════════════════════════════════════════════════════════════════════════


def has_clause_list(c: dict) -> bool:
    """正文是否有**分条列举**（≥3 条有序标记且 ≥2 条含责任方）。

    ⚠️ 这是**代理判据，只建队列**，**不是**「流程章节」的判据 —— 它抓不出流程，
      它抓的是「分条写的条款」。真语料上它命中职称评审条件、章程权利义务等
      几十处**条件列举**，正确用法是给人工核验当候选（铁律 6）。
    """
    text = c.get("text") or c.get("snippet") or ""
    ords = [ln for ln in text.split("\n") if _RE_ORD.match(ln)]
    return len(ords) >= 3 and sum(1 for ln in ords if _RE_ACTOR.search(ln)) >= 2


def flow_reason(c: dict) -> str:
    """命中的是哪条判据（供扫描/调试输出，不参与逻辑）。

    ⚠️ 必须与 `_is_flow_chunk` **同一套分流**，否则报告会显示「宽松判据」的命中，
    看起来像误判（实测：显示 `(一)应征入伍…按有关程序认定…`，实际命中它的
    是同块里的 `第二节 转专业工作程序`）。
    """
    path = c.get("heading_path") or []
    text = c.get("text") or c.get("snippet") or ""
    for el in path:
        if _looks_like_flow_label(el):
            return "path:" + el.strip()[:40]
    for ln in text.split("\n"):
        if ln.lstrip().startswith("#"):
            if _looks_like_flow_label(ln):
                return "head:" + ln.strip().lstrip("#").strip()[:40]
        elif _looks_like_inline_label(ln):
            return "inline:" + ln.strip()[:40]
    if has_clause_list(c):
        return "clause"
    return ""


# ── 章节完整召回（阶段 2a 用；接线在 rag.py）────────────────────────────────
# 开关：优先读 `config.settings.flow_expand`（**单一来源**，A/B 时可复现），
# 读不到再退环境变量 `FLOW_EXPAND=0`，都读不到按默认**开**（铁律 12：失败开放）。
#
# ★ 为什么是函数不是模块常量：模块常量在 import 时定死，`flows` 又被
#   `_work/tutor/*.py`（在 backend 目录外、import 不到 config）引用 ——
#   两处都要能用，所以**调用时**再取，取不到就各自回退。
def flow_expand_enabled() -> bool:
    try:
        from core.config import settings
        return bool(settings.flow_expand)
    except Exception:
        pass
    return os.environ.get("FLOW_EXPAND", "1") not in ("0", "false", "False")

_MAX_DOCS = 2        # 最多补几份文档（方案上限 2 份）
_MAX_ADDED = 6       # 一共最多补几块（方案上限 6）


def expand_flow_sections(chunks: list[dict], *, max_docs: int = _MAX_DOCS,
                         max_added: int = _MAX_ADDED) -> list[dict]:
    """把一个命中流程章节的块，**补齐同节 / 相邻**的兄弟块。

    ★ 为什么需要：`max_chunks_per_doc=2` 的限额 + RRF 名次，会让「第十六条 转专业
      工作流程」只带出 (一)(二) 两步 —— 用户问「能不能转、卡在哪」时，模型看不到
      (六)「公示 5 个工作日、报主管校领导批准」。整节的语义是**不可拆**的。

    只对**命中流程判据**的块做扩展；按 `document_id` 取全文档块（复用
    `vector.get_document_chunks`，走本地 Qdrant，不走 HTTP），只保留与流程块
    **heading_path 相邻或同节**的块。总量上限 max_added，避免把上下文窗口撑爆。

    失败开放：取不到文档块时原样返回（宁可不扩，不可报错）。
    """
    if not flow_expand_enabled() or not chunks:
        return chunks
    try:
        from agent.tools.search_regulations.scripts.index_ops import indexing
    except Exception:
        return chunks

    out = list(chunks)
    have = {(c.get("document_id"), c.get("chunk_idx")) for c in out}
    picked: list[dict] = []
    docs_used = 0

    for c in chunks:
        if len(picked) >= max_added or docs_used >= max_docs:
            break
        if not _is_flow_chunk(c):
            continue
        did = c.get("document_id")
        if did is None:
            continue
        try:
            sibs = indexing.get_document_chunks(did)
        except Exception:
            continue
        if not sibs:
            continue
        docs_used += 1
        # 同节 / 相邻：heading_path 完全相同的兄弟，或 chunk_idx 相邻的块
        base = tuple(c.get("heading_path") or [])
        idx = c.get("chunk_idx")
        for s in sibs:
            if len(picked) >= max_added:
                break
            key = (did, s.get("chunk_idx"))
            if key in have:
                continue
            same_sec = base and tuple(s.get("heading_path") or []) == base
            adjacent = idx is not None and abs((s.get("chunk_idx") or 0) - idx) <= 1
            if c.get("chunking_version"):
                # Structural chunks carry explicit boundaries: adjacency alone
                # must not pull in a different article or table.
                related = (same_sec and all(c.get(k) and s.get(k) == c[k] for k in
                           ("source_sha256", "chunking_version", "parent_id", "section_parent_id")))
            else:
                related = same_sec or adjacent
            if not related:
                continue
            have.add(key)
            picked.append(dict(s, **{
                "document_id": did,
                "chunk_idx": s.get("chunk_idx"),
                "source": s.get("source", c.get("source", "")),
                "snippet": s.get("text", ""),
                "text": s.get("text", ""),
                "heading_path": s.get("heading_path") or [],
                "block_type": s.get("block_type") or "",
                "score": c.get("score"),
                "rrf": c.get("rrf"),
                "flow_expanded": True,
            }))
    return out + picked
