# -*- coding: utf-8 -*-
"""培养方案来源记录 —— 纯函数：只读文件、只做哈希。不连库、不加载模型。

为什么单独成模块：`build_plan_db.py` 需要给**每一行**一个可复算的身份，
而"行号"是个坏身份 —— 阶段5B 审计的 F2 就是实证：
`CREDIT_FIXES` 按 `(文件名, 行号)` 索引，`_md` 被重洗之后，
第 337 行从「侧信道分析技术」变成了「军事技能」，于是**修错了课、真目标仍在丢**。

所以这里给两样东西：
  · `row_hash(line)` —— 一行的**内容身份**。行号会漂，内容不会。
  · `source_fingerprint(dir)` —— 整棵语料树的指纹，用来标记"这批数据是从哪一版语料抽的"。

⚠️ 读文件必须走 `open(path, encoding="utf-8").read()` —— 91 份 md 全是 CRLF，
   通用换行会把它折成 LF，这正是入库路径的读法。用 `rb().decode()` 会得到**不同的行**。
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from typing import Iterable


def read_text(path: str | os.PathLike) -> str:
    """入库路径的读法（通用换行：CRLF → LF）。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_lines(path: str | os.PathLike) -> list[str]:
    return read_text(path).split("\n")


def row_hash(line: str) -> str:
    """一行的内容指纹：NFKC 规范化 + 去掉全部空白后取 sha256。

    去空白是**有意的**：重洗时常见的是排版性空白变化（PDF 字距、表格补空格），
    那不算内容变了。真正的文字改动会让哈希变。
    """
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line or ""))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def iter_markdown(root: str | os.PathLike) -> Iterable[str]:
    found = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.lower().endswith(".md"):
                found.append(os.path.join(dirpath, name))
    return iter(sorted(found))


def per_file(root: str | os.PathLike) -> dict[str, str]:
    """{相对路径: 文件字节 sha256}，相对路径统一用 `/`。"""
    out = {}
    for path in iter_markdown(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, "rb") as fh:
            out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def source_fingerprint(root: str | os.PathLike) -> str:
    """整树指纹：`sha256("\\n".join("relpath\\t文件 sha256"))`，按相对路径排序。"""
    files = per_file(root)
    payload = "\n".join("%s\t%s" % (rel, files[rel]) for rel in sorted(files))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()