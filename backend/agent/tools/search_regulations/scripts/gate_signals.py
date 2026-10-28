# -*- coding: utf-8 -*-
"""Pure runtime-observable signals for the evidence gate.

Everything here is derived from a real retrieval call: candidate rows, the
dense/BM25 candidate lists, the reviewed anchor index, and degradation flags.
No expected answer, expected document, label, scenario id, or evaluation
membership may enter this module. No retrieval/network I/O; the BM25 tokenizer
may lazily load its local jieba dictionary.
"""
from dataclasses import asdict, dataclass
import re
import unicodedata

from agent.core.citations import short_title

from .bm25 import tokenize as _bm25_tokenize
from .coverage import _RE_DOCNO, docno_of

_QUERY_KEY_DROP = re.compile(r"[\s《》()（）〔〕\[\]·,，。;；:：、\-—_\"'“”‘’]+")
_TITLE_NOISE = re.compile(r"[（(](?:试行|\d{4}年修订)[）)]")
_OURS = "桂林电子科技大学"
_UNSUPPORTED_ACTION = re.compile(
    r"(?:(?:帮我|替我|给我)(?:直接)?(?:写|生成|完成)|代写)"
    r"[^\n，。？！]{0,16}(?:论文|作业|报告|课程设计)"
    r"|(?:绕过|规避|逃避)[^\n，。？！]{0,10}"
    r"(?:检测|审查|监考|规定|限制)"
)


def _clean(value: str) -> str:
    return _QUERY_KEY_DROP.sub("", unicodedata.normalize("NFKC", value or ""))


def title_key(value: str) -> str:
    """Normalize a document/title for exact anchor comparison."""
    text = unicodedata.normalize("NFKC", value or "")
    text = short_title(text)
    text = text.replace(_OURS, "")
    text = _TITLE_NOISE.sub("", text)
    return _clean(text)


def query_key(value: str) -> str:
    return _clean(value).replace(_OURS, "")


def _grams(text: str) -> set:
    """Character bigrams for CJK, whole tokens for alphanumeric runs."""
    out = set()
    for token in re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", unicodedata.normalize("NFKC", text or "").lower()):
        if token[0].isascii():
            out.add(token)
        elif len(token) == 1:
            out.add(token)
        else:
            out.update(token[i:i + 2] for i in range(len(token) - 1))
    return out


def _coverage(query: str, text: str) -> float:
    asked = _grams(query)
    if not asked:
        return 0.0
    return round(len(asked & _grams(text)) / len(asked), 4)


def _other_university(query: str) -> bool:
    """Reuse the reviewed external-university filter.

    It only matches names ending in 大学/学院 (after stripping our own name and
    fixed terms). A bare "外校" is deliberately NOT a conflict: "去外校访学"
    is an in-scope question governed by our own staff-training rules.
    """
    try:
        from .intent import _has_other_univ
        return bool(_has_other_univ(query))
    except Exception:
        return False


def unsupported_action(query: str) -> bool:
    """Whether the query asks the evidence tool to perform an action.

    This is deliberately narrow: ordinary questions about writing rules or
    minimum lengths are not matched. It is a runtime-observable scope signal,
    not a content label or evaluation lookup.
    """
    text = unicodedata.normalize("NFKC", query or "")
    return bool(_UNSUPPORTED_ACTION.search(text))


def _doc_id(row: dict):
    value = row.get("document_id", row.get("doc_id"))
    return None if value is None else str(value)


def _row_source(row: dict) -> str:
    # Raw retrieval rows use "source"; trace rows use "title".
    return row.get("source") or row.get("title") or ""


def _row_text(row: dict) -> str:
    # Raw rows carry the full text; trace rows only carry a preview.
    return row.get("text") or row.get("snippet") or row.get("text_preview") or ""


def _top_doc(rows: list[dict]):
    for row in rows or []:
        did = _doc_id(row)
        if did is not None:
            return did
    return None


@dataclass(frozen=True)
class AnchorIndex:
    """Reviewed anchors: corpus document titles, document numbers, entities."""

    titles: tuple[tuple[str, str], ...] = ()
    docnos: tuple[str, ...] = ()
    entities: tuple[tuple[str, str], ...] = ()


