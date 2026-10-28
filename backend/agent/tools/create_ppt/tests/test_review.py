"""`routers/ppt.py::_review` —— 审阅投影的形状。

★ 这里是**人工确认前**用户唯一能看到的东西，所以投影必须：
  · 只给出**被引用到**的来源（不是把整篇证据倒出去）；
  · 正文截断，避免一次响应把整本语料塞进去；
  · 脏数据不炸 —— state.json 是磁盘上的外部数据。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from routers.ppt import _review  # noqa: E402


def _state():
    return {
        "slides": [
            {"kind": "cover", "title": "封面", "claims": [], "notes": []},
            {"kind": "bullets", "title": "依据与缺口",
             "claims": [{"text": "有据的一条", "evidence_ids": ["aaa"], "gap": None},
                        {"text": "没查到的一条", "evidence_ids": [], "gap": "本次未找到"}],
             "notes": [{"text": "备注里的事实", "evidence_ids": ["aaa"], "gap": None}]},
        ],
        "evidence": {
            "aaa": {"title": "某办法", "heading_path": ["第三章"], "line": 12,
                    "text": "正文" * 500},
            "bbb": {"title": "没人引用的来源", "text": "不该出现"},
        },
    }


def test_only_referenced_sources_are_projected():
    out = _review(_state())
    assert list(out["sources"]) == ["aaa"]           # bbb 没人引用 → 不出去
    assert out["slides"][1]["claims"][0]["text"] == "有据的一条"
    assert out["slides"][1]["claims"][1]["gap"] == "本次未找到"
    assert out["slides"][1]["notes"][0]["text"] == "备注里的事实"


def test_evidence_text_is_truncated():
    out = _review(_state())
    assert len(out["sources"]["aaa"]["text"]) == 400
    assert out["sources"]["aaa"]["heading_path"] == ["第三章"]
    assert out["sources"]["aaa"]["line"] == 12


def test_empty_and_dirty_state_does_not_raise():
    assert _review({}) == {"slides": [], "sources": {}}
    assert _review({"slides": ["不是字典"], "evidence": {"x": "也不是"}}) \
        == {"slides": [], "sources": {}}


def test_claim_fields_are_whitelisted():
    """只投影前端使用字段，包括新增事实/建议表达类型。"""
    out = _review({"slides": [{"kind": "bullets", "title": "t",
                               "claims": [{"text": "x", "evidence_ids": [],
                                           "gap": "g", "column": None,
                                           "内部字段": "不该出现"}]}],
                   "evidence": {}})
    assert set(out["slides"][0]["claims"][0]) == {"text", "evidence_ids", "gap", "column", "kind"}
