# -*- coding: utf-8 -*-
"""S1：证据覆盖信号 —— 让模型看见"还缺什么"，而不是只看见"检索成功"。

背景（2026-10-20 实测）：reg-10b 只调 1 次 search_regulations 就收尾，
因为 gate 报 supported_high（best_rerank 0.9935）。但 gate 衡量的是
**文档相关性**，不是**问题是否被回答**。模型拿不到"缺什么"的信号，
就没有理由再查一次 —— 这就是 ReAct 循环提前终止的根因。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.tools.search_regulations.scripts.answer_coverage import (
    coverage_signal, QUESTION_STOPWORDS,
)


def _blocks(*texts):
    return [{"source": "某办法", "heading_path": ["某办法"], "text": t} for t in texts]


def test_fully_covered_question_reports_no_missing_terms():
    sig = coverage_signal("延缓派遣的期限是多久", _blocks("延缓派遣期限为两年"))
    assert sig["coverage_ratio"] == 1.0
    assert sig["missing_terms"] == []


def test_partially_covered_question_lists_the_missing_terms():
    """reg-10b 形状：问的是"延期"和"转国家"两件事，证据只答了其一。"""
    sig = coverage_signal(
        "国境外交流期间能否自行延期或转去另一个国家学习",
        _blocks("交流期满后应按时回校，不得擅自延长或转往其他地区或国家"))
    assert 0.0 < sig["coverage_ratio"] < 1.0
    assert sig["missing_terms"], "未覆盖的词必须被点名，否则模型无从判断该不该再查"


def test_no_blocks_reports_zero_coverage_and_does_not_raise():
    sig = coverage_signal("宿舍几点熄灯", [])
    assert sig["coverage_ratio"] == 0.0
    assert sig["covered_terms"] == []


def test_stopwords_are_not_counted_as_missing():
    """疑问词/虚词不该进 missing，否则每一题都会"缺东西"，信号就废了。"""
    sig = coverage_signal("请问这个怎么办理", _blocks("办理流程如下"))
    for word in ("请问", "这个", "怎么"):
        assert word not in sig["missing_terms"]
    assert "请问" in QUESTION_STOPWORDS


def test_signal_is_json_serialisable_and_bounded():
    """必须能直接进 ToolResult.data 并写进 JSON 落盘。"""
    import json
    sig = coverage_signal("国境外交流延期", _blocks("不得擅自延长"))
    assert set(sig) == {"coverage_ratio", "covered_terms", "missing_terms", "question_terms"}
    assert 0.0 <= sig["coverage_ratio"] <= 1.0
    json.dumps(sig, ensure_ascii=False)


def test_missing_terms_are_capped_so_observation_does_not_explode():
    """观测文本要短。缺词最多给 5 个。"""
    long_q = " ".join("术语%d" % i for i in range(30))
    sig = coverage_signal(long_q, _blocks("无关内容"))
    assert len(sig["missing_terms"]) <= 5
