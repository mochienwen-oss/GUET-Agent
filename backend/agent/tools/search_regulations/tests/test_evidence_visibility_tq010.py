# -*- coding: utf-8 -*-
"""tq-010「拿学士学位对学分绩有要求吗？」的证据可见性防回归。

三层全部离线，不连 Qdrant、不加载模型、不调 LLM：

  1. **源文**   `_md/规章制度/培养方案管理制度/《…学士学位授予工作实施细则…》.md`
  2. **切块**   `search_regulations.tool.build_index_units`（纯函数）
  3. **观察渲染** `agent.runtime.registry.render` → `search_regulations.renderer.render`（纯函数）

★ 本文件只锁「证据里到底有没有那句话」，**不判回答对错**。回答是否越过证据，仍由
  pending 场景的 rubric 与**人工逐主张审核**负责（见
  `_work/phase6/tests/test_tq010_qualification_invariants.py`）。
  这里刻意不写任何关键词打分器 —— 那会变成一个自动语义放行器。

★ 每条断言先证明自己读到了真实输入（源文非空且含可判定锚点、切块非空）。
  一个把文件读成空串的门会安静地全绿。

Run:
  D:\\venv-guet-ppt\\Scripts\\python.exe -m pytest \
    backend/agent/tools/search_regulations/tests/test_evidence_visibility_tq010.py -q
"""
import re
from pathlib import Path

import pytest

from agent.core import ToolResult
from agent.runtime import registry
from agent.tools.search_regulations import tool as search_tool


BACKEND = Path(__file__).resolve().parents[4]
PROJECT = BACKEND.parent
CORPUS_MD = (PROJECT / "_md" / "规章制度" / "培养方案管理制度"
             / "《桂林电子科技大学普通高等教育学士学位授予工作实施细则(2025年修订)》"
               "(桂电学位〔2025〕19号).md")

#: 学士学位授予条件的学分绩门槛（第四条(三)）—— 逐字，半角括号与分号。
GRANT_CLAUSE = "(三)课程学习水平与质量综合评价指标(学分绩)达到70"
#: 第五条整句的头与尾。中间任何一刀都会把「条件」与「后果」切散。
ARTICLE5_HEAD = "### 第五条 达到毕业条件但学分绩未达到 70 的学生，在校学习期间若参加"
ARTICLE5_CLAUSE = "达到毕业条件但学分绩未达到 70 的学生，在校学习期间"
ARTICLE5_TAIL = "可在计算学分绩时给予加分。"

#: 修复前的**截断标题**形态（实测取自修复前的语料快照
#: `训练集/_work/eval/_md_new/…/《…学士学位授予工作实施细则…》.md`）：
#: 第五条标题句被拦腰切开，前半句并进了上一条块的尾巴。
MANGLED_ARTICLE5 = "### 第五条期间若参加"

#: 修复前的源文片段（逐字复制自上述快照，仅保留缺陷所涉的两行）—— 用作负向对照。
PREFIX_SNIPPET = (
    "第四条符合下列条件的普通高等教育本科毕业生(含专科起点)可授予学士学位:\n\n"
    "(三)课程学习水平与质量综合评价指标(学分绩)达到70;\n\n"
    "达到毕业条件但学分绩未达到70 的学生,在校学习\n\n"
    "### 第五条期间若参加《桂林电子科技大学本科教学奖励学科竞赛目录》中的竞赛项目"
    "并获奖或获得发明专利授权,可在计算学分绩时给予加分。\n"
)


def _article5_line(text: str) -> str:
    """源文里第五条那一整行（`### 第五条 …`）。"""
    return next(line for line in text.splitlines() if line.startswith("### 第五条"))


def visibility_violations(text: str) -> list[str]:
    """在**源文文本**上判 tq-010 的证据可见性；返回违反项（空 = 通过）。

    纯函数，可对任意语料文本求值 —— 这是「门看得见输入」的判据本体。
    """
    problems = []
    if GRANT_CLAUSE not in text:
        problems.append("缺少学分绩门槛原文：%s" % GRANT_CLAUSE)
    if MANGLED_ARTICLE5 in text:
        problems.append("第五条标题被截断：%r" % MANGLED_ARTICLE5)
    line = _article5_line(text)
    if not line.startswith(ARTICLE5_HEAD):
        problems.append("第五条不是一条完整句（头部不符）：%r" % line[:40])
    if not line.endswith(ARTICLE5_TAIL):
        problems.append("第五条不是一条完整句（尾部不符）：%r" % line[-20:])
    return problems


# ================================================================ 负向对照：门看得见缺陷

def test_the_gate_reports_the_prefix_defect_it_is_meant_to_catch():
    """对**修复前**的片段求值必须报违规，且报的正是那两处。"""
    problems = visibility_violations(PREFIX_SNIPPET)
    assert problems, "门对已知缺陷视而不见 —— 这一层等于没测"
    assert any("第五条标题被截断" in item for item in problems), problems
    assert any("第五条不是一条完整句（头部不符）" in item for item in problems), problems
    # 门槛原文本身在修复前就在（缺陷是切散与截断，不是缺句子）—— 一并钉住，免得把两件事混为一谈
    assert not any("缺少学分绩门槛原文" in item for item in problems), problems


def test_the_gate_reports_a_missing_threshold():
    problems = visibility_violations(PREFIX_SNIPPET.replace(GRANT_CLAUSE, ""))
    assert any("缺少学分绩门槛原文" in item for item in problems), problems


# ================================================================ L1 源文层

