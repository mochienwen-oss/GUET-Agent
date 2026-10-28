"""Owner and fixed-path task management checks; run only after approval."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from core.config import settings
from core.errors import ApiError
from agent.tools.create_ppt.scripts import subgraph, task_management, workflow

TASK_ID = 'a123456789ab'


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'agent_ppt_workdir', str(tmp_path))
    state = {'task_id': TASK_ID, 'owner_id': 1, 'stage': 'paused', 'next_node': 'master',
             'request_raw': '介绍图像处理', 'brief': {'goal': '介绍图像处理'}, 'plan': {},
             'evidence': {}, 'coverage': {}, 'tool_calls': [], 'budget': {},
             'chat_messages': [{'role': 'user', 'content': '保留原来的制作要求'}]}
    subgraph.save_state(TASK_ID, state)
    (tmp_path / (TASK_ID + '.owner')).write_text('1', encoding='ascii')
    return state


def test_rename_changes_display_name_without_replanning(state):
    renamed = task_management.rename(TASK_ID, 1, '  课堂汇报  ')
    assert renamed['title'] == '课堂汇报'
    assert renamed['brief'] == state['brief']
    assert renamed['chat_messages'] == state['chat_messages']
    assert renamed['stage'] == 'paused'
    assert subgraph.load_state(TASK_ID, for_resume=True)['title'] == '课堂汇报'


def test_delete_removes_its_versions_and_files_only(state, tmp_path):
    folder = tmp_path / TASK_ID
    (folder / 'v1').mkdir(parents=True)
    (folder / 'uploads').mkdir()
    (folder / 'v1' / 'presentation.pptx').write_bytes(b'fixture')
    (folder / 'uploads' / 'material.txt').write_text('fixture', encoding='utf-8')
    (tmp_path / (TASK_ID + '.pptx')).write_bytes(b'legacy fixture')
    unrelated = tmp_path / 'other-task.txt'
    unrelated.write_text('keep', encoding='utf-8')
    assert task_management.remove(TASK_ID, 1)['deleted'] is True
    assert not folder.exists()
    assert not (tmp_path / (TASK_ID + '.json')).exists()
    assert not (tmp_path / (TASK_ID + '.owner')).exists()
    assert not (tmp_path / (TASK_ID + '.pptx')).exists()
    assert unrelated.read_text(encoding='utf-8') == 'keep'


def test_other_owner_cannot_rename_or_delete(state, tmp_path):
    for operation in (lambda: task_management.rename(TASK_ID, 2, 'new'),
                      lambda: task_management.remove(TASK_ID, 2)):
        with pytest.raises(ApiError) as error:
            operation()
        assert error.value.status_code == 404
    assert (tmp_path / (TASK_ID + '.json')).is_file()


def test_active_execution_blocks_management(state):
    workflow._ACTIVE_TASKS.add(TASK_ID)
    try:
        with pytest.raises(ApiError) as error:
            task_management.remove(TASK_ID, 1)
        assert error.value.status_code == 409
        with pytest.raises(ApiError):
            task_management.rename(TASK_ID, 1, 'new')
    finally:
        workflow._ACTIVE_TASKS.discard(TASK_ID)


def test_symlink_in_task_does_not_delete_target(state, tmp_path):
    target = tmp_path / 'outside-task'
    target.mkdir()
    marker = target / 'keep.txt'
    marker.write_text('keep', encoding='utf-8')
    folder = tmp_path / TASK_ID
    folder.mkdir()
    try:
        (folder / 'linked').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Creating a local symlink is not permitted in this environment')
    with pytest.raises(ApiError) as error:
        task_management.remove(TASK_ID, 1)
    assert error.value.status_code == 409
    assert marker.read_text(encoding='utf-8') == 'keep'
    assert (tmp_path / (TASK_ID + '.json')).is_file()


def test_invalid_identifier_does_not_escape_workdir(state, tmp_path):
    with pytest.raises(ApiError) as error:
        task_management.remove('../outside-task', 1)
    assert error.value.status_code == 404
    assert (tmp_path / (TASK_ID + '.json')).is_file()


def test_corrupt_task_can_be_deleted_only_with_verified_owner(state, tmp_path):
    (tmp_path / (TASK_ID + '.json')).write_text('{broken', encoding='utf-8')
    with pytest.raises(ApiError) as error:
        task_management.remove(TASK_ID, 2)
    assert error.value.status_code == 404
    assert task_management.remove(TASK_ID, 1)['deleted'] is True
