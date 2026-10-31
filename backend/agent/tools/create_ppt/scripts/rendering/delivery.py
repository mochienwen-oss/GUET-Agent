"""PPT 文件的归属、短期下载凭证与路径限制。

## 人工确认位（本轮新增）

生成完成只到 `draft`（**已生成待确认**）；`ready`（**已确认可下载**）**只能**由
`confirm()` 设置 —— 它是唯一的下载凭证签发点。所以
「用户确认后才能下载」是**后端强制**的，不是前端提示。
"""
import hashlib
import secrets
import threading
import time
import uuid
import shutil
from pathlib import Path

from core.errors import ApiError
from core.config import settings

from agent.tools.create_ppt.scripts.execution import subgraph


_LOCK = threading.Lock()


class TaskCorrupt(Exception):
    """已核实归属，但任务快照损坏。"""


def _cleanup_finished(root: Path) -> None:
    """只清理由本模块生成、且已终结并过期的任务文件。"""
    import json

    for file in root.glob("*.json"):
        if not file.stem.isalnum() or time.time() - file.stat().st_mtime \
                < settings.agent_ppt_task_ttl_sec:
            continue
        try:
            item = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(item, dict) or item.get("stage") not in ("ready", "draft", "failed", "await_materials", "materials_changed", "brief_changed"):
            continue
        folder = root / file.stem
        if item.get("workflow_version") == 2 and folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == root.resolve():
            shutil.rmtree(folder)
        for suffix in (".pptx", ".sources.md", ".owner", ".json"):
            target = (root / (file.stem + suffix)).resolve()
            if target.parent == root.resolve():
                target.unlink(missing_ok=True)


def reserve_task(owner_id: int, conversation_id: int) -> str:
    """原子检查同会话执行中任务并预留 ID；澄清等待不算执行中。"""
    with _LOCK:
        root = Path(subgraph._workdir())
        root.mkdir(parents=True, exist_ok=True)
        _cleanup_finished(root)
        for file in root.glob("*.json"):
            try:
                import json
                item = json.loads(file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(item, dict):
                continue
            if conversation_id is not None and item.get("owner_id") == owner_id and item.get("conversation_id") == conversation_id \
                    and item.get("stage") not in ("ready", "draft", "failed",
                                                  "await_clarify", "await_design", "paused", "await_materials", "materials_changed", "brief_changed"):
                if item.get("stage") != "starting" or \
                        time.time() - item.get("reserved_at", 0) < settings.agent_ppt_timeout_sec:
                    raise ApiError(409, "已有 PPT 任务正在制作，请等待完成")
        tid = uuid.uuid4().hex[:12]
        (root / (tid + ".owner")).write_text(str(owner_id), encoding="ascii")
        subgraph.save_state(tid, {"task_id": tid, "owner_id": owner_id,
                                  "conversation_id": conversation_id, "stage": "starting",
                                  "reserved_at": time.time(), "next_node": "intake",
                                  "request_raw": "", "brief": {}, "plan": {}, "evidence": {},
                                  "coverage": {}, "tool_calls": [], "budget": {}})
        return tid


def owned_state(task_id: str, owner_id: int) -> dict:
    try:
        state = subgraph.load_state(task_id, for_resume=True)
    except subgraph.StateCorrupt:
        # 独立的归属标记只用于识别损坏任务；非本人仍统一 404。
        try:
            owner_file = Path(subgraph._state_path(task_id)).with_suffix(".owner")
            if owner_file.read_text(encoding="ascii").strip() == str(owner_id):
                raise TaskCorrupt("任务状态损坏，请重新发起") from None
        except (OSError, UnicodeError, subgraph.StateCorrupt):
            pass
        raise ApiError(404, "PPT 任务不存在") from None
    if state.get("owner_id") != owner_id:
        raise ApiError(404, "PPT 任务不存在")
    return state


def _safe_file(task_id: str) -> Path:
    root = Path(subgraph._workdir()).resolve()
    state = subgraph.load_state(task_id, for_resume=True)
    if state.get("workflow_version") == 2:
        path = Path((state.get("artifacts") or {}).get("pptx_path") or "").resolve()
        expected = root / task_id / ("v%d" % state.get("revision", 0)) / "presentation.pptx"
        if path != expected or not path.is_relative_to(root):
            raise ApiError(404, "文件不存在")
        return path
    path = (root / (task_id + ".pptx")).resolve()
    if path.parent != root:
        raise ApiError(404, "文件不存在")
    return path


def _issue_locked(task_id: str, state: dict) -> str:
    """签发一次性下载凭证。**调用方必须已持有 `_LOCK`，且 stage 已确认是 `ready`。**"""
    if not _safe_file(task_id).is_file():
        raise ApiError(404, "文件不存在")
    secret = secrets.token_urlsafe(24)
    state["download"] = {
        "digest": hashlib.sha256(secret.encode()).hexdigest(),
        "expires_at": time.time() + settings.agent_ppt_token_ttl_sec,
        "uses_left": 1,
        "revision": state.get("revision", 0),
    }
    subgraph.save_state(task_id, state)
    return task_id + "." + secret


def issue_token(task_id: str, owner_id: int) -> str:
    """为**已确认**（`ready`）的任务重签一个凭证 —— 前端把地址弄丢时用。"""
    with _LOCK:
        state = owned_state(task_id, owner_id)
        if time.time() - Path(subgraph._state_path(task_id)).stat().st_mtime \
                > settings.agent_ppt_task_ttl_sec:
            raise ApiError(404, "PPT 文件已过期")
        if state.get("stage") != "ready":
            raise ApiError(404, "文件不存在")
        return _issue_locked(task_id, state)


def confirm(task_id: str, owner_id: int, *, expected_revision=None) -> tuple[dict, str]:
    """**人工确认位** —— `draft`（已生成待确认）→ `ready`（已确认可下载），当场签发凭证。

    ★ 这是把 stage 变成 `ready` 的**唯一**入口，所以在此之前 `issue_token` /
      `/download-token` 一律 404。「用户确认后才能下载」因此是**后端强制**的。
    重复确认幂等（再发一个新凭证），文件不在盘时抛 404 且**保持 `draft`**（翻转不落盘）。
    """
    with _LOCK:
        state = owned_state(task_id, owner_id)
        if expected_revision is not None and state.get("revision", 0) != expected_revision:
            raise ApiError(409, "草稿版本已变化，请刷新后确认")
        if state.get("stage") == "draft":
            state["stage"] = "ready"
            state["next_node"] = None
        elif state.get("stage") != "ready":
            raise ApiError(409, "任务还没有生成可确认的草稿")
        return state, _issue_locked(task_id, state)


def consume(token: str, owner_id: int) -> Path:
    task_id, sep, secret = token.partition(".")
    if not sep or not secret:
        raise ApiError(404, "下载凭证无效")
    with _LOCK:
        state = owned_state(task_id, owner_id)
        dl = state.get("download") or {}
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if state.get("stage") != "ready" or dl.get("revision", 0) != state.get("revision", 0) or \
                not secrets.compare_digest(digest, dl.get("digest") or "") or \
                dl.get("expires_at", 0) <= time.time() or dl.get("uses_left", 0) <= 0:
            raise ApiError(404, "下载凭证无效或已过期")
        path = _safe_file(task_id)
        if not path.is_file():
            raise ApiError(404, "文件不存在")
        dl["uses_left"] -= 1
        subgraph.save_state(task_id, state)
    return path
