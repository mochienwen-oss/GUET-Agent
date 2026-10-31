# -*- coding: utf-8 -*-
"""语料指纹：确定性、对字节敏感、且与入库读法一致。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.tools.search_regulations.scripts.index_ops import corpus_fingerprint as cf  # noqa: E402


def _tree(tmp_path, files: dict):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _fake_units(text):
    """最小切分器：每行一块。测试不加载 sentence-transformers。"""
    return [{"chunk_idx": i, "text": line}
            for i, line in enumerate(text.splitlines()) if line.strip()]


def test_same_input_gives_the_same_fingerprint(tmp_path):
    root = _tree(tmp_path, {"a.md": "第一行\n第二行\n"})
    first = cf.fingerprint(root, units_fn=_fake_units)
    second = cf.fingerprint(root, units_fn=_fake_units)
    assert first["tree_sha256"] == second["tree_sha256"]
    assert first["chunked_sha256"] == second["chunked_sha256"]


def test_one_byte_change_moves_both_digests(tmp_path):
    root = _tree(tmp_path, {"a.md": "第一行\n第二行\n"})
    before = cf.fingerprint(root, units_fn=_fake_units)
    (root / "a.md").write_text("第一行\n第二行。\n", encoding="utf-8")
    after = cf.fingerprint(root, units_fn=_fake_units)
    assert before["tree_sha256"] != after["tree_sha256"]
    assert before["chunked_sha256"] != after["chunked_sha256"]


def test_per_file_lists_every_markdown_with_slash_paths(tmp_path):
    root = _tree(tmp_path, {"x/a.md": "甲\n", "x/b.md": "乙\n", "x/skip.txt": "no\n"})
    files = cf.per_file(root)
    assert sorted(files) == ["x/a.md", "x/b.md"]


def test_read_markdown_folds_crlf_to_lf(tmp_path):
    """★ 入库路径的读法：CRLF 折成 LF。

    `rb().decode()` 会保留 \\r\\n，切出不同的块（实测 3384 vs 3047）。
    这里把口径钉死，免得以后有人"顺手"改成二进制读。
    """
    path = tmp_path / "crlf.md"
    path.write_bytes("甲\r\n乙\r\n".encode("utf-8"))
    assert cf.read_markdown(path) == "甲\n乙\n"
    assert path.read_bytes() == "甲\r\n乙\r\n".encode("utf-8")


def test_chunked_digest_distinguishes_documents_with_the_same_text(tmp_path):
    """同样正文的两份文档必须算出不同的分块指纹（doc_id 参与身份）。"""
    root = _tree(tmp_path, {"甲.md": "同一条\n", "乙.md": "同一条\n"})
    both = cf.chunked_digest(root, units_fn=_fake_units)
    (root / "乙.md").unlink()
    only_one = cf.chunked_digest(root, units_fn=_fake_units)
    assert both != only_one