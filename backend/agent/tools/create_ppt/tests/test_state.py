# tests/graph/test_ppt_state.py —— 进度落盘：**原子写** + 损坏/不完整的**两条分支**
#
# ★ 阶段0.5 §5.6 / 阶段3 §6：
#   · **应用级 state.json** 支持进程重启后从**阶段边界**恢复；
#   · **损坏/不完整**时 —— **显式续接**必须报可见错误（静默新建会**丢原任务目标**）；
#     只有**用户发起新任务**时才能新建。
#   · 落盘用**临时文件 + `os.replace()` 原子替换** —— 崩溃在「节点执行后、落盘前」时，
#     该节点会重跑，而**绝不会读到半截文件**。
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.config import settings  # noqa: E402
from agent.tools.create_ppt.scripts.execution import subgraph as S  # noqa: E402

_GOOD = {"task_id": "t1", "stage": "researching", "next_node": "evidence_check",
         "request_raw": "做个PPT", "brief": {"goal": "g"},
         "plan": {"research_questions": []}, "evidence": {}, "coverage": {},
         "tool_calls": [], "budget": {}}


@pytest.fixture(autouse=True)
def _tmp_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    return tmp_path


def test_round_trip(tmp_path):
    path = S.save_state("t1", _GOOD)
    assert Path(path).exists()
    assert S.load_state("t1", for_resume=True) == _GOOD


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    """★ 原子写：正常情况下**不留临时文件**（它是 `os.replace` 的被替换对象）。"""
    S.save_state("t1", _GOOD)
    leftovers = list(Path(tmp_path).glob("*.tmp"))
    assert leftovers == [], f"残留临时文件：{leftovers}"


def test_truncated_file_is_detected(tmp_path):
    """模拟「崩溃在落盘中途」留下的**半截文件**。"""
    p = tmp_path / "t1.json"
    p.write_text('{"task_id": "t1", "stage": "resea', encoding="utf-8")
    with pytest.raises(S.StateCorrupt):
        S.load_state("t1", for_resume=True)          # 显式续接 → **报错**，不静默新建
    assert S.load_state("t1", for_resume=False) is None   # 发起新任务 → 才允许新建


def test_incomplete_file_is_detected(tmp_path):
    """缺必需键也算不完整。"""
    p = tmp_path / "t1.json"
    p.write_text(json.dumps({"task_id": "t1", "stage": "s"}), encoding="utf-8")
    with pytest.raises(S.StateCorrupt):
        S.load_state("t1", for_resume=True)
    assert S.load_state("t1", for_resume=False) is None


def test_non_object_file_is_detected(tmp_path):
    (tmp_path / "t1.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(S.StateCorrupt):
        S.load_state("t1", for_resume=True)


def test_missing_file_two_branches(tmp_path):
    with pytest.raises(S.StateCorrupt):
        S.load_state("不存在的任务", for_resume=True)
    assert S.load_state("不存在的任务", for_resume=False) is None


def test_resume_error_carries_no_silent_new_task_semantics(tmp_path):
    """★ 反面锁定：损坏时**绝不能**返回一个「空的新状态」让人以为可以继续。"""
    (tmp_path / "t1.json").write_text("{ 坏掉了", encoding="utf-8")
    try:
        got = S.load_state("t1", for_resume=True)
    except S.StateCorrupt as e:
        assert "损坏" in str(e)
        return
    pytest.fail("损坏状态竟然被静默接受了：%r" % (got,))