def test_the_corpus_file_is_present_and_non_trivial():
    assert CORPUS_MD.is_file(), "语料文件缺失：%s" % CORPUS_MD
    text = CORPUS_MD.read_text(encoding="utf-8")
    assert len(text) > 2000, "语料文件短得不像真实规章：%d 字符" % len(text)
    assert text.lstrip().startswith("#"), "读到的不是 markdown 正文"


def test_source_carries_the_gpa_threshold_verbatim():
    text = CORPUS_MD.read_text(encoding="utf-8")
    assert GRANT_CLAUSE in text
    assert "第四条" in text and "学分绩" in text


def test_source_article5_is_one_complete_sentence():
    line = _article5_line(CORPUS_MD.read_text(encoding="utf-8"))
    assert line.startswith(ARTICLE5_HEAD), line[:60]
    assert line.endswith(ARTICLE5_TAIL), line[-30:]
    assert ARTICLE5_CLAUSE in line


def test_source_has_no_violations():
    assert visibility_violations(CORPUS_MD.read_text(encoding="utf-8")) == []


# ================================================================ L2 切块层

def _units():
    return search_tool.build_index_units(CORPUS_MD.read_text(encoding="utf-8"),
                                         "规章制度")


def test_chunking_keeps_the_threshold_in_one_block():
    """门槛原文必须落在**同一块**里，且该块归属第四条。"""
    hits = [unit for unit in _units() if GRANT_CLAUSE in unit["text"]]
    assert len(hits) == 1, "命中 %d 块：%s" % (len(hits), [u["chunk_idx"] for u in hits])
    assert (hits[0]["heading_path"] or [""])[-1].startswith("第四条")


def test_chunking_keeps_article5_whole():
    """第五条的条件从句与后果必须在**同一块**里，标题路径也必须是完整句。

    修复前：条件从句 `达到毕业条件但学分绩未达到70 的学生,在校学习` 被留在上一块的
    尾部，第五条块只剩 `### 第五条期间若参加…给予加分。` —— 检索渲染出来就是一问
    半句，条件与后果隔着块边界。
    """
    units = _units()
    holders = [unit for unit in units if unit["text"].strip().startswith(ARTICLE5_HEAD)]
    assert len(holders) == 1, [u["chunk_idx"] for u in holders]
    holder = holders[0]
    assert ARTICLE5_TAIL.strip("。") in holder["text"]
    path_tail = (holder["heading_path"] or [""])[-1]
    assert path_tail.startswith("第五条"), path_tail[:40]
    assert ARTICLE5_CLAUSE in path_tail, path_tail[:60]


def test_no_block_is_left_carrying_a_truncated_article5_heading():
    texts = [unit["text"] for unit in _units()]
    assert texts, "切块返回空 —— 断言没有输入可看"
    assert not [t for t in texts if MANGLED_ARTICLE5 in t]


# ================================================================ L3 观察渲染层

def _result(blocks, *, status="success"):
    return ToolResult(tool="search_regulations", ok=True, status=status,
                      data={"query": "拿学士学位对学分绩有要求吗？", "blocks": list(blocks)},
                      sources=[])


def _blocks_from_corpus():
    """把切出来的两块（门槛块 + 第五条块）包成检索结果里的 block 形状。"""
    units = _units()
    picked = [unit for unit in units
              if GRANT_CLAUSE in unit["text"] or unit["text"].strip().startswith(ARTICLE5_HEAD)]
    assert len(picked) == 2, [u["chunk_idx"] for u in picked]
    return [{"source": "《桂林电子科技大学普通高等教育学士学位授予工作实施细则(2025年修订)》"
                       "(桂电学位〔2025〕19号)",
             "heading_path": unit["heading_path"], "text": unit["text"],
             "document_id": 1983967568, "chunk_idx": unit["chunk_idx"]}
            for unit in picked]


def test_render_passes_the_threshold_and_the_consequence_through_verbatim():
    out = registry.render(_result(_blocks_from_corpus()))
    assert GRANT_CLAUSE in out
    assert ARTICLE5_HEAD in out
    assert ARTICLE5_TAIL in out


def test_render_adds_nothing_beyond_headers():
    """渲染只许加 `[n]《标题》 路径` 头；正文必须与块原文逐字一致。"""
    blocks = _blocks_from_corpus()
    out = registry.render(_result(blocks))
    body = re.sub(r"^\[\d+\]《[^\n]*\n", "", out, flags=re.M).strip()
    assert body == "\n\n".join(block["text"].strip() for block in blocks)


def test_render_of_an_incomplete_context_invents_no_threshold():
    """**不完整**的上下文里没有门槛与后果，渲染就一个字都不许补。"""
    thin = [{"source": "《某规定》", "heading_path": ["第一章"], "text": "第一条 本条正文。"}]
    out = registry.render(_result(thin))
    for token in (GRANT_CLAUSE, "学分绩", "达到70", "不低于", "加分", "70"):
        assert token not in out, "渲染凭空补出了 %r：%s" % (token, out)


def test_render_of_a_thin_result_adds_no_substance():
    assert registry.render(_result([])) == "未检索到相关资料。"
    out = registry.render(_result([], status="irrelevant_result"))
    assert not re.search(r"\d", out), out


def test_render_never_leaks_retrieval_internals():
    out = registry.render(_result(_blocks_from_corpus()))
    for token in ("rerank", "score", "reason", "tier", "chunk_idx"):
        assert token not in out


@pytest.mark.parametrize("token", ["学分绩", "70", "加分"])
def test_render_only_echoes_what_the_blocks_carry(token):
    """每个探针 token：出现在渲染里 ⟺ 出现在输入块里。"""
    present = registry.render(_result(_blocks_from_corpus()))
    absent = registry.render(_result([{"source": "《某规定》", "heading_path": [],
                                       "text": "第一条 本条正文。"}]))
    assert token in present
    assert token not in absent