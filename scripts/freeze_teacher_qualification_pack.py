# -*- coding: utf-8 -*-
"""Task 8C.2 / Task 3：冻结离线 Teacher 资格考试包的**唯一**入口。

```text
读参数 → 全部静态门 → 真工具重放（逐条） → 快照 + 绑定哈希 → stage → replace
```

★ **本文件不重复实现任何判据。** 静态门、重放、快照、manifest、写盘边界
  一律在 `compiler/qualification.py`；这里只做「编排 + 参数校验 + 一次真重放」。

★ **不调 LLM。** 冻结只重放工具、记快照与哈希；答案候选是后续计划的事，
  所以这个脚本没有 model / url / sampling 之类的开关 —— 采样与响应 schema
  由 `compiler.qualification` **直接读** `scripts/generate_answer_candidates.py`
  的那两份定义，不接受命令行自由传入。

★ **语料身份有四个字段，必须来自实测**（`scripts/corpus_identity.py` 一条命令
  算完并直接打印出可粘用的冻结参数）：collection / points，加上身份模型 v2 的
  `source_tree_sha256`、`source_chunked_sha256`、`index_payload_sha256`、
  `index_chunking_profile`。这里只**严格校验并写进 manifest**。
  ★ **不再收单一 `corpus.fingerprint`** —— 它会把「文件侧分块」与「索引 payload」
  说成同一件事，而二者在 phase5 上是**子集关系**（3381 → 786）。

★ **四份产物按 `scenario_id` 稳定排序**，manifest **最后** replace。
  产物里不出现绝对路径、时间戳、主机名、用户名、凭据或完整 prompt。

Run:
  python scripts/freeze_teacher_qualification_pack.py \\
      --scenarios _work/phase6/teacher_qualification/qualification.scenarios.draft.jsonl \\
      --rubric _work/phase6/teacher_qualification/qualification.rubric.draft.jsonl \\
      --selection _work/phase6/teacher_qualification/qualification.selection.jsonl \\
      --inventory _work/phase6/teacher_qualification/qualification.inventory.jsonl \\
      --inventory-manifest _work/phase6/teacher_qualification/qualification.inventory.manifest.json \\
      --out-dir _work/phase6/teacher_qualification \\
      --corpus-collection school_docs_phase5 --corpus-points 786 \\
      --corpus-source-tree-sha256 <64 位小写十六进制> \\
      --corpus-source-chunked-sha256 <64 位小写十六进制> \\
      --corpus-index-payload-sha256 <64 位小写十六进制> \\
      --corpus-index-chunking-profile phase5-plan_ingest-r1-r6 \\
      --prompt-version answer-candidate/1 --pack-version teacher-qualification/1
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _extra in (REPO / "backend", REPO / "训练集"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from compiler.qualification import PACK_PRODUCTS, freeze_pack  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """CLI 的全部参数 —— **没有** model / url / sampling / approve 开关。"""
    parser = argparse.ArgumentParser(
        description="冻结离线 teacher 资格考试包（只重放工具；不调模型、不生成答案）")
    parser.add_argument("--scenarios", nargs="+", required=True, metavar="JSONL",
                        help="已人审的资格考试场景（`ScenarioSpec` JSONL）")
    parser.add_argument("--rubric", required=True, help="criteria JSONL，与场景一对一")
    parser.add_argument("--selection", required=True,
                        help="选题台账 JSONL（每条被考虑过的候选都要有去向）")
    parser.add_argument("--inventory", required=True,
                        help="Task 2 的候选问法 inventory")
    parser.add_argument("--inventory-manifest", required=True, dest="inventory_manifest",
                        help="inventory 自己的 manifest（用来核对 inventory 的哈希）")
    parser.add_argument("--coverage-probes", required=True,
                        dest="coverage_probe",
                        help="已批准的人工覆盖探针 sidecar"
                             "（qualification.coverage-probes.jsonl）")
    parser.add_argument("--out-dir", required=True, dest="out_dir",
                        help="四份产物的输出目录")
    parser.add_argument("--corpus-collection", required=True,
                        dest="corpus_collection",
                        help="服务器实测的语料集合名（必须 school_docs_phase5）")
    parser.add_argument("--corpus-points", required=True, type=int,
                        dest="corpus_points", help="服务器实测的向量点数（必须 789）")
    parser.add_argument("--corpus-source-tree-sha256", required=True,
                        dest="corpus_source_tree_sha256",
                        help="91 份 _md 的字节级树哈希（64 位小写十六进制）")
    parser.add_argument("--corpus-source-chunked-sha256", required=True,
                        dest="corpus_source_chunked_sha256",
                        help="默认切分器在 _md 上的分块哈希 —— **未过滤**的全量块")
    parser.add_argument("--corpus-index-payload-sha256", required=True,
                        dest="corpus_index_payload_sha256",
                        help="从 school_docs_phase5 **实际 payload** 现算的快照哈希")
    parser.add_argument("--corpus-index-chunking-profile", required=True,
                        dest="corpus_index_chunking_profile",
                        help="索引切分画像，如 phase5-plan_ingest-r1-r6")
    parser.add_argument("--prompt-version", required=True, dest="prompt_version",
                        help="冻结时使用的提示词版本（只记版本，不记 prompt）")
    parser.add_argument("--pack-version", required=True, dest="pack_version",
                        help="包版本；改题必须升版本并保留旧 manifest")
    return parser


def main(argv=None, *, replay=None) -> dict:
    """CLI 入口。

    `replay` 只给测试注入替身用；生产路径为 `None`，于是走真实 `replay_step`。
    """
    args = build_parser().parse_args(argv)
    manifest = freeze_pack(
        scenario_paths=args.scenarios,
        rubric_path=args.rubric,
        selection_path=args.selection,
        inventory_path=args.inventory,
        inventory_manifest_path=args.inventory_manifest,
        coverage_probe_path=args.coverage_probe,
        out_dir=args.out_dir,
        corpus={"collection": args.corpus_collection,
                "points": args.corpus_points,
                "source_tree_sha256": args.corpus_source_tree_sha256,
                "source_chunked_sha256": args.corpus_source_chunked_sha256,
                "index_payload_sha256": args.corpus_index_payload_sha256,
                "index_chunking_profile": args.corpus_index_chunking_profile},
        prompt_version=args.prompt_version,
        pack_version=args.pack_version,
        replay=replay)
    print("[qualification] pack=%s 场景 %d 重放 %d"
          % (manifest["pack_version"], manifest["files"]["scenarios"]["rows"],
             len(manifest["replays"])))
    print("[qualification] 语料 %s / %d points ｜ 身份模型 %s"
          % (manifest["corpus"]["collection"], manifest["corpus"]["points"],
             manifest["corpus"]["identity_model"]))
    for _key in ("source_tree_sha256", "source_chunked_sha256",
                 "index_payload_sha256", "index_chunking_profile"):
        print("[qualification]   %-22s %s" % (_key, manifest["corpus"][_key]))
    print("[qualification] 产物：%s" % "、".join(PACK_PRODUCTS.values()))
    return manifest


if __name__ == "__main__":
    main()