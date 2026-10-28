# tests/agent/test_core_evidence.py —— 证据身份与台账
#
# ★ 本文件里最重要的一条是 `test_same_section_different_chunks_are_not_merged`：
#   阶段0.5 §1.2 明令 **禁止只按 `doc_id + heading_path` 去重** ——
#   同一章节下有**多个块**，那样去重会把不同的块误合并成一条，证据直接错。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.core import EvidenceLedger  # noqa: E402
from agent.core import evidence as ev  # noqa: E402
from agent.core.contracts import Source  # noqa: E402


def _src(text="", title="", hp=None, line=None, chunk_id=None):
    return Source(chunk_id=chunk_id, title=title, heading_path=hp or [], text=text,
                  text_hash=ev.text_hash(text), line=line)


def test_text_hash_is_deterministic_and_ignores_surrounding_space():
    assert ev.text_hash("正文") == ev.text_hash("  正文\n")


def test_chunk_id_of_requires_both_parts():
    assert ev.chunk_id_of(123, 4) == "123:4"
    assert ev.chunk_id_of(123, None) is None
    assert ev.chunk_id_of(None, 4) is None


def test_identity_prefers_chunk_id_then_text_hash():
    assert ev.identity(_src(text="x", chunk_id="7:1")).startswith("c:")
    assert ev.identity(_src(text="x")).startswith("t:")


def test_sources_without_text_do_not_collapse_to_one_identity():
    """没有正文的来源（如培养方案的 `{file, line}`）**不能**都塌成同一个身份键。

    否则同一份方案的两条不同出处会被当成同一条证据 —— 静默丢证据。
    """
    a = _src(title="A专业培养方案.md", line=10)
    b = _src(title="A专业培养方案.md", line=99)
    assert ev.identity(a) != ev.identity(b)


def test_same_section_different_chunks_are_not_merged():
    """★ 同 `doc_id` + 同 `heading_path`，但**不同块** → 必须保留两条。"""
    hp = ["第三章 转专业的条件与程序", "第十二条"]
    c1 = Source(doc_id="2001", chunk_id="2001:18", title="转专业办法",
                heading_path=hp, text="第十二条 可以申请…", text_hash=ev.text_hash("第十二条 可以申请…"))
    c2 = Source(doc_id="2001", chunk_id="2001:19", title="转专业办法",
                heading_path=hp, text="第十三条 不予申请…", text_hash=ev.text_hash("第十三条 不予申请…"))
    led = EvidenceLedger()
    led.add("search_regulations", {"query": "转专业"}, c1)
    led.add("search_regulations", {"query": "转专业"}, c2)
    assert len(led) == 2, "同章节的两个块被误合并了 —— 这正是被明令禁止的去重方式"


def test_same_section_different_text_without_chunk_id_also_survives():
    """没有 chunk_id 时，靠 text_hash 也要能把同章节的不同块分开。"""
    hp = ["第五章"]
    led = EvidenceLedger()
    led.add("search_regulations", {"query": "q"}, _src(text="正文甲", hp=hp))
    led.add("search_regulations", {"query": "q"}, _src(text="正文乙", hp=hp))
    assert len(led) == 2


def test_dedup_same_source_twice_adds_once():
    """同来源重复加入不新增条目 —— 这是「重跑不新增证据」的落点。"""
    led = EvidenceLedger()
    s = _src(text="同一段正文", hp=["第一章"])
    assert led.add("search_regulations", {"query": "q"}, s) is not None
    assert led.add("search_regulations", {"query": "q"}, s) is None
    assert len(led) == 1


def test_different_args_are_different_evidence():
    """参数不同 → 证据不同（同一段正文用两种问法查到，是两条独立的取证）。"""
    led = EvidenceLedger()
    s = _src(text="同一段正文")
    led.add("search_regulations", {"query": "问法甲"}, s)
    led.add("search_regulations", {"query": "问法乙"}, s)
    assert len(led) == 2


def test_evidence_id_is_stable():
    s = _src(text="正文")
    a = ev.evidence_id("search_regulations", ev.args_hash({"query": "q"}), s)
    b = ev.evidence_id("search_regulations", ev.args_hash({"query": "q"}), s)
    assert a == b and len(a) == 12


def test_add_many_returns_only_newly_added():
    led = EvidenceLedger()
    s1, s2 = _src(text="甲"), _src(text="乙")
    assert len(led.add_many("search_regulations", {"query": "q"}, [s1, s2])) == 2
    assert len(led.add_many("search_regulations", {"query": "q"}, [s1, s2])) == 0
    assert len(led) == 2


def test_ledger_lookup_by_id():
    led = EvidenceLedger()
    ev1 = led.add("search_regulations", {"query": "q"}, _src(text="正文"))
    assert ev1 is not None
    assert led.get(ev1.evidence_id) is ev1
    assert ev1.evidence_id in led
