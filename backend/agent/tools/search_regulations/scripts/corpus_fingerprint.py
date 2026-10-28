# -*- coding: utf-8 -*-
"""语料指纹 —— 纯函数：只读文件、只做哈希。不连 Qdrant、不加载模型。

为什么单独成模块：阶段5A 要在**重建索引前**钉住语料，并在重建后证明入库过程没改过语料。
旧索引的 `diagnostics._corpus_snapshot()` 也能算语料指纹，但它**必须先连上 Qdrant**才能跑；
而"重建前"那一刻恰恰还没有新集合。所以这里给一份不依赖任何服务的口径。

两个口径：
  · 文件树指纹 —— `sha256("\\n".join("relpath\\t文件 sha256"))`，按 relpath 排序。
    与切分无关，**任何改动都会变**，是最强的"语料没被动过"证据。
  · 分块指纹 —— `sha256("\\n".join(sorted("doc_id:chunk_idx:title:sha256(chunk_text)")))`，
    与 `diagnostics._corpus_snapshot()` **同式**，所以能和索引里算出来的直接比。

⚠️ 读文件必须走 `open(path, encoding="utf-8").read()` —— 通用换行会把 CRLF 折成 LF，
   这正是入库路径（`scripts/knowledge/ingest_local.py`）的读法。用 `open(path,"rb").read().decode()`
   **保留 `\\r\\n`**，会切出不同的块（实测 3384 → 3047，差 337 块）。
   文件级 SHA-256 仍按**原始字节**算，不受此影响。
"""
from __future__ import annotations

import hashlib
import os
import zlib
from typing import Callable, Iterable


def read_markdown(path: str | os.PathLike) -> str:
    """入库路径的读法（通用换行：CRLF → LF）。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def iter_markdown(root: str | os.PathLike) -> Iterable[str]:
    """按相对路径排序产出 root 下的所有 .md。"""
    found = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.lower().endswith(".md"):
                found.append(os.path.join(dirpath, name))
    return iter(sorted(found))


def per_file(root: str | os.PathLike) -> dict[str, str]:
    """{相对路径: 文件字节的 sha256}，相对路径统一用 `/`。"""
    out = {}
    for path in iter_markdown(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, "rb") as fh:
            out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def tree_digest(files: dict[str, str]) -> str:
    payload = "\n".join("%s\t%s" % (rel, files[rel]) for rel in sorted(files))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunked_digest(root: str | os.PathLike,
                   units_fn: Callable[[str], list[dict]] | None = None) -> str:
    """与 `diagnostics._corpus_snapshot()` 同式的分块指纹。

    `units_fn` 可注入（测试用假切分器），默认才去 import `indexing`
    —— 这样本模块在**没有 sentence-transformers 的环境里**也能做文件级校验。
    """
    if units_fn is None:
        from . import indexing
        units_fn = lambda text: indexing._build_index_units(text, "")  # noqa: E731
    identities = []
    for path in iter_markdown(root):
        title = os.path.splitext(os.path.basename(path))[0]
        doc_id = zlib.crc32(title.encode("utf-8")) & 0x7FFFFFFF
        for unit in units_fn(read_markdown(path)):
            digest = hashlib.sha256(unit["text"].encode("utf-8")).hexdigest()
            identities.append("%s:%s:%s:%s" % (doc_id, unit["chunk_idx"], title, digest))
    return hashlib.sha256("\n".join(sorted(identities)).encode("utf-8")).hexdigest()


def fingerprint(root: str | os.PathLike,
                units_fn: Callable[[str], list[dict]] | None = None) -> dict:
    files = per_file(root)
    return {"files": len(files), "tree_sha256": tree_digest(files),
            "chunked_sha256": chunked_digest(root, units_fn=units_fn),
            "per_file": files}