def build_anchor_index(*, sources=(), docnos=(), entities=()) -> AnchorIndex:
    """Build a de-duplicated index from reviewed corpus/catalog values."""
    titles, seen = [], set()
    for raw in sources:
        key = title_key(raw)
        if len(key) < 6 or key in seen:
            continue
        seen.add(key)
        titles.append((key, raw))
    entity_rows, seen_entities = [], set()
    for raw in entities:
        key = query_key(raw)
        if len(key) < 2 or key in seen_entities:
            continue
        seen_entities.add(key)
        entity_rows.append((key, raw))
    return AnchorIndex(titles=tuple(sorted(titles)),
                       docnos=tuple(sorted({d for d in docnos if d})),
                       entities=tuple(sorted(entity_rows)))


def _matches(anchor_key: str, candidate_key: str) -> bool:
    return bool(anchor_key and candidate_key and
                (anchor_key in candidate_key or candidate_key in anchor_key))


def find_anchors(query: str, anchors: AnchorIndex | None) -> dict:
    """Deterministic exact anchors present in the query text."""
    if anchors is None:
        return {"title": None, "docno": None, "entity": None}
    qkey = query_key(query)
    title_matches = [(key, raw) for key, raw in anchors.titles if key in qkey]
    title = max(title_matches, key=lambda item: (len(item[0]), item[0]))[1] \
        if title_matches else None
    entity_matches = [(key, raw) for key, raw in anchors.entities if key in qkey]
    entity = max(entity_matches, key=lambda item: (len(item[0]), item[0]))[1] \
        if entity_matches else None
    found_docnos = []
    for match in _RE_DOCNO.finditer(query or ""):
        key = docno_of(match.group(0))
        if key and key in anchors.docnos:
            found_docnos.append(match.group(0))
    docno = found_docnos[0] if found_docnos else None
    return {"title": title, "docno": docno, "entity": entity}


# ---------------------------------------------------------------------------
# Deterministic claim anchors (experimental; enabled only by policies with
# require_semantic_recovery=True). Inputs are limited to the user's query,
# explicit keywords, reviewed catalog aliases, the BM25 tokenizer, and the
# candidate title/heading/body. No expectation, label, scenario id, or grader
# verdict is read here.
# ---------------------------------------------------------------------------

_CLAIM_GENERIC = set("""
的 了 和 及 与 或 在 是 为 对 按 从 到 由 之 其 该 本 等 有 无 不 也 就 而 并
可以 应当 应该 必须 根据 依据 按照 关于 对于 以及 或者 一个 进行 第 条 章 节 款 项 规定 办法 通知
学生 学校 老师 教师 咱们 我们 你们 他们 请问 想问 一下 这个 那个 这些 那些 到底 就是 的话
什么 怎么 怎样 如何 哪些 哪个 哪里 多少 几年 多久 是否 有没有 是不是 不是 还是 包括 属于
相关 有关 方面 问题 时候 一般 通常 具体 现在 目前 以后 之前 能 会 被 想 拿 都 才 够 呀 啊
呢 吗 吧 嘛 哈 啦 呗 哦 喔 要是 我发 问问 学长 常说 就是 还是 这个 那个 我想 问下
""".split())

# Shape words describe the question, not the subject matter. They are checked
# by claim_shape_supported and cannot by themselves count as specific anchors.
_CLAIM_SHAPE_WORDS = set("""
情况 条件 情形 要求 标准 资格 流程 手续 步骤 程序 材料 适用 适用范围 范围 对象
定义 含义 意思 啥意思 什么意思 是什么 什么叫 指什么 哪些 哪几类 种类 分为 门槛
之一 上限 下限 最高 最低 比例 占比 多少 几 几年 多久 怎么 怎样 如何 是否 能不能
什么时候 何时 哪天 哪一年 几月
""".split())

_RE_NUM_PHRASE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个工作日|工作日|个月|年|月|日|天|周|小时|分钟|学分|学时|分|%|％|人|次|项|篇|门|元|万元|周岁|岁|级|米|平方米)"
    r"|[一二三四五六七八九十百千万两]+(?:个工作日|工作日|个月|年|月|日|天|周|小时|分钟|学分|学时|分|人|次|项|篇|门|元|万元|周岁|岁|级)")
_RE_ROLE_PHRASE = re.compile(r"第\s*[0-9一二三四五六七八九十]+\s*作者")
_RE_CLAIM_BOILERPLATE = re.compile(
    r"^(?:第[一二三四五六七八九十百千0-9]+[章节条款项部分]|第[一二三四五六七八九十百千0-9]+"
    r"|本(?:细则|规定|办法|方案|章|条|文)|该(?:校|规定|办法|细则))$")
