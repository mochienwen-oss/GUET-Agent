"""Read-only quality checks for model-produced regulation-search arguments."""
import json
import re
from dataclasses import dataclass
from pathlib import Path


_LEXICON_PATH = Path(__file__).resolve().parents[2] / "data" / "query_expansions.json"
_FACT_TOKEN = re.compile(
    r"第[一二三四五六七八九十百零〇0-9]+条|"
    r"〔\d{4}〕|\d+(?:\.\d+)?(?:万元|元|学分|学时|分|%|％)"
)


@dataclass(frozen=True)
class QueryIssue:
    code: str
    detail: str


def load_lexicon(path: Path = _LEXICON_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reviewed_expansions(user_text: str, lexicon: dict) -> set[str]:
    allowed = set()
    for row in lexicon.get("entries") or []:
        if row.get("reviewed") is not True:
            continue
        terms = [row.get("canonical") or "", *(row.get("aliases") or [])]
        terms = [term for term in terms if term]
        if any(term in user_text for term in terms):
            allowed.update(terms)
    return allowed


def _looks_like_exact_title(text: str) -> bool:
    return ("桂林电子科技大学" in text
            or (text.startswith("《") and text.endswith("》"))
            or re.search(r"〔\d{4}〕\d+号", text) is not None)


def audit_query_args(user_text: str, query: str, keywords: list[str],
                     lexicon: dict) -> list[QueryIssue]:
    """Report query drift without silently rewriting model output."""
    issues = []
    user_text = user_text or ""
    query = (query or "").strip()
    keywords = list(keywords or [])
    if not query:
        issues.append(QueryIssue("blank_semantic_query", "query 为空"))
    if len(keywords) > 12:
        issues.append(QueryIssue("too_many_keywords", "keywords 超过 12 项"))

    allowed = _reviewed_expansions(user_text, lexicon)
    for keyword in keywords:
        keyword = (keyword or "").strip()
        if not keyword:
            continue
        if _looks_like_exact_title(keyword) and keyword not in user_text:
            issues.append(QueryIssue("answer_title_leak", keyword))
        for token in _FACT_TOKEN.findall(keyword):
            if token not in user_text:
                issues.append(QueryIssue("numeric_fact_expansion", token))
                break
        if keyword not in user_text and keyword not in allowed:
            issues.append(QueryIssue("unknown_expansion", keyword))
    return issues
