"""PPT reading coverage only; no Skill execution or provider calls."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.tools.create_ppt.scripts.reading import PptFileWorkspace


def test_read_receipt_reports_new_ranges_and_keeps_legitimate_reread_text(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_text('\n'.join('line%d' % number for number in range(12)), encoding='utf-8')
    files = PptFileWorkspace(tmp_path, {}, read_state={})
    first = files.read_file('notes.md', line_count=5)['data']
    assert first['start_line'] == 0 and first['end_line'] == 5
    assert first['next_start_line'] == 5 and not first['eof']
    assert first['new_coverage'] and not first['already_read']
    second = files.read_file('notes.md', start_line=5, line_count=7)['data']
    assert second['eof'] and second['file_fully_read'] and second['next_start_line'] is None
    repeated = files.read_file('notes.md', line_count=5)['data']
    assert repeated['already_read'] and repeated['content_unchanged'] and not repeated['new_coverage']
    assert repeated['text'] == first['text']


def test_changed_file_is_not_mistaken_for_stale_repeated_read(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_text('old', encoding='utf-8')
    files = PptFileWorkspace(tmp_path, {}, read_state={})
    files.read_file('notes.md')
    path.write_text('new content', encoding='utf-8')
    result = files.read_file('notes.md')['data']
    assert result['text'] == 'new content'
    assert result['new_coverage'] and not result['content_unchanged'] and not result['already_read']


def test_character_pagination_is_not_reported_as_a_complete_file_early(tmp_path):
    (tmp_path / 'long.md').write_text('a' * 40005, encoding='utf-8')
    files = PptFileWorkspace(tmp_path, {}, read_state={})
    first = files.read_file('long.md', line_count=1)['data']
    assert first['next_character_offset'] == 40000 and not first['file_fully_read']
    second = files.read_file('long.md', line_count=1, character_offset=40000)['data']
    assert second['text'] == 'a' * 5 and second['file_fully_read']
    repeated = files.read_file('long.md', line_count=1, character_offset=40000)['data']
    assert repeated['already_read'] and repeated['text'] == 'a' * 5