_RE_ORG_PHRASE = re.compile(
    r"[一-鿿A-Za-z0-9]{2,24}?(?:代表大会|委员会|领导小组|研究生院|教务处|学生处|"
    r"人事处|财务处|研究生工作部|图书馆|学院|大学|工会|团委)")
_RE_ORG_PREFIX = re.compile(
    r"^(?:请问|我想问下|想问下|咱们学校的|我们学校的|咱们的|我们的|本校的|学校的|"
    r"哪些情况下|什么情况下|那|该|这个|我们院准备组建一个|我们院准备组建|"
    r"我们院组建一个|我们院组建|准备组建一个|准备组建|组建一个|我们(?=学院$))+")
_RE_QUERY_UNIT = re.compile(
    r"(个工作日|工作日|个月|年|月|日|天|周|小时|分钟|学分|学时|分|%|％|人|次|项|篇|门|元|万元|周岁|岁|级|米|平方米)")
_RE_QUANT = re.compile(
    r"(?:不少于|不超过|不低于|不高于|至少|至多|最高|最低|比例|占比|以上|以下)[^。；\n]{0,16}?(?:\d|[一二三四五六七八九十百千万两])"
    r"|(?:\d|[一二三四五六七八九十百千万两])[^。；\n]{0,16}?(?:不少于|不超过|不低于|不高于|至少|至多|最高|最低|比例|占比|以上|以下)")
_RE_PROC_WORD = re.compile(
    r"办理|审核|审批|公示|申请|提交|报送|备案|受理|复核|申诉|程序|流程|步骤|手续|批准|核准|签发|登记|填写|审查|评审|组织|推荐|下达|公布")
_RE_COND_WORD = re.compile(
    r"条件|情形|之一|下列|符合|满足|具备|资格|要求|若|如果|凡|情况")
_RE_APPL_WORD = re.compile(
    r"适用|适用范围|适用对象|对象|针对|凡|本办法|本规定|本细则|本条件|本方案|本规则|执行|参照执行"
    r"|可以|不得|应当|应予|不予|允许|禁止|批准|同意|授权")
_RE_DEF_WORD = re.compile(r"是指|所称|定义|含义|意思|指的是|系指")
_RE_ENUM = re.compile(
    r"(?:^|\n)\s*(?:[（(]\s*[一二三四五六七八九十\d]+\s*[)）]|\d+[.、．]|[一二三四五六七八九十]+、|第[一二三四五六七八九十\d]+步)")

_RE_Q_NUMERIC = re.compile(
    r"\d+|多少|几(?:年|天|个|门|篇|分|次|人|项|周|月|小时|工作日|学时)?|多久|多长时间|多长"
    r"|什么时候|何时|哪天|哪一年|几月"
    r"|比例|占比|上限|下限|最高|最低|不少于|不超过|不低于|不高于|以上|以下")
_RE_Q_PROCEDURE = re.compile(
    r"怎么|怎样|如何|流程|手续|步骤|程序|办理|申请|审批|审核|公示|提交|报送|备案|受理|复核|申诉|材料")
_RE_Q_DEFINITION = re.compile(r"是什么|啥意思|什么意思|什么叫|含义|定义|指什么|是指")
_RE_Q_APPLICABILITY = re.compile(
    r"适用|适用范围|对象|针对|是否|是不是|算不算|属于|能不能|可以[^？?]{0,10}吗|能[^？?]{0,10}吗|资格")
_RE_Q_CONDITION = re.compile(
    r"条件|情形|情况|哪些|哪几类|种类|分为|标准|要求|门槛|之一|什么情况|什么条件")


def _add_term(terms: list[str], value: str) -> None:
    value = (value or "").strip()
    if value and value not in terms:
        terms.append(value)


def claim_terms(query: str, keywords=()) -> tuple[str, ...]:
    """High-information phrases from user-provided text only.

    Numbers with units and role phrases are kept as whole phrases. BM25
    ``cut_for_search`` supplies overlapping word candidates; generic and
    shape-only words are dropped so they cannot support a claim by themselves.
    """
    text = unicodedata.normalize("NFKC", " ".join([query or "", *keywords]))
    terms: list[str] = []
    for match in _RE_NUM_PHRASE.finditer(text):
        _add_term(terms, match.group(0))
    for match in _RE_ROLE_PHRASE.finditer(text):
        _add_term(terms, match.group(0))
    for token in _bm25_tokenize(text):
        token = token.strip()
        if not token or token in _CLAIM_GENERIC or token in _CLAIM_SHAPE_WORDS:
            continue
        if token.isdigit() or len(token) < 2:
            continue
        if _RE_CLAIM_BOILERPLATE.match(token):
            continue
        _add_term(terms, token)
    ordered = tuple(sorted(set(terms), key=lambda value: (-len(value), value)))
    # ``cut_for_search`` deliberately emits overlapping fragments. They are
    # useful for retrieval but would let one phrase cast several semantic
    # votes (e.g. 生物医学/医学/生物), so keep only maximal concepts here.
    return tuple(value for value in ordered
                 if not any(value != other and value in other
                            for other in ordered))


