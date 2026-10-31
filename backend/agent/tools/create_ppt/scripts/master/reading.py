"""PPT read receipts: retain content, expose coverage and immutable-read reuse."""
from agent.tools.workspace.tool import FileWorkspace


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


class PptFileWorkspace(FileWorkspace):
    def __init__(self, *args, read_state, on_written=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_state = read_state
        self.on_written = on_written
        self.writes = []

    def write_file(self, path, text):
        target = self.resolve(path, write=True)
        relative = target.relative_to(self.folder).as_posix()
        if target.is_file() and target.stat().st_size <= 2 * 1024 * 1024 and target.read_text(encoding='utf-8-sig') == text:
            self.writes.append((relative, False))
            return {'status': 'success', 'data': {'path': path, 'written': 0, 'unchanged_write': True}}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        result = {'status': 'success', 'data': {'path': path, 'written': len(text)}}
        self.writes.append((relative, True))
        if self.on_written:
            self.on_written(relative)
        result['data']['artifact_changed'] = True
        return result

    def take_writes(self):
        writes, self.writes = self.writes, []
        return writes

    def read_file(self, path, start_line=0, line_count=160, character_offset=0):
        result = super().read_file(path, start_line, line_count, character_offset)
        data = result['data']
        target = self.resolve(path)
        if 'text' not in data:
            return result

        stat = target.stat()
        version = [stat.st_mtime_ns, stat.st_size]
        key = str(target)
        previous = self.read_state.get(key)
        unchanged = previous is not None and previous['version'] == version
        entry = previous if unchanged else {'version': version, 'ranges': [], 'windows': {}}
        total = data['total_lines']
        start = min(start_line, total)
        end = min(start_line + line_count, total)
        window = '%d:%d' % (start, end)
        char_end = character_offset + len(data['text'])
        windows = entry['windows'].get(window, [])
        covered = (any(left <= start and right >= end for left, right in entry['ranges']) or
                   any(left <= character_offset and right >= char_end for left, right in windows))
        already_read = unchanged and covered

        entry['windows'][window] = _merge([*windows, [character_offset, char_end]])
        complete_window = data['next_character_offset'] is None and any(
            left == 0 and right >= char_end for left, right in entry['windows'][window])
        if complete_window:
            entry['ranges'] = _merge([*entry['ranges'], [start, end]])
        self.read_state[key] = entry
        next_line = 0
        for left, right in entry['ranges']:
            if left > next_line:
                break
            next_line = max(next_line, right)
        fully_read = next_line >= total
        data.update(start_line=start, end_line=end, eof=(end == total and data['next_character_offset'] is None),
                    file_fully_read=fully_read, next_start_line=None if fully_read else next_line,
                    already_read=already_read, content_unchanged=unchanged,
                    new_coverage=not already_read)
        if data['next_character_offset'] is not None:
            data['read_note'] = '当前行范围仍有未返回字符；先按next_character_offset续读同一范围。'
        return result
