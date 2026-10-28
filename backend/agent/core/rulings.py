"""Reviewed field-level corrections, separate from immutable source text."""
import json
from pathlib import Path
import unicodedata

PATH = Path(__file__).resolve().parents[2] / "data/document_rulings.json"


def ruling_for(title: str, source_sha256: str | None = None) -> dict | None:
    title = unicodedata.normalize("NFKC", title).removesuffix(".md").strip("《》")
    data = json.loads(PATH.read_text(encoding="utf-8"))
    if data.get("version") != "document-rulings/1":
        raise ValueError("不支持的人工裁决版本")
    for row in data["decisions"]:
        if title == unicodedata.normalize("NFKC", row["document_title"]):
            if source_sha256 is not None and source_sha256 != row["source_md_sha256"]:
                return None
            return dict(row)
    return None