def claim_phrases(query: str) -> tuple[str, ...]:
    """Named institution/organization phrases that must appear verbatim.

    Bag-of-words matching cannot tell "研究生代表大会" from "学生代表大会".
    For these organization heads we require the full normalized phrase in the
    evidence; if the query has no such phrase this guard is inert (None later).
    """
    text = unicodedata.normalize("NFKC", query or "")
    phrases: list[str] = []
    for match in _RE_ORG_PHRASE.finditer(text):
        phrase = _clean(match.group(0))
        phrase = _RE_ORG_PREFIX.sub("", phrase)
        phrase = re.sub(r"^(?:桂林电子科技大学|我校|本校|学校)", "", phrase)
        if len(phrase) >= 4 and phrase not in phrases:
            phrases.append(phrase)
    return tuple(phrases)


def claim_shape(query: str) -> str | None:
    """One deterministic question shape, or None when no shape is asserted."""
    text = unicodedata.normalize("NFKC", query or "")
    if _RE_Q_NUMERIC.search(text):
        return "numeric"
    if _RE_Q_APPLICABILITY.search(text):
        return "applicability"
    if _RE_Q_PROCEDURE.search(text):
        return "procedure"
    if _RE_Q_CONDITION.search(text):
        return "condition"
    if _RE_Q_DEFINITION.search(text):
        return "definition"
    return None


def _shape_supported(shape: str | None, evidence: str, query: str) -> bool | None:
    """Does the evidence itself carry the structure the question asks for?"""
    if shape is None or shape == "generic":
        return None
    text = unicodedata.normalize("NFKC", evidence or "")
    if shape == "numeric":
        wanted = set(_RE_QUERY_UNIT.findall(unicodedata.normalize("NFKC", query or "")))
        numbers = [match.group(0) for match in _RE_NUM_PHRASE.finditer(text)]
        if wanted:
            if any(any(unit in value for unit in wanted) for value in numbers):
                return True
        elif numbers:
            return True
        return bool(_RE_QUANT.search(text))
    if shape == "procedure":
        words = set(_RE_PROC_WORD.findall(text))
        if not words:
            return False
        if len(words) >= 2:
            return True
        return bool(_RE_ENUM.search(text) or any(
            word in words for word in ("公示", "审核", "审批", "受理", "复核", "申诉", "备案")))
    if shape == "condition":
        words = set(_RE_COND_WORD.findall(text))
        if not words:
            return False
        if "之一" in words or "下列" in words:
            return True
        if _RE_ENUM.search(text):
            return True
        if re.search(r"条件[^。；\n]{0,24}(?:为|是|包括|如下|[：:])", text):
            return True
        return len(words) >= 2
    if shape == "applicability":
        return bool(_RE_APPL_WORD.search(text))
    if shape == "definition":
        return bool(_RE_DEF_WORD.search(text))
    return None


def claim_anchor(query: str, keywords, evidence_texts) -> dict:
    """Compute claim anchors against the top evidence only.

    ``evidence_texts`` should already include the formal title and
    heading_path, so a candidate's own identity counts as evidence text.
    """
    terms = claim_terms(query, keywords or ())
    text = "\n".join(item for item in evidence_texts if item)
    normalized = _clean(text).lower()
    hits = (tuple(term for term in terms
                  if _clean(term).lower() in normalized)
            if normalized else ())
    coverage = (round(len(hits) / len(terms), 4)
                if terms and normalized else None)
    shape = claim_shape(query)
    phrases = claim_phrases(query)
    phrase_hits = tuple(phrase for phrase in phrases if phrase in normalized)
    phrase_supported = ((len(phrase_hits) == len(phrases)) if phrases else None)
    return {
        "claim_anchor_terms": terms,
        "claim_anchor_hits": hits,
        "claim_anchor_coverage": coverage,
        "claim_shape": shape,
        "claim_shape_supported": _shape_supported(shape, text, query),
        "claim_anchor_phrases": phrases,
        "claim_anchor_phrase_hits": phrase_hits,
        "claim_anchor_phrase_supported": phrase_supported,
    }


