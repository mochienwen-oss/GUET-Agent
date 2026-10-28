# -*- coding: utf-8 -*-
"""金标准证据必须落在**唯一一个块**内（阶段5A 裁决 四）。

为什么：旧索引时期 `B-004`/`B-007` 的证据串跨块，`diagnose_case` 的整串包含检查
在**有序拼接**的最终上下文上做的，跨块必然判失败 —— 失败标签因此失真。
改成"逐条独立证据项 + 每条落在单块内"之后，跨块不再是问题。

15 条 `query_course` 已标 `pending_phase5b`（裁决 四.5），不在本测试范围。
本测试只用**纯切分函数**，不连 Qdrant、不加载模型。
"""
import os
import re
import unicodedata
from pathlib import Path

import pytest

from agent.tools.search_regulations.scripts import corpus_fingerprint as cf
from agent.tools.search_regulations.scripts import diagnostics, indexing
from agent.tools.search_regulations.scripts.gold import load_gold

ROOT = Path(__file__).resolve().parents[5]
MD = ROOT / "_md"
GOLD = ROOT / "测试集" / "retrieval_gold.jsonl"

pytestmark = pytest.mark.skipif(
    not MD.is_dir() or not GOLD.is_file(),
    reason="需要本地 _md/ 与 retrieval_gold.jsonl",
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


@pytest.fixture(scope="module")
def corpus() -> dict:
    """{文档标题: [块文本, ...]}，读法与入库一致。"""
    out = {}
    for path in cf.iter_markdown(MD):
        title = os.path.splitext(os.path.basename(path))[0]
        out[title] = [u["text"] for u in
                      indexing._build_index_units(cf.read_markdown(path), "")]
    return out


def _blocks_for(row: dict, corpus: dict) -> list[str]:
    return [text for title, units in corpus.items()
            if any(diagnostics.same_doc(doc, title) for doc in row["expected_docs"])
            for text in units]


def test_every_regulation_evidence_item_sits_in_exactly_one_block(corpus):
    offenders = []
    for row in load_gold(GOLD):
        if not row["answerable"] or row["expected_tool"] != "search_regulations":
            continue
        blocks = _blocks_for(row, corpus)
        for item in row["expected_evidence"]:
            holders = [i for i, text in enumerate(blocks) if _norm(item) in _norm(text)]
            if len(holders) != 1:
                offenders.append((row["id"], len(holders), item[:48]))
    assert not offenders, "证据项没有恰好落在一个块内：%s" % offenders


def test_pending_phase5b_rows_keep_their_evidence_untouched(corpus):
    """裁决 四.5：15 条 `query_course` 只打标记，**不重录**。"""
    rows = load_gold(GOLD)
    pending = [r for r in rows if r.get("pending_phase5b")]
    assert len(pending) == 15
    assert all(r["expected_tool"] == "query_course" for r in pending)
    assert all(r["expected_evidence"] for r in pending)


def test_negatives_carry_no_evidence(corpus):
    """14 条负例不得因为迁移而带上 docs/evidence。"""
    negatives = [r for r in load_gold(GOLD) if not r["answerable"]]
    assert len(negatives) == 14
    assert all(not r["expected_docs"] and not r["expected_evidence"] for r in negatives)