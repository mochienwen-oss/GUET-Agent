"""下载凭证和任务归属的安全边界。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.config import settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from agent.tools.create_ppt.scripts.rendering import delivery
from agent.tools.create_ppt.scripts.execution import subgraph  # noqa: E402


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    tid = delivery.reserve_task(11, 7)
    state = subgraph.load_state(tid, for_resume=True)
    state["stage"] = "ready"
    state["next_node"] = None
    subgraph.save_state(tid, state)
    (tmp_path / (tid + ".pptx")).write_bytes(b"pptx-test")
    return tid


def test_owner_token_one_use_and_other_user_hidden(task):
    token = delivery.issue_token(task, 11)
    with pytest.raises(ApiError) as foreign:
        delivery.consume(token, 12)
    assert foreign.value.status_code == 404
    assert delivery.consume(token, 11).name == task + ".pptx"
    with pytest.raises(ApiError):
        delivery.consume(token, 11)
    renewed = delivery.issue_token(task, 11)
    assert renewed != token
    assert delivery.consume(renewed, 11).name == task + ".pptx"


def test_missing_expired_and_invalid_token(task):
    for token in ("", "wrong", "../file.secret"):
        with pytest.raises(ApiError):
            delivery.consume(token, 11)
    token = delivery.issue_token(task, 11)
    state = subgraph.load_state(task, for_resume=True)
    state["download"]["expires_at"] = 0
    subgraph.save_state(task, state)
    with pytest.raises(ApiError):
        delivery.consume(token, 11)


def test_corrupt_state_reports_to_owner_only(task):
    Path(subgraph._state_path(task)).write_text("{", encoding="utf-8")
    with pytest.raises(delivery.TaskCorrupt):
        delivery.owned_state(task, 11)
    with pytest.raises(ApiError) as foreign:
        delivery.owned_state(task, 12)
    assert foreign.value.status_code == 404


def test_active_task_blocks_second_until_clarify(task):
    state = subgraph.load_state(task, for_resume=True)
    state["stage"] = "researching"
    subgraph.save_state(task, state)
    with pytest.raises(ApiError) as busy:
        delivery.reserve_task(11, 7)
    assert busy.value.status_code == 409
    state["stage"] = "await_clarify"
    subgraph.save_state(task, state)
    assert delivery.reserve_task(11, 7) != task


# ── 人工确认位：`draft`（已生成待确认）→ `ready`（已确认可下载）──────
@pytest.fixture
def draft(tmp_path, monkeypatch):
    """生成完成但**尚未确认**的任务 —— `ready` 只能由 `confirm()` 设置。"""
    monkeypatch.setattr(settings, "agent_ppt_workdir", str(tmp_path))
    tid = delivery.reserve_task(11, 7)
    state = subgraph.load_state(tid, for_resume=True)
    state["stage"] = "draft"
    state["next_node"] = None
    subgraph.save_state(tid, state)
    (tmp_path / (tid + ".pptx")).write_bytes(b"pptx-test")
    return tid


def test_draft_gets_no_download_token(draft):
    """★ 未确认 → 签发口拿不到凭证。**后端强制**，不是前端提示。"""
    with pytest.raises(ApiError) as not_ready:
        delivery.issue_token(draft, 11)
    assert not_ready.value.status_code == 404
    assert subgraph.load_state(draft, for_resume=True).get("download") is None


def test_confirm_turns_a_draft_into_a_downloadable_file(draft):
    state, token = delivery.confirm(draft, 11)
    assert state["stage"] == "ready" and state["next_node"] is None
    assert subgraph.load_state(draft, for_resume=True)["stage"] == "ready"
    assert delivery.consume(token, 11).name == draft + ".pptx"


def test_confirm_before_a_draft_exists_is_rejected(draft):
    state = subgraph.load_state(draft, for_resume=True)
    state["stage"] = "researching"
    subgraph.save_state(draft, state)
    with pytest.raises(ApiError) as early:
        delivery.confirm(draft, 11)
    assert early.value.status_code == 409


def test_confirm_is_idempotent_and_reissues(draft):
    _, first = delivery.confirm(draft, 11)
    _, second = delivery.confirm(draft, 11)
    assert second != first
    assert delivery.consume(second, 11).name == draft + ".pptx"


def test_confirm_without_the_file_stays_a_draft(draft):
    """★ 翻转与签发是**同一次落盘**：文件不在盘 → 404，且**必须仍是 draft**。"""
    (Path(subgraph._workdir()) / (draft + ".pptx")).unlink()
    with pytest.raises(ApiError) as gone:
        delivery.confirm(draft, 11)
    assert gone.value.status_code == 404
    assert subgraph.load_state(draft, for_resume=True)["stage"] == "draft"


def test_only_the_owner_can_confirm(draft):
    with pytest.raises(ApiError) as foreign:
        delivery.confirm(draft, 12)
    assert foreign.value.status_code == 404


def test_draft_does_not_block_a_new_task(draft):
    """草稿在等确认，**不算执行中** —— 用户可以另起一个任务。"""
    assert delivery.reserve_task(11, 7) != draft