def best_claim_anchor(query: str, keywords, evidence_groups) -> dict:
    """Return the strongest claim match from one-document evidence groups.

    A response may expose several selected chunks, so a multi-part claim may
    be supported across chunks from the same regulation. Groups must already
    be separated by document; this function never combines different sources.
    """
    results = [claim_anchor(query, keywords, group)
               for group in evidence_groups if any(group)]
    if not results:
        return claim_anchor(query, keywords, ())

    def quality(item):
        phrase = item["claim_anchor_phrase_supported"]
        shape = item["claim_shape_supported"]
        return (
            phrase is not False,
            shape is True,
            item["claim_anchor_coverage"] or 0.0,
            len(item["claim_anchor_hits"]),
        )

    return max(results, key=quality)


@dataclass(frozen=True)
class GateSignals:
    best_rerank: float | None
    second_rerank: float | None
    rerank_margin: float | None
    best_dense: float | None
    dense_bm25_top1_agreement: bool | None
    dense_bm25_top3_overlap: float | None
    top_doc_count: int
    top_doc_share: float
    top_doc_id: str | None
    title_anchor: str | None
    title_anchor_hit: bool | None
    docno_anchor: str | None
    docno_anchor_hit: bool | None
    entity_anchor: str | None
    entity_anchor_hit: bool | None
    token_coverage_top1: float | None
    token_coverage_top3: float | None
    candidate_count: int
    bm25_candidate_count: int
    bm25_available: bool
    focus_document_id: str | None
    scope_conflict: bool
    scope_conflict_kind: str | None
    absent_reference: str
    degradations: tuple[dict, ...]
    selected_document_ids: tuple[str, ...]
    # Runtime-observable top candidate title; used only for bounded corrective
    # recall, never as proof that the candidate answers the question.
    top_document_title: str | None = None
    claim_anchor_terms: tuple[str, ...] = ()
    claim_anchor_hits: tuple[str, ...] = ()
    claim_anchor_coverage: float | None = None
    claim_shape: str | None = None
    claim_shape_supported: bool | None = None
    claim_anchor_phrases: tuple[str, ...] = ()
    claim_anchor_phrase_hits: tuple[str, ...] = ()
    claim_anchor_phrase_supported: bool | None = None
    rerank_score_version: str = "legacy-double-sigmoid/1"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["degradations"] = [dict(item) for item in self.degradations]
        data["claim_anchor_terms"] = list(self.claim_anchor_terms)
        data["claim_anchor_hits"] = list(self.claim_anchor_hits)
        data["claim_anchor_phrases"] = list(self.claim_anchor_phrases)
        data["claim_anchor_phrase_hits"] = list(self.claim_anchor_phrase_hits)
        return data


