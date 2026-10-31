# -*- coding: utf-8 -*-
"""Build the retrieval gold set from two already-independent reviewed sets."""
import argparse
import json
import os
from pathlib import Path
from core.paths import PROJECT_ROOT

from agent.tools.search_regulations.scripts.evidence_ops.gold import validate_case


ROOT = PROJECT_ROOT
DEFAULT_HOLDOUT = ROOT / "测试集" / "eval_holdout_src.jsonl"
DEFAULT_ACCEPTANCE = ROOT / "测试集" / "agent_acceptance.json"
DEFAULT_OUT = ROOT / "测试集" / "retrieval_gold.jsonl"


# ── 阶段5A 覆盖表（2026-10-13）──────────────────────────────────────────────
# 金标准是**构建产物**：签入的 retrieval_gold.jsonl 必须与 build_rows() 的输出逐字相同
# （`tests/test_gold.py::test_checked_in_gold_matches_builder_byte_for_byte` 钉住这一点）。
# 所以阶段5A 对证据串的迁移写在这里，而不是直接手改 jsonl —— 手改会让构建产物与签入
# 文件不再一致，那扇门会红。
#
# 为什么这几条要覆盖：旧索引时期它们的证据串是按**旧索引**抄的（顺带抄进了一个
# markdown 标题标记、还有一处标点与新语料不同）。按裁决四，只迁移这三条，
# 每条证据串都逐字取自**当前 `_md/`**，且逐条验证落在唯一一个块内。
# 详情与逐条依据：`_work/phase5a-gold-migration.md`。
PHASE5A_EVIDENCE: dict[str, list[str]] = {
    "retrieval-ref-B-004": [
        "1.外国语学院根据课程目标达成情况以及对毕业要求指标点的支持情况进行认定,换算标准如下(折算后四舍五入取整)。",
        "大学英语四级(CET4)成绩 A 在 425≤A<567 时按 A×0.1764 折算",
    ],
    "retrieval-ref-B-005": [
        "若全国大学英语四、六级及雅思、托福等考试计分体制调整,学校将对上述成绩认定标准作相应调整并公布。",
        "2.一项考试成绩只能申请认定一门“大学英语”课程。",
    ],
    "retrieval-ref-B-007": [
        "2024 年及之前入学的普通本科学生仅按照本细则中的黄色预警、橙色预警、红色预警条款执行,不执行退学预警和学业退学条款。2025 年及之后入学的普通本科学生按照本细则执行。",
    ],
}


def build_rows(holdout_path=DEFAULT_HOLDOUT,
               acceptance_path=DEFAULT_ACCEPTANCE) -> list[dict]:
    holdout = [json.loads(line) for line in Path(holdout_path).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    acceptance = json.loads(Path(acceptance_path).read_text(encoding="utf-8"))
    rows = []
    for source in holdout:
        structured_course = all(
            doc.startswith("2025级") and "培养方案" in doc
            for doc in (source.get("expected_docs") or []))
        rows.append(validate_case({
            "id": "retrieval-%s" % source["id"],
            "query": source["question"],
            "answerable": True,
            "expected_tool": "query_course" if structured_course else "search_regulations",
            "expected_docs": list(source.get("expected_docs") or []),
            "expected_evidence": [source["evidence"]],
            "acceptable_gate": ["supported", "ambiguous"],
            "source_dataset": str(Path(holdout_path).name),
            "source_id": source["id"],
            "review_status": "inherited_source_verified",
        }))
    for source in acceptance:
        if source.get("kind") != "未收录":
            continue
        rows.append(validate_case({
            "id": "retrieval-negative-%s" % source["id"],
            "query": source["question"],
            "answerable": False,
            "expected_tool": "search_regulations",
            "expected_docs": [],
            "expected_evidence": [],
            "acceptable_gate": ["empty", "irrelevant"],
            "source_dataset": str(Path(acceptance_path).name),
            "source_id": source["id"],
            "review_status": "inherited_behavior_reviewed",
        }))
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate ids while building retrieval gold")
    return apply_phase5a(rows)


def apply_phase5a(rows: list[dict]) -> list[dict]:
    """阶段5A：按当前 `_md/` 重录指定行的证据串；`query_course` 行标 `pending_phase5b`。

    密钥顺序不变（覆盖已有键 = 原位替换；新增键 = 追加到尾部），
    所以产物与签入文件仍然逐字相同。
    """
    for row in rows:
        if row["id"] in PHASE5A_EVIDENCE:
            row["expected_evidence"] = list(PHASE5A_EVIDENCE[row["id"]])
        if row["expected_tool"] == "query_course":
            # 裁决四.5：这 15 条保留记录但**不在 5A 重录** ——
            # 等 5B 的 plan_db 重建后，从结构化 ToolResult 与原始表格人工核验。
            row["pending_phase5b"] = True
    return rows


def encoded(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                   for row in rows)


def write_gold(path: str | Path, rows: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(encoded(rows), encoding="utf-8")
    os.replace(temp, target)


def training_collisions(rows: list[dict], train_path: str | Path) -> list[str]:
    questions = {row["query"] for row in rows}
    collisions = []
    for line in Path(train_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        for message in item.get("messages") or []:
            if message.get("role") == "user" and message.get("content") in questions:
                collisions.append(message["content"])
    return sorted(set(collisions))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build provenance-preserving retrieval gold")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--train", default=None,
                        help="optional active training JSONL for exact-question leakage report")
    args = parser.parse_args()
    rows = build_rows()
    write_gold(args.out, rows)
    print("rows=%d answerable=%d negative=%d path=%s" %
          (len(rows), sum(x["answerable"] for x in rows),
           sum(not x["answerable"] for x in rows), args.out))
    if args.train:
        collisions = training_collisions(rows, args.train)
        print("training_exact_collisions=%d" % len(collisions))
        for question in collisions:
            print("COLLISION %s" % question)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
