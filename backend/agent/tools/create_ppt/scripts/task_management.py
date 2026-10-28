"""Owner-only display names and deletion of fixed task-local files."""
from pathlib import Path
import shutil
import stat

from core.errors import ApiError
from . import delivery, subgraph, workflow


def _not_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ApiError(409, '任务目录包含链接，不能自动修改或删除，请检查本地任务目录')


def _paths(task_id):
    # Validate the identifier with the same storage boundary as normal tasks.
    try:
        subgraph._state_path(task_id)
    except subgraph.StateCorrupt:
        raise ApiError(404, 'PPT 任务不存在') from None
    configured = Path(subgraph._workdir()).absolute()
    for parent in (configured, *configured.parents):
        _not_link(parent)
    root = configured.resolve()
    folder = root / task_id
    files = [root / (task_id + suffix) for suffix in
             ('.pptx', '.sources.md', '.json.tmp', '.json', '.owner')]
    for path in (folder, *files):
        _not_link(path)
        if path.resolve().parent != root:
            raise ApiError(409, '任务路径不属于当前制作工作区')
    return folder, files


def _idle(task_id, state):
    if task_id in workflow._ACTIVE_TASKS or state.get('stage') in {'starting', 'generating'}:
        raise ApiError(409, '该演示文稿正在制作，请等待本轮结束后再操作')


def rename(task_id, owner_id, title):
    name = ' '.join(title.split())
    if not name or len(name) > 120:
        raise ApiError(400, '演示文稿名称需为1—120个字符')
    with delivery._LOCK, subgraph._STATE_LOCK:
        _paths(task_id)
        try:
            state = delivery.owned_state(task_id, owner_id)
        except delivery.TaskCorrupt:
            raise ApiError(409, '任务记录已损坏，暂时无法重命名；可删除后重新创建') from None
        _idle(task_id, state)
        state['title'] = name
        subgraph.save_state(task_id, state)
        return state


def _check_tree(path):
    _not_link(path)
    if path.is_dir():
        for child in path.iterdir():
            _check_tree(child)


def remove(task_id, owner_id):
    with delivery._LOCK, subgraph._STATE_LOCK:
        folder, files = _paths(task_id)
        try:
            state = delivery.owned_state(task_id, owner_id)
        except delivery.TaskCorrupt:
            # owned_state raises this only after its separate owner marker matched.
            state = {}
        _idle(task_id, state)
        # Inspect before deleting anything; never use paths from model/artifact data.
        if folder.exists():
            if not folder.is_dir():
                raise ApiError(409, '任务材料目录状态异常，未执行删除')
            _check_tree(folder)
        for path in files:
            if path.exists() and not path.is_file():
                raise ApiError(409, '任务文件状态异常，未执行删除')
        try:
            if folder.exists():
                shutil.rmtree(folder)
            for path in files:
                path.unlink(missing_ok=True)
        except OSError:
            raise ApiError(409, '部分任务文件未能删除。请关闭占用文件的程序、确认目录可写后重试') from None
    return {'task_id': task_id, 'deleted': True}
