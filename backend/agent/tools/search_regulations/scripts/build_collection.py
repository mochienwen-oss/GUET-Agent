# -*- coding: utf-8 -*-
"""`school_docs_phase5` 的**可重放**数据构建工具（阶段5A）。

为什么是一个正式工具而不是一次性脚本：语料会变（`_md/` 在 2026-10-13 当天被清洗了 4 批），
每变一次就要能**用同一条判据**重建并复算指纹。所以规则写在这里，不写在临时脚本里。

入库规则（阶段5A 裁决）：

| # | 规则 | 判据 |
|---|---|---|
| R1 | 规章制度正文入库 | 非表格的 `_md/规章制度/*` |
| R2 | 规章有效表格**整表**入库 | 是表格 **且** 通过 `table_check` 的单表列校验 |
| R3 | 培养方案叙述正文入库 | 非表格、且不在 R4/R6 的排除节内 |
| R4 | 教学进程课程表**不入** | `heading_path` 命中 `教学进程计划表` / `培养计划学时` / `学时、学分统计表` / `学分统计表` |
| R5 | 先修后修关系图 OCR 堆叠**不入** | 堆叠判据（当前语料命中 0，判据保留） |
| R6 | **两类矩阵都不入** | `heading_path` 命中 `毕业要求对培养目标的支撑矩阵` / `课程计划与毕业要求的对应矩阵` |

★ 只**筛掉块**，不重切、不改块边界：`chunk_idx` 保留原编号（会留空号），
  这样任何一块都能回溯到原文档的原始切分位置。
★ 原始 `_md` **一个字节都不改**（R6 的另一半：表要留着做结构化数据来源与溯源）。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import zlib
from pathlib import Path

from core.config import settings

from . import bm25, corpus_fingerprint as cf, indexing, table_check

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_ROOT = ROOT / "_md"
DEFAULT_COLLECTION = "school_docs_phase5"
DEFAULT_VOCAB = ROOT / "model" / "bm25_vocab_phase5.json"

#: **索引切分画像** —— 如实描述 `school_docs_phase5` 是怎么建出来的：
#: 默认切分器（`indexing._build_index_units`）切出的块，再按 R1–R6 **过滤**，
#: `chunk_idx` **保留原编号**（被筛掉的位置留空号）。
#: ★ 这不是「另一套切分器」：块边界与文本都与默认切分**逐块相同**，
#:   索引侧只是它的**过滤子集**（实测 3381 → 786）。
INDEX_CHUNKING_PROFILE = "phase5-plan_ingest-r1-r6"

#: R4 + R6 的排除章节（**只看 heading_path**，不改正文）
EXCLUDE_HINTS = (
    "教学进程计划表", "培养计划学时", "学时、学分统计表", "学分统计表",
    "毕业要求对培养目标的支撑矩阵", "课程计划与毕业要求的对应矩阵",
)

#: 命中这些特征的行不像"有内容的表格"（R5 的兜底判据，当前语料为空转）
_STACK_MARK = 0.55


def excluded_by(heading_path) -> str | None:
    """这个块属于要排除的章节吗？返回命中的判据（便于统计），否则 None。"""
    for part in heading_path or ():
        for hint in EXCLUDE_HINTS:
            if hint in part:
                return hint
    return None


def is_table(text: str) -> bool:
    lines = (text or "").lstrip().split("\n")
    return len(lines) >= 2 and lines[0].startswith("|") and indexing._is_table_sep(lines[1])


def _category(rel: str) -> str:
    head = rel.replace("\\", "/").split("/")[0]
    return head if head in ("规章制度", "培养方案") else ""


def plan_ingest(root=DEFAULT_ROOT, units_fn=None) -> dict:
    """纯函数：只读 + 切分 + 筛选。**不连 Qdrant、不加载模型**。

    返回 `{"kept", "excluded", "tables", "counts", "per_doc"}`。
    `kept` 每项含 `chunk_id` / `title` / `doc_id` / `category` / `unit`（原始 unit，原样透传给入库）。
    """
    if units_fn is None:
        units_fn = lambda text: indexing._build_index_units(text, "")  # noqa: E731

    kept, excluded, per_doc, per_cat = [], {}, {}, {}
    for path in cf.iter_markdown(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        title = os.path.splitext(os.path.basename(path))[0]
        doc_id = zlib.crc32(title.encode("utf-8")) & 0x7FFFFFFF
        category = _category(rel)
        for unit in units_fn(cf.read_markdown(path)):
            hint = excluded_by(unit["heading_path"])
            if hint:
                excluded[hint] = excluded.get(hint, 0) + 1
                continue
            kept.append({"chunk_id": "%d:%d" % (doc_id, unit["chunk_idx"]),
                         "title": title, "doc_id": doc_id, "category": category,
                         "rel": rel, "unit": unit})
            per_doc[title] = per_doc.get(title, 0) + 1
            per_cat[category] = per_cat.get(category, 0) + 1

    table_units = [{"chunk_id": k["chunk_id"], "title": k["title"], "text": k["unit"]["text"]}
                   for k in kept if is_table(k["unit"]["text"])]
    tables = table_check.check_tables(table_units)
    tables["by_category"] = {}
    for k in kept:
        if is_table(k["unit"]["text"]):
            tables["by_category"][k["category"]] = tables["by_category"].get(k["category"], 0) + 1

    return {
        "kept": kept,
        "excluded": excluded,
        "tables": tables,
        "counts": {"files": len({k["rel"] for k in kept}), "kept": len(kept),
                   "excluded": sum(excluded.values()),
                   "documents": len(per_doc)},
        "per_doc": per_doc,
        "per_category": per_cat,
    }


@contextlib.contextmanager
def _temporary_settings(**overrides):
    """临时改写 settings（集合名 / 词表路径），退出时恢复。

    集合名与词表路径**必须成对**（裁决 三.5）：词表是全语料统计量，
    换集合不换词表 = 查询侧与文档侧用的 idf 不是同一套。
    """
    original = {key: getattr(settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(settings, key, value)


def build(collection=DEFAULT_COLLECTION, vocab_path=DEFAULT_VOCAB, root=DEFAULT_ROOT,
          *, qdrant_path=None, qdrant_url=None, dry_run=True, plan=None,
          overwrite=False, client=None) -> dict:
    """按入库规则建集合。`dry_run=True` 只统计，不碰任何库。

    `client` 可以直接给一个已打开的 Qdrant 客户端 —— 内嵌模式（`path=`）不允许
    同一目录被两个实例同时打开，调用方想先看一眼旧集合再建新集合时必须复用同一个。
    """
    plan = plan or plan_ingest(root)
    report = {"collection": collection, "vocab_path": str(vocab_path),
              "root": str(root), "dry_run": bool(dry_run),
              "counts": plan["counts"], "excluded": plan["excluded"],
              "per_category": plan["per_category"],
              "tables": {"seen": plan["tables"]["tables_seen"],
                         "passed": plan["tables"]["passed"],
                         "failed": len(plan["tables"]["failed"]),
                         "by_category": plan["tables"]["by_category"]}}

    if plan["tables"]["failed"]:
        # 裁决 二.4：列对不齐的表**不得静默入库**，写隔离报告并**就此打住** ——
        # dry_run 与否都要返回（先前写成只在 dry_run 时 return，真写库时会穿过去，
        # 把隔离表连同其余块一起建进集合）。
        report["isolated_tables"] = plan["tables"]["failed"]
        report["ok"] = False
        report["reason"] = "table_validation_failed"
        return report

    if dry_run:
        report["ok"] = True
        return report

    if client is None:
        from qdrant_client import QdrantClient
        client = (QdrantClient(path=str(qdrant_path)) if qdrant_path
                  else QdrantClient(url=qdrant_url))
    indexing._client = client
    try:
        if client.collection_exists(collection):
            if not overwrite:
                report.update({"ok": False, "reason": "collection_exists"})
                return report
            # `overwrite` 必须先删再建。仅 upsert 会在新语料块数减少时留下旧 point，
            # 让索引指纹、BM25 词表和实际检索集合悄悄不一致。
            client.delete_collection(collection)
        with _temporary_settings(qdrant_collection=collection, bm25_vocab_path=str(vocab_path)):
            indexing.ensure_collection()
            # ★ 词表只由**实际入库的块**统计 —— 与语料一一对应，不能拿全量 `_md` 统计
            vocab = bm25.build_vocab([k["unit"]["text"] for k in plan["kept"]])
            bm25.save_vocab(vocab, str(vocab_path))
            by_doc: dict[str, list] = {}
            for k in plan["kept"]:
                by_doc.setdefault(k["title"], []).append(k["unit"])
            for title, units in by_doc.items():
                doc_id = zlib.crc32(title.encode("utf-8")) & 0x7FFFFFFF
                indexing._index_document(doc_id, title, "", units=units)
            info = client.get_collection(collection)
        report.update({"ok": True, "points": info.points_count,
                       "documents": len(by_doc), "vocab_terms": len(vocab["terms"]),
                       "corpus": cf.fingerprint(root)})
    finally:
        indexing._client = None
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Build school_docs_phase5 from _md per phase-5A rules")
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--vocab", default=str(DEFAULT_VOCAB))
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--qdrant-path", default=None, help="内嵌 Qdrant 数据目录（无需服务）")
    ap.add_argument("--qdrant-url", default=None, help="Qdrant 服务地址")
    ap.add_argument("--write", action="store_true", help="真的写库；默认只空转统计")
    ap.add_argument("--overwrite", action="store_true", help="目标集合已存在时覆盖")
    ap.add_argument("--report", default=None, help="把构建报告写成 JSON")
    args = ap.parse_args()

    report = build(args.collection, args.vocab, args.root,
                   qdrant_path=args.qdrant_path, qdrant_url=args.qdrant_url,
                   dry_run=not args.write, overwrite=args.overwrite)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "corpus"},
                     ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
