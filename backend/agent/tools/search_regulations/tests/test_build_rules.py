# -*- coding: utf-8 -*-
"""入库规则 R1–R6：纯函数，用合成语料验，不连 Qdrant、不加载模型。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.tools.search_regulations.scripts.index_ops import build_collection as bc  # noqa: E402


def _units(*specs):
    """specs: (heading_path, text) → 产出带 chunk_idx 的 unit 列表。"""
    return [{"chunk_idx": i, "text": text, "heading_path": list(path),
             "block_type": "text", "category": ""}
            for i, (path, text) in enumerate(specs)]


def _corpus(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


_PROSE = "第一条 本条正文。"
_COURSE_TABLE = "| 课程代码 | 课程名称 |\n|---|---|\n| 000042 | 思想道德与法治 |\n"
_GOOD_TABLE = "| 类别 | 成绩A | 大学英语成绩 |\n|---|---|---|\n| CET4 | 425≤A<567 | A×0.1764 |\n"
_BAD_TABLE = "| 甲 | 乙 |\n|---|---|\n| 1 | 2 | 3 |\n"


def _build(tmp_path, files, units_map):
    root = _corpus(tmp_path, files)
    fn = lambda text: units_map[text]  # noqa: E731 - 按文本查合成块
    return bc.plan_ingest(root, units_fn=fn)


def test_regulation_prose_is_kept(tmp_path):
    plan = _build(tmp_path, {"规章制度/教学制度/甲办法.md": _PROSE},
                  {_PROSE: _units((["甲办法"], _PROSE))})
    assert plan["counts"]["kept"] == 1
    assert plan["kept"][0]["category"] == "规章制度"


def test_course_schedule_section_is_excluded(tmp_path):
    """R4：教学进程课程表不进普通向量索引。"""
    plan = _build(tmp_path, {"培养方案/2025级甲专业培养方案(主修).md": _COURSE_TABLE},
                  {_COURSE_TABLE: _units((["十、教学进程计划表"], _COURSE_TABLE))})
    assert plan["counts"]["kept"] == 0
    assert plan["excluded"] == {"教学进程计划表": 1}


def test_both_matrices_are_excluded(tmp_path):
    """R6：五节与六节两张矩阵都不进普通向量索引。"""
    plan = _build(tmp_path, {"培养方案/2025级甲专业培养方案(主修).md": _GOOD_TABLE},
                  {_GOOD_TABLE: _units((["五、毕业要求对培养目标的支撑矩阵"], _GOOD_TABLE),
                                       (["六、课程计划与毕业要求的对应矩阵"], _GOOD_TABLE))})
    assert plan["counts"]["kept"] == 0
    assert plan["excluded"] == {"毕业要求对培养目标的支撑矩阵": 1,
                                "课程计划与毕业要求的对应矩阵": 1}


def test_narrative_body_of_a_plan_is_kept(tmp_path):
    """R3：培养方案叙述正文继续入普通索引。"""
    plan = _build(tmp_path, {"培养方案/2025级甲专业培养方案(主修).md": _PROSE},
                  {_PROSE: _units((["三、培养目标"], _PROSE))})
    assert plan["counts"]["kept"] == 1
    assert plan["kept"][0]["category"] == "培养方案"


def test_a_table_block_of_column_mismatched_shape_is_isolated(tmp_path):
    """裁决二.4：列对不齐的表不得静默入库，要进隔离报告并带原文。"""
    plan = _build(tmp_path, {"规章制度/教学制度/乙办法.md": _BAD_TABLE},
                  {_BAD_TABLE: _units((["乙办法"], _BAD_TABLE))})
    assert plan["tables"]["tables_seen"] == 1
    assert plan["tables"]["passed"] == 0
    assert len(plan["tables"]["failed"]) == 1
    assert plan["tables"]["failed"][0]["raw"] == _BAD_TABLE


def test_a_good_regulation_table_passes_and_is_kept(tmp_path):
    plan = _build(tmp_path, {"规章制度/教学制度/丙办法.md": _GOOD_TABLE},
                  {_GOOD_TABLE: _units((["丙办法"], _GOOD_TABLE))})
    assert plan["counts"]["kept"] == 1
    assert plan["tables"]["tables_seen"] == 1 and plan["tables"]["passed"] == 1
    assert plan["tables"]["by_category"] == {"规章制度": 1}


def test_original_chunk_idx_is_preserved_not_renumbered(tmp_path):
    """筛掉块之后**不重排编号** —— 每一块都能回溯到原文档的原始切分位置。"""
    plan = _build(tmp_path, {"培养方案/2025级甲专业培养方案(主修).md": _PROSE},
                  {_PROSE: _units((["三、培养目标"], _PROSE),
                                  (["十、教学进程计划表"], _COURSE_TABLE),
                                  (["九、学分修读要求"], _PROSE))})
    assert [k["unit"]["chunk_idx"] for k in plan["kept"]] == [0, 2]


def test_dry_run_reports_but_never_touches_a_collection(tmp_path):
    root = _corpus(tmp_path, {"规章制度/教学制度/甲办法.md": _PROSE})
    plan = bc.plan_ingest(root, units_fn=lambda text: _units((["甲办法"], text)))
    report = bc.build("school_docs_phase5", tmp_path / "vocab.json", root,
                      dry_run=True, plan=plan)
    assert report["ok"] is True and report["dry_run"] is True
    assert "points" not in report          # 没连库，不该有库侧数字


def test_build_refuses_when_a_table_fails_validation(tmp_path):
    """有隔离表时，即使真的写库也必须拒绝并给出隔离报告。"""
    root = _corpus(tmp_path, {"规章制度/教学制度/乙办法.md": _BAD_TABLE})
    plan = bc.plan_ingest(root, units_fn=lambda text: _units((["乙办法"], text)))
    report = bc.build("school_docs_phase5", tmp_path / "vocab.json", root,
                      dry_run=False, plan=plan, qdrant_path=str(tmp_path / "q"))
    assert report["ok"] is False
    assert report["reason"] == "table_validation_failed"
    assert report["isolated_tables"][0]["raw"] == _BAD_TABLE


def test_overwrite_deletes_the_target_collection_before_rebuild(monkeypatch, tmp_path):
    """`--overwrite` 不能只 upsert；否则新语料变少时旧点会残留。"""
    root = _corpus(tmp_path, {"规章制度/教学制度/甲办法.md": _PROSE})
    plan = bc.plan_ingest(root, units_fn=lambda text: _units((["甲办法"], text)))

    class Client:
        def __init__(self):
            self.deleted = []

        def collection_exists(self, name):
            return True

        def delete_collection(self, name):
            self.deleted.append(name)

        def get_collection(self, name):
            return type("Info", (), {"points_count": 1})()

    client = Client()
    monkeypatch.setattr(bc.indexing, "ensure_collection", lambda: None)
    monkeypatch.setattr(bc.indexing, "_index_document", lambda *a, **k: None)
    monkeypatch.setattr(bc.bm25, "build_vocab", lambda texts: {"terms": {"甲": 0}})
    monkeypatch.setattr(bc.bm25, "save_vocab", lambda vocab, path: None)

    report = bc.build("school_docs_phase5", tmp_path / "vocab.json", root,
                      dry_run=False, plan=plan, overwrite=True, client=client)

    assert report["ok"] is True
    assert client.deleted == ["school_docs_phase5"]
