# tests/test_rag_dedupe.py —— 跨文档**近似**折叠（rag._dedupe_near）的单测
#
# 背景（2026-10-03 实测）：`_dedupe_by_text` 只认**逐字相等**，而 65 份培养方案是
# 同一套模板生成的 —— 同一段学分说明在各方案里只差几个专业名和数字，逐字不相等，
# 于是每份各占一个候选位：全库 3393 个向量里 85% 来自培养方案。
# 后果是 318 题集当天 5 道未命中里的 4 道，top-6 全是各方案的同一段模板文字，
# 而正确的规章制度文档被挡在召回池第 93/73/33/15 名（精排窗口只有 40）。
# 这是个**纯函数**，所以能脱离 Qdrant / 精排单测。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tools.search_regulations import tool as rag  # noqa: E402


def _plan_chunk(i: int, *, rrf: float) -> dict:
    """造一段"同模板、只差几个数字"的培养方案块（贴近语料真实形态：几百字）。"""
    body = ("毕业总学分不低于170学分。其中:通识必修课程46学分,通识选修课程5学分,"
            "学科基础课程%d学分,专业核心课程%d学分,集中性实践环节%d学分。"
            "各模块学分要求详见下表。") % (20 + i, 10 + i, 30 + i)
    body += "说明:专业培养方案在实际运行中会存在微调的情况,最终解释权归属本学院专业负责人与教学办。" * 4
    return {"document_id": 1000 + i, "chunk_idx": 1,
            "source": f"2025级某某{i}专业培养方案(主修)", "text": body, "rrf": rrf}


def test_threshold_zero_is_passthrough():
    """0 = 关闭（改前的默认行为）—— 顺序与内容都不许动。"""
    ch = [_plan_chunk(1, rrf=0.03), _plan_chunk(2, rrf=0.02)]
    assert rag.dedupe_near(ch, 0.0) == ch
    assert rag.dedupe_near(ch, 0) == ch


def test_collapses_template_copies_across_documents():
    """只差几个数字的模板复制体必须折叠成一条 —— 这正是逐字去重抓不到的那一类。"""
    ch = [_plan_chunk(i, rrf=0.03 - i * 0.001) for i in range(6)]
    got = rag.dedupe_near(ch, 0.6)
    assert len(got) == 1, [c["source"] for c in got]
    # 保留的是 RRF 名次最高的那条
    assert got[0]["document_id"] == 1000


def test_keeps_genuinely_different_blocks():
    """别为了去重把正常内容吃掉：话题不同的块一条都不许少。"""
    a = _plan_chunk(1, rrf=0.03)
    b = {"document_id": 2001, "chunk_idx": 3, "source": "桂林电子科技大学考试管理规定",
         "text": "第十条 学生因故不能参加考试的,应当事先向所在学院提出缓考申请,"
                 "经学院审核同意后报教务处备案。缓考一般不单独安排,随下一次考试进行。",
         "rrf": 0.02}
    got = rag.dedupe_near([a, b], 0.6)
    assert len(got) == 2


def test_output_is_rrf_ordered():
    """折叠后仍按 RRF 名次降序 —— 后续 `[:rerank_pool]` 截断依赖这个顺序。"""
    a = _plan_chunk(1, rrf=0.010)
    b = {"document_id": 2001, "chunk_idx": 3, "source": "桂林电子科技大学考试管理规定",
         "text": "缓考一般不单独安排,随下一次考试进行。补考成绩按卷面成绩记载。",
         "rrf": 0.090}
    got = rag.dedupe_near([a, b], 0.6)
    assert [c["document_id"] for c in got] == [2001, 1001]


def test_empty_and_short_text_survive():
    """空/极短文本进不了 shingle 集合，但**不能被丢掉**（丢了就等于静默少内容）。"""
    ch = [{"document_id": 1, "chunk_idx": 1, "text": "", "rrf": 0.1},
          {"document_id": 2, "chunk_idx": 1, "text": "短。", "rrf": 0.2}]
    assert len(rag.dedupe_near(ch, 0.6)) == 2
