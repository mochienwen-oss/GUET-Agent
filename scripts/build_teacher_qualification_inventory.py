# -*- coding: utf-8 -*-
"""Build a deterministic question inventory for the offline-teacher exam."""
import argparse
import hashlib
import json
from pathlib import Path


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _candidate_id(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _logical_names(paths) -> list[str]:
    names = [Path(path).name for path in paths]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError("输入逻辑名重复：%s" % duplicates)
    return names


def _read_jsonl(path: str | Path) -> list[tuple[int, dict]]:
    source = Path(path)
    rows = []
    for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("%s 第 %d 行不是合法 JSON：%s"
                             % (source, lineno, error)) from error
        if not isinstance(value, dict):
            raise ValueError("%s 第 %d 行必须是 JSON 对象" % (source, lineno))
        rows.append((lineno, value))
    return rows


def _required_text(row: dict, key: str, where: str) -> str:
    if key not in row:
        raise ValueError("%s 缺少字段 %s" % (where, key))
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s 的 %s 必须是非空字符串" % (where, key))
    return value.strip()


def load_tool_names(tool_names_path: str | Path) -> tuple[str, ...]:
    """读取当前运行时工具名称契约（有序名称数组），不导入运行时工具注册表。

    只记名称而不复制完整 runtime schema：构建器只需要验证 `want_tool` 是否属于
    当前工具集合，第二份大型 schema 快照只会多出一个会漂移的副本。
    """
    source = Path(tool_names_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("%s 不是合法 JSON：%s" % (source, error)) from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("工具名称契约必须是非空 JSON 字符串数组")
    names = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ValueError("工具名称契约第 %d 项必须是非空且无首尾空白的字符串"
                             % index)
        names.append(item)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError("工具名称契约存在重复名称：%s" % duplicates)
    return tuple(names)


def _dedupe(rows: list[dict]) -> tuple[list[dict], int, list[str]]:
    unique = {}
    duplicate_count = 0
    duplicate_ids = set()
    for row in rows:
        identity = row["candidate_id"]
        previous = unique.get(identity)
        if previous is None:
            unique[identity] = row
        elif ({key: value for key, value in previous.items()
               if key not in {"source_line", "source_index"}}
              == {key: value for key, value in row.items()
                  if key not in {"source_line", "source_index"}}):
            duplicate_count += 1
            duplicate_ids.add(identity)
        else:
            raise ValueError("candidate_id SHA-256 碰撞：%s" % identity)
    return list(unique.values()), duplicate_count, sorted(duplicate_ids)


def _single_rows(path: Path) -> tuple[list[dict], dict]:
    built = []
    raw_rows = _read_jsonl(path)
    if not raw_rows:
        raise ValueError("single candidate 输入不得为空：%s" % path)
    for lineno, raw in raw_rows:
        where = "%s 第 %d 行" % (path, lineno)
        question = _required_text(raw, "question", where)
        chunk = _required_text(raw, "chunk", where)
        if raw.get("origin") != "单轮" or raw.get("round") != 1 \
                or isinstance(raw.get("round"), bool):
            raise ValueError("%s 必须 origin=单轮 且 round=1" % where)
        identity = {"source_kind": "single_candidate", "question": question,
                    "chunk": chunk, "origin": "单轮", "round": 1}
        built.append({"candidate_id": _candidate_id(identity),
                      "source_kind": "single_candidate",
                      "source_name": path.name, "source_line": lineno,
                      "question": question, "chunk": chunk,
                      "origin": "单轮", "round": 1})
    unique, duplicates, duplicate_ids = _dedupe(built)
    return unique, {"name": path.name, "kind": "single_candidate",
                    "rows": len(raw_rows), "unique_rows": len(unique),
                    "accepted": len(unique), "filtered": 0,
                    "exact_duplicates": duplicates,
                    "duplicate_candidate_ids": duplicate_ids,
                    "sha256": sha256_file(path)}


def _seed_rows(paths) -> tuple[list[dict], list[dict]]:
    built = []
    reports = []
    seen_scenarios = set()
    for path in sorted((Path(value) for value in paths), key=lambda item: item.name):
        raw_rows = _read_jsonl(path)
        if len(raw_rows) != 1:
            raise ValueError("校准 seed %s 必须恰好 1 条，实际 %d 条"
                             % (path, len(raw_rows)))
        lineno, raw = raw_rows[0]
        where = "%s 第 %d 行" % (path, lineno)
        scenario_id = _required_text(raw, "scenario_id", where)
        question = _required_text(raw, "question", where)
        if raw.get("review_status") != "reviewed":
            raise ValueError("%s 必须 review_status=reviewed" % where)
        if raw.get("assistant_target") is not None:
            raise ValueError("%s 不得携带 assistant_target" % where)
        if scenario_id in seen_scenarios:
            raise ValueError("校准 seed scenario_id 重复：%s" % scenario_id)
        seen_scenarios.add(scenario_id)
        identity = {"source_kind": "calibration_seed",
                    "scenario_id": scenario_id, "question": question}
        built.append({"candidate_id": _candidate_id(identity),
                      "source_kind": "calibration_seed",
                      "source_name": path.name, "source_line": lineno,
                      "scenario_id": scenario_id, "question": question})
        reports.append({"name": path.name, "kind": "calibration_seed",
                        "rows": 1, "unique_rows": 1, "accepted": 1,
                        "filtered": 0, "exact_duplicates": 0,
                        "duplicate_candidate_ids": [],
                        "sha256": sha256_file(path)})
    return built, reports


def _acceptance_rows(path: Path, tool_names) -> tuple[list[dict], dict]:
    try:
        raw_rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("%s 不是合法 JSON：%s" % (path, error)) from error
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("acceptance 输入必须是非空 JSON 数组")
    known = frozenset(tool_names)
    built = []
    seen_ids = {}
    for index, raw in enumerate(raw_rows, 1):
        where = "%s 第 %d 项" % (path, index)
        if not isinstance(raw, dict):
            raise ValueError("%s 必须是 JSON 对象" % where)
        if "id" not in raw:
            raise ValueError("%s 缺少字段 id" % where)
        case_id = raw["id"]
        if isinstance(case_id, (dict, list)) or case_id is None:
            raise ValueError("%s 的 id 必须是标量" % where)
        question = _required_text(raw, "question", where)
        if "want_tool" not in raw or "want_args" not in raw:
            raise ValueError("%s 必须同时声明 want_tool/want_args" % where)
        tool = raw["want_tool"]
        arguments = raw["want_args"]
        if tool is None:
            if arguments is not None:
                raise ValueError("%s 的 want_tool/want_args 必须同时为 null" % where)
            hint_complete = True
            review_flags = []
        else:
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("%s 的 want_tool 必须非空" % where)
            tool = tool.strip()
            if tool not in known:
                raise ValueError("%s 使用的工具不在工具名称契约中：%s"
                                 % (where, tool))
            if arguments is None:
                hint_complete = False
                review_flags = ["missing_want_args"]
            elif not isinstance(arguments, dict):
                raise ValueError("%s 的 want_args 必须是 JSON 对象" % where)
            else:
                hint_complete = True
                review_flags = []
        identity = {"source_kind": "agent_acceptance", "id": case_id,
                    "question": question, "want_tool": tool,
                    "want_args": arguments}
        previous = seen_ids.get(case_id)
        if previous is not None and previous != identity:
            raise ValueError("acceptance id %s 对应了不同内容" % case_id)
        seen_ids[case_id] = identity
        built.append({"candidate_id": _candidate_id(identity),
                      "source_kind": "agent_acceptance",
                      "source_name": path.name, "source_index": index,
                      "acceptance_id": case_id, "question": question,
                      "want_tool": tool, "want_args": arguments,
                      "hint_complete": hint_complete,
                      "review_flags": review_flags})
    unique, duplicates, duplicate_ids = _dedupe(built)
    return unique, {"name": path.name, "kind": "agent_acceptance",
                    "rows": len(raw_rows), "unique_rows": len(unique),
                    "accepted": len(unique), "filtered": 0,
                    "incomplete_tool_hints": sum(
                        not row["hint_complete"] for row in unique),
                    "exact_duplicates": duplicates,
                    "duplicate_candidate_ids": duplicate_ids,
                    "sha256": sha256_file(path)}


def _build(single_path, seed_paths, acceptance_path, tool_names):
    single_rows, single_report = _single_rows(Path(single_path))
    seed_rows, seed_reports = _seed_rows(seed_paths)
    acceptance_rows, acceptance_report = _acceptance_rows(
        Path(acceptance_path), tool_names)
    rows = single_rows + seed_rows + acceptance_rows
    by_id = {}
    for row in rows:
        previous = by_id.get(row["candidate_id"])
        if previous is not None:
            raise ValueError("候选 candidate_id 跨来源重复：%s" % row["candidate_id"])
        by_id[row["candidate_id"]] = row
    rows.sort(key=lambda row: (row["source_kind"], row["question"],
                               row["candidate_id"]))
    return rows, [single_report] + seed_reports + [acceptance_report]


def inventory_rows(single_path, seed_paths, acceptance_path, tool_names) -> list[dict]:
    rows, _ = _build(single_path, seed_paths, acceptance_path, tool_names)
    return rows


def _write_jsonl(path: Path, rows) -> None:
    text = "".join(_canonical_json(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_inventory(single_path, seed_paths, acceptance_path, tool_names_path,
                    out_path, manifest_path) -> dict:
    """Validate everything in memory, then write inventory followed by manifest."""
    single_path = Path(single_path)
    seed_paths = tuple(Path(path) for path in seed_paths)
    acceptance_path = Path(acceptance_path)
    tool_names_path = Path(tool_names_path)
    out_path = Path(out_path)
    manifest_path = Path(manifest_path)
    inputs = (single_path,) + seed_paths + (acceptance_path, tool_names_path)
    _logical_names(inputs)
    if out_path.resolve() == manifest_path.resolve():
        raise ValueError("output 与 manifest 不得是同一文件")

    tool_names = load_tool_names(tool_names_path)
    rows, reports = _build(single_path, seed_paths, acceptance_path, tool_names)
    contract_report = {"name": tool_names_path.name, "kind": "tool_names",
                       "rows": len(tool_names), "unique_rows": len(tool_names),
                       "accepted": len(tool_names), "filtered": 0,
                       "exact_duplicates": 0, "duplicate_candidate_ids": [],
                       "sha256": sha256_file(tool_names_path)}
    reports.append(contract_report)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_path, rows)
    manifest = {
        "phase": "6C",
        "artifact_kind": "teacher_qualification_inventory",
        "inputs": reports,
        "tool_names": {"count": len(tool_names), "names": list(tool_names),
                       "sha256": sha256_file(tool_names_path)},
        "output": {"name": out_path.name, "rows": len(rows),
                   "sha256": sha256_file(out_path)},
        "summary": {
            "input_rows": sum(item["rows"] for item in reports
                              if item["kind"] != "tool_names"),
            "unique_rows": len(rows),
            "exact_duplicates": sum(item["exact_duplicates"] for item in reports),
            "filtered": sum(item["filtered"] for item in reports),
            "incomplete_tool_hints": sum(
                item.get("incomplete_tool_hints", 0) for item in reports),
        },
    }
    manifest_path.write_text(_canonical_json(manifest) + "\n",
                             encoding="utf-8", newline="\n")
    return manifest


def require_six_seeds(seed_paths):
    paths = tuple(Path(path) for path in seed_paths)
    if len(paths) != 6:
        raise ValueError("必须显式提供 6 个校准 seed，实际 %d 个" % len(paths))
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build teacher qualification inventory")
    parser.add_argument("--single", required=True, type=Path)
    parser.add_argument("--seed", required=True, action="append", type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--tool-names", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    seeds = require_six_seeds(args.seed)
    manifest = write_inventory(args.single, seeds, args.acceptance,
                               args.tool_names, args.output, args.manifest)
    print(_canonical_json(manifest["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
