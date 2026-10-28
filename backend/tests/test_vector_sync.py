# tests/test_vector_sync.py —— 灌库对账（bug③）里那个纯函数的单测
#
# 背景：`scripts/ingest_local.py` 直接调 `vector._index_document()` 写 Qdrant，
# **从不写 MySQL**，而后台的文档列表读的正是 MySQL —— 于是灌完库只看得见向量数、
# 看不见任何文档。`vector.sync_documents()` 就是补这一步的对账；
# 其中唯一能脱离 Qdrant/MySQL 单测的，是「把分块拼回全文」这一步。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tools.search_regulations.scripts import indexing as V  # noqa: E402


def test_join_chunk_texts_removes_seam_overlap():
    """★ 块之间有重叠（`split_text` 的 overlap=50 与 `recursive_split` 的接缝），
    直接首尾相接会把接缝复制一遍 —— 拼出来的"全文"回填进 `Document.content` 后，
    一旦拿它重建索引，重复的接缝就会被切成额外的块。"""
    a = "第一章 总则。本规定适用于全校在籍本科生。"
    b = "适用于全校在籍本科生。具体事项由教务处负责解释。"
    assert V._join_chunk_texts([a, b]) == a + "具体事项由教务处负责解释。"
    assert V._join_chunk_texts(["完整的一段话。"]) == "完整的一段话。"
    assert V._join_chunk_texts([]) == ""
    assert V._join_chunk_texts(["", "abc"]) == "abc"


def test_join_chunk_texts_ignores_coincidental_short_overlap():
    """**前后块恰好都以同一个字收尾/开头，不是接缝。**

    分块器的契约是「自然断点处 0% 重叠、只有字符硬切才叠 overlap」（见 _split_with_path），
    所以 1~4 个字的"重叠"只能是巧合 —— 实测《2025级自动化专业培养方案》99 条接缝里
    有 82 条如此，多半是两块都以换行开头。旧实现写的是 `while k > 0`，会照着这些巧合
    **静默删掉真实的字符**；而 `content` 正是"重建索引"要用的那一份，删掉了没人会报错。
    """
    # 两块都以换行开头/结尾：那是排版，不是接缝
    assert V._join_chunk_texts(["第一段结束。\n", "\n第二段开始。"]) == "第一段结束。\n\n第二段开始。"
    # 恰好都以「的」收尾/开头同理
    assert V._join_chunk_texts(["…的", "的补充说明。"]) == "…的的补充说明。"


def test_join_chunk_texts_without_overlap_is_plain_concatenation():
    """没有重叠时就该是普通拼接 —— 别为了去重把正常内容吃掉。"""
    assert V._join_chunk_texts(["第一段。", "第二段。"]) == "第一段。第二段。"


def test_join_chunk_texts_handles_realistic_overlap():
    """按实际参数形态走一遍：后块以前块的**结尾若干字**开头。"""
    a = "转专业需要学生提出申请，经所在学院同意后报教务处审核。"
    b = "经所在学院同意后报教务处审核。审核通过后办理学籍异动手续。"
    joined = V._join_chunk_texts([a, b])
    assert joined == a + "审核通过后办理学籍异动手续。"
    assert joined.count("报教务处审核") == 1        # 接缝没有被复制