def collect(*, query: str, ranked: list[dict], selected: list[dict] | None = None,
            dense_rows: list[dict] | None = None,
            bm25_rows: list[dict] | None = None,
            bm25_available: bool = False,
            focus_document_id=None,
            degradations=(), anchors: AnchorIndex | None = None,
            absent_reference: str = "", keywords: list[str] | None = None) -> GateSignals:
    """Collect signals from one real retrieval call. Pure; never filters rows."""
    ranked = list(ranked or [])
    selected = list(selected if selected is not None else ranked[:5])
    scores = [row.get("rerank_score") for row in ranked
              if row.get("rerank_score") is not None]
    scores.sort(reverse=True)
    best = scores[0] if scores else None
    second = scores[1] if len(scores) > 1 else None
    margin = round(best - second, 4) if best is not None and second is not None else None
    dense_scores = [row.get("score") for row in ranked if row.get("score") is not None]
    best_dense = max(dense_scores) if dense_scores else None

    dense_top = _top_doc(dense_rows or [])
    bm25_top = _top_doc(bm25_rows or [])
    agreement = (dense_top == bm25_top) if dense_top and bm25_top else None
    dense3 = {_doc_id(r) for r in (dense_rows or [])[:3]} - {None}
    bm253 = {_doc_id(r) for r in (bm25_rows or [])[:3]} - {None}
    overlap = (round(len(dense3 & bm253) / len(dense3 | bm253), 4)
               if dense3 and bm253 else None)

    window = ranked[:5]
    counts = {}
    for row in window:
        did = _doc_id(row)
        if did is not None:
            counts[did] = counts.get(did, 0) + 1
    top_doc_id, top_doc_count = (max(counts.items(), key=lambda x: (x[1], x[0]))
                                 if counts else (None, 0))
    top_doc_share = round(top_doc_count / len(window), 4) if window else 0.0

    found = find_anchors(query, anchors)
    selected_keys = [title_key(_row_source(row)) for row in selected]
    title_hit = (any(_matches(title_key(found["title"]), key) for key in selected_keys)
                 if found["title"] else None)
    docno_hit = None
    if found["docno"]:
        wanted = docno_of(found["docno"])
        docno_hit = any(docno_of(_row_source(row)) == wanted for row in selected)
    entity_hit = None
    if found["entity"]:
        wanted = query_key(found["entity"])
        evidence = []
        for row in selected:
            # A source title may verify an entity when the query names the
            # entity but not a reviewed document title (e.g. a major plan).
            # If a reviewed title was named, keep title and entity evidence
            # independent so one title match cannot cast two votes.
            if found["title"] is None:
                evidence.append(_row_source(row))
            evidence.append(_row_text(row))
            evidence.extend(row.get("heading_path") or ())
        entity_hit = any(_matches(wanted, query_key(text)) for text in evidence)

    top1_text = _row_text(selected[0]) if selected else ""
    top3_text = "\n".join(_row_text(row) for row in selected[:3])
    top_title = _row_source(selected[0]) if selected else None
    claim_groups = {}
    for index, row in enumerate(selected):
        key = _doc_id(row) or f"row:{index}"
        group = claim_groups.setdefault(key, [])
        group.append(_row_source(row))
        group.extend(row.get("heading_path") or ())
        group.append(_row_text(row))
    leading_key = (_doc_id(selected[0]) or "row:0") if selected else None
    leading_group = [claim_groups[leading_key]] if leading_key in claim_groups else []
    claim = best_claim_anchor(query, keywords or (), leading_group)

    conflict_kind = None
    if absent_reference:
        conflict_kind = "absent_reference"
    elif _other_university(query):
        conflict_kind = "other_university"
    elif unsupported_action(query):
        conflict_kind = "unsupported_action"
    elif found["title"] and title_hit is False:
        conflict_kind = "entity_conflict"
    elif found["docno"] and docno_hit is False:
        conflict_kind = "entity_conflict"
    elif found["entity"] and entity_hit is False:
        conflict_kind = "entity_anchor_conflict"

    return GateSignals(
        rerank_score_version=(ranked[0].get("rerank_score_version", "legacy-double-sigmoid/1")
                              if ranked else "legacy-double-sigmoid/1"),
        best_rerank=best,
        second_rerank=second,
        rerank_margin=margin,
        best_dense=best_dense,
        dense_bm25_top1_agreement=agreement,
        dense_bm25_top3_overlap=overlap,
        top_doc_count=top_doc_count,
        top_doc_share=top_doc_share,
        top_doc_id=top_doc_id,
        title_anchor=found["title"],
        title_anchor_hit=title_hit,
        docno_anchor=found["docno"],
        docno_anchor_hit=docno_hit,
        entity_anchor=found["entity"],
        entity_anchor_hit=entity_hit,
        token_coverage_top1=(_coverage(query, top1_text) if top1_text else None),
        token_coverage_top3=(_coverage(query, top3_text) if top3_text else None),
        candidate_count=len(ranked),
        bm25_candidate_count=len(bm25_rows or []),
        bm25_available=bool(bm25_available),
        focus_document_id=(str(focus_document_id) if focus_document_id is not None else None),
        scope_conflict=conflict_kind is not None,
        scope_conflict_kind=conflict_kind,
        absent_reference=absent_reference or "",
        degradations=tuple(dict(item) for item in (degradations or ())),
        selected_document_ids=tuple(did for did in (_doc_id(r) for r in selected) if did is not None),
        top_document_title=top_title or None,
        claim_anchor_terms=claim["claim_anchor_terms"],
        claim_anchor_hits=claim["claim_anchor_hits"],
        claim_anchor_coverage=claim["claim_anchor_coverage"],
        claim_shape=claim["claim_shape"],
        claim_shape_supported=claim["claim_shape_supported"],
        claim_anchor_phrases=claim["claim_anchor_phrases"],
        claim_anchor_phrase_hits=claim["claim_anchor_phrase_hits"],
        claim_anchor_phrase_supported=claim["claim_anchor_phrase_supported"],
    )
