import json
from pathlib import Path

from agent.tools.search_regulations.scripts import bm25


def test_default_bm25_vocabulary_resolves_to_the_existing_project_artifact():
    path = Path(bm25._DEFAULT_VOCAB)
    assert path == Path(__file__).resolve().parents[5] / "model" / "bm25_vocab.json"
    assert path.is_file()
    vocab = bm25.load_vocab()
    assert vocab and vocab.get("terms")


def test_vocab_path_falls_back_to_the_project_default(monkeypatch):
    """不设配置 → 走默认路径。这是**生产路径**，必须逐字不变。"""
    from core.config import settings
    monkeypatch.setattr(settings, "bm25_vocab_path", "")
    assert bm25.vocab_path() == bm25._DEFAULT_VOCAB


def test_vocab_path_follows_the_configured_path(monkeypatch):
    """★ 阶段5A 裁决 三.5：集合名与词表路径成对切换。

    新集合 `school_docs_phase5` 必须有自己的词表；诊断它时**不得**依赖共享单例。
    """
    from core.config import settings
    monkeypatch.setattr(settings, "bm25_vocab_path", "model/bm25_vocab_phase5.json")
    assert bm25.vocab_path() == "model/bm25_vocab_phase5.json"


def test_save_and_load_honour_the_configured_path(monkeypatch, tmp_path):
    from core.config import settings
    target = tmp_path / "vocab_phase5.json"
    monkeypatch.setattr(settings, "bm25_vocab_path", str(target))
    bm25.save_vocab({"terms": ["甲"], "idf": [1.0], "avgdl": 1.0, "n": 1})
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["terms"] == ["甲"]
    assert bm25.load_vocab()["terms"] == ["甲"]


def test_configured_path_does_not_touch_the_shared_default(monkeypatch, tmp_path):
    """写新词表**不得**覆盖旧集合在用的那一份。"""
    from core.config import settings
    shared = Path(bm25._DEFAULT_VOCAB)
    before = shared.read_bytes()
    monkeypatch.setattr(settings, "bm25_vocab_path", str(tmp_path / "other.json"))
    bm25.save_vocab({"terms": ["乙"], "idf": [2.0], "avgdl": 2.0, "n": 1})
    assert shared.read_bytes() == before