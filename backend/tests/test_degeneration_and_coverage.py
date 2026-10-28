# -*- coding: utf-8 -*-
"""两个新判据的单测：重复退化护栏（citations）与覆盖面判空（coverage）。

它们都是**纯函数**，所以能脱离 LLM / Qdrant / 网络单测 —— 这正是把逻辑从
`rag.retrieve` 与 `chat.py` 里搬出来的原因（在路由里就没法这么测）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.core import citations  # noqa: E402
from agent.tools.search_regulations.scripts import coverage  # noqa: E402


# ---------------------------------------------------------------- 重复退化
def test_no_degeneration_on_normal_text():
    t = "补考安排在下一学期第一、二周。体育课补考在第一周周六。具体以开课单位通知为准哈～"
    assert citations.find_degeneration(t) == -1


def test_two_fold_repeat_is_not_degeneration():
    """**两次**重复不判 —— 排比/对仗/表格相邻行相同在中文里很常见，判了就是误伤正文。"""
    blk = "具体时间以教务处通知为准，不要以去年的安排来估今年。"
    assert citations.find_degeneration(blk + blk) == -1


def test_three_fold_repeat_is_cut_at_first_occurrence():
    """复读段只保留**第一次出现**，后面几次全切掉。

    （实现上最容易错的一步：只看"是否重复 2 次"会保留倒数第一次之前的全部内容 ——
    5 次复读时会留下 2 份。这个单测就是在守这一点。）
    """
    head = "所以具体怎么排最稳妥，还是得问下咱们学院的教学秘书或者班主任哦～"
    blk = "所以具体怎么排最稳妥，还是得问下咱们学院的教学秘书或者班主任，问他们比问我靠谱多了～"
    t = head + blk * 5
    cut = citations.find_degeneration(t)
    assert cut == len(head) + len(blk), "应保留第一次出现（head + 一份 blk）"
    out, ok = citations.strip_degeneration(t)
    assert ok and out == head + blk


def test_repeated_punctuation_run_is_caught():
    """实测出现的退化简写形态：整行破折号 / 波浪号刷屏。"""
    t = "教室使用管理办法如下：" + "-" * 200
    assert citations.find_degeneration(t) >= 0


def test_short_text_never_triggers():
    assert citations.find_degeneration("嗯嗯嗯嗯嗯嗯") == -1


# ---------------------------------------------------------------- 覆盖面判空
def _idx():
    return {"names": {coverage._norm("桂林电子科技大学章程（2023年核准稿）"),
                      coverage._norm("桂电教〔2026〕13号--关于印发《桂林电子科技大学创新创业拓展课程学分认定管理办法》的通知")},
            "docnos": {"2026-13"}}


def test_present_doc_is_not_flagged():
    """本校文件名要能对上 —— 带括号后缀(`（2023年核准稿）`)不能算"不存在"。"""
    assert coverage.absent_reference("《桂林电子科技大学章程》里关于学生团体是怎么规定的？", _idx()) == ""


def test_absent_doc_is_flagged():
    q = "《桂林电子科技大学学生宿舍空调使用管理规定》里空调温度限制是多少？"
    assert coverage.absent_reference(q, _idx()) == "《桂林电子科技大学学生宿舍空调使用管理规定》"


def test_present_docno_is_not_flagged():
    assert coverage.absent_reference("桂电教〔2026〕13号文件规定了什么？", _idx()) == ""


def test_absent_docno_is_flagged():
    assert coverage.absent_reference("桂电教〔2099〕1号文件规定了什么？", _idx()) == "桂电教〔2099〕1号"


def test_course_name_in_wrap_is_not_a_doc_ref():
    """《机械设计》是**课程名**不是文档名：语料里没有同样形态的名字时不该据此判空。

    这条守的是"宁可漏拦不可误拦" —— 课程名/书名号里的专业术语非常常见。
    """
    idx = _idx()
    idx["names"].add(coverage._norm("2025级机械设计制造及其自动化专业培养方案(主修)"))
    assert coverage.absent_reference("帮我写一篇《机械设计》课程论文。", idx) == ""


def test_course_name_without_doc_suffix_is_never_flagged():
    """回归：实测误伤案例 `ref-C-010`。

    题面「想在第5学期一次选上《问卷设计与抽样调查》《最优化方法(双语教学)》
    《数据挖掘及其应用》，这三门会撞学期吗？」—— 三个都是**课程名**，
    语料里当然没有这几份"文档"，但答案就在该专业培养方案里。
    靠**公文尾词**把课程名挡在外面（课程名不会以「办法/规定/…」收尾）。
    """
    idx = _idx()
    q = ("我是应用统计学专业的，想在第5学期一次选上《问卷设计与抽样调查》"
         "《最优化方法(双语教学)》和《数据挖掘及其应用》，这三门会撞学期吗？")
    assert coverage.absent_reference(q, idx) == ""


def test_doc_like_name_without_doc_suffix_still_not_flagged():
    """带公文尾词的才判；不带的一律放行（哪怕它确实不在语料里）。"""
    idx = _idx()
    assert coverage.absent_reference("《某某工作条例》怎么规定的？", idx) != ""
    assert coverage.absent_reference("《某某工作报告》怎么规定的？", idx) == ""
