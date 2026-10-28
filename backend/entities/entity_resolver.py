# -*- coding: utf-8 -*-
"""纯函数实体解析器 —— **不接运行图、不碰工具、不联网**（设计 §17）。

输入只有三样（Task 3 ruling 7）：**已严格加载的规范目录** + **reviewed 别名** +
**reviewed 实体族**。传进 pending / rejected 行是**构造期错误**，不是运行期降级。

它**只做解析**：候选生成、证据分级、上下文过滤、歧义判定、规范问句生成。
它**不**决定「该调哪个工具」，也**不**执行任何工具（那是 Task 4）。
embedding / LLM 只能作为**注入式候选提供者**，其输出永远不构成「确定性支持」。

★ 等级与真相（设计 §17.5 / Task 3 ruling 1、2）：

    T1 exact_canonical / T2 reviewed_alias / T3 code_exact  —— 确定性支持，唯一时可选中
    T4 确定性缩写 / T5 词法 / T6 embedding / T7 LLM         —— **只生成候选，绝不自动选中**

    T4 的判据是**集合判据**（首字锚定 + 严格顺序子序列），没有分数、没有名次，
    因此代码里不存在「取最像的一个」的路径。实测它会把 `高数` 拉到 `高等代数`、
    把 `计科` 拉到 `计算机科学导论` —— 精度不足以为真，这是它不许自动选中的直接原因。

★ **本轮不做「按提及顺序累积上下文」**：解析是**单趟、与提及顺序无关**的纯函数。
  专业/年级上下文由调用方通过 `ResolutionContext` 传入（Task 4 负责从问句与图里取）。
  理由：`compare_course` 会同时提到**多个**专业，按顺序累积会让第二个专业被第一个的
  上下文过滤掉 —— 那是错的。改用两趟/定点会引入顺序效应，得不偿失。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import entities.entity_aliases as EA

RESOLVER_VERSION = "entity_resolver/1.0.0"

ENTITY_TYPES = ("major", "course", "regulation", "institutional_term")

#: 证据层级 —— **只有 T1/T2/T3 是确定性支持**（ruling 1/2）
CERTIFIED_TIERS = ("T1", "T2", "T3")
#: T4..T7 **只能提出候选**，绝不自动选中（ruling 2）
WEAK_TIERS = ("T4", "T5", "T6", "T7")
TIERS = CERTIFIED_TIERS + WEAK_TIERS
TIER_RANK = {tier: index for index, tier in enumerate(TIERS)}
TIER_SOURCE = {
    "T1": "exact_canonical", "T2": "reviewed_alias", "T3": "code_exact",
    "T4": "deterministic_abbreviation", "T5": "lexical",
    "T6": "embedding", "T7": "llm",
}
CANDIDATE_SOURCES = ("exact_canonical", "reviewed_alias", "code_exact",
                     "deterministic_abbreviation", "context", "lexical",
                     "embedding", "llm")

RESOLUTION_STATUSES = ("exact_canonical", "resolved_reviewed_alias",
                       "resolved_by_context", "resolved_by_deterministic_rule",
                       "ambiguous", "unknown", "scope_conflict",
                       "resolved_scope_absent", "catalog_invalid")
#: 各确定性层级在**没有**上下文参与时的状态名
_TIER_STATUS = {"T1": "exact_canonical", "T2": "resolved_reviewed_alias",
                "T3": "resolved_by_deterministic_rule"}

#: 顶层聚合的「取最差」次序 —— 数字越小越差（**声明**的次序，不是打分）
STATUS_SEVERITY = {
    "catalog_invalid": 0, "scope_conflict": 1, "resolved_scope_absent": 1,
    "ambiguous": 2, "unknown": 3,
    "resolved_by_context": 4, "resolved_by_deterministic_rule": 4,
    "resolved_reviewed_alias": 5, "exact_canonical": 6,
}
CLARIFYING_STATUSES = ("ambiguous", "unknown", "scope_conflict",
                       "resolved_scope_absent")

#: T4 缩写的长度上下界（1 字简称会命中几百个名称，直接拒）
ABBREV_MIN, ABBREV_MAX = 2, 6
#: T5 词法前缀的最短长度
PREFIX_MIN = 3

_NAME_FIELD = {"major": "canonical_name", "course": "canonical_name",
               "regulation": "canonical_title",
               "institutional_term": "canonical_name"}
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_JOIN_KEY = None


class ResolverError(RuntimeError):
    """解析器无法构造或无法解析。**不得伪装**成 no_evidence / tool_failed。"""


def _dump(value) -> str:
    """规范化 JSON —— 排序 + 紧凑分隔符，保证逐字节可复现。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _join_key(text: str) -> str:
    """**复用当前正式归一函数** —— 不另写第二份名称归一规则（只加载一次）。"""
    global _JOIN_KEY
    if _JOIN_KEY is None:
        from agent.tools.query_course.scripts.normalize import join_key

        _JOIN_KEY = join_key
    return _JOIN_KEY(text)


def _abbrev_positions(short: str, long: str):
    """`short` 是否为 `long` 的合法缩写，是则返回逐字符对位，否则 `None`。

    规则（设计 §17.5.1，**本轮唯一定义的字符规则**，确定性、可逐字符复核）：
    `2 ≤ len(short) < len(long)`、`short[0] == long[0]`、`short[1:]` 是 `long[1:]`
    的**顺序子序列**。无词表、无分词、无权重。

    ★ 另加一条**收紧**（Task 3 实施时实测得出，只**减少**候选、不放松任何门）：
    匹配位置**至少要跳过 1 个字符** —— 即缩写必须是**压缩**而不是**截断**。
    否则 `专业` 会命中 `专业外语`（逐字前缀），使正常问句
    「计算机科学与技术**专业**的课程」无端多出一个 `专业` 提及并**强制澄清**
    （实测复现）。截断那一类由 **T5 真前缀**通道负责（长度 ≥ 3），两条通道分工清楚。
    实测本收紧后 `高数→高等数学`、`大物→大学物理`、`计科→计算机科学与技术`、
    `软工→软件工程`、`毛概→毛泽东思想…`、`形策→形势与政策1` **全部仍然命中**。
    """
    if not (ABBREV_MIN <= len(short) < len(long)) or short[0] != long[0]:
        return None
    positions = [0]
    cursor = 1
    for char in short[1:]:
        while cursor < len(long) and long[cursor] != char:
            cursor += 1
        if cursor == len(long):
            return None
        positions.append(cursor)
        cursor += 1
    if positions[-1] == len(short) - 1:
        return None                       # 逐字截断 → 交给 T5，不是缩写
    return tuple(positions)


def _derivation(short: str, long: str):
    """把缩写的**字符对位**写成可机械复核的数据（不是解释文字）。"""
    positions = _abbrev_positions(short, long)
    if positions is None:
        return None
    return " ".join("%s@%d→%s@%d" % (short[i], i, long[j], j)
                    for i, j in enumerate(positions))


# ------------------------------------------------------------------ 数据结构

@dataclass(frozen=True)
class EntityCandidate:
    candidate_id: str
    entity_type: str
    canonical_name: str
    tier: str
    sources: tuple = ()
    scope: dict = field(default_factory=dict)
    instance_ids: tuple = ()
    score: float | None = None
    alias_id: str | None = None
    family_id: str | None = None


@dataclass(frozen=True)
class SelectedEntity:
    kind: str                      # "instance" | "name"
    entity_id: str | None
    canonical_name: str
    entity_type: str
    instance_ids: tuple
    via: str
    alias_id: str | None = None
    family_id: str | None = None


@dataclass(frozen=True)
class MentionResolution:
    mention_id: str
    raw_text: str
    start: int
    end: int
    entity_type: str | None
    detection_sources: tuple
    candidates: tuple
    support_tier: str | None
    selected: SelectedEntity | None
    resolution_status: str
    requires_clarification: bool
    derivation: str | None = None
    #: ★ 仅当「确定性上下文收敛」通路生效时非空 —— 保留**过滤前候选集**与**过滤依据**，
    #:   供审计复核（哪条规则把哪些候选滤掉、最终选中哪个 entity ID）。
    context_filter: dict | None = None
    #: ★ `resolved_scope_absent` 的确定性证据：规范实体全局存在，但目标专业
    #:   作用域内没有任何实例。只在「唯一规范课程名 + 目标 major_code」时填写。
    scope_absent: dict | None = None


@dataclass(frozen=True)
class SuppressedSpan:
    start: int
    end: int
    raw_text: str
    tier: str
    reason: str


@dataclass(frozen=True)
class DiscardedProposal:
    source: str
    start: int
    end: int
    text: str
    reason: str


@dataclass(frozen=True)
class ResolutionContext:
    grade: str | None = None
    major_code: str | None = None
    major_name: str | None = None
    semester: int | None = None
    source: str = "query"


@dataclass(frozen=True)
class QueryResolutionEnvelope:
    original_query: str
    normalized_query: str
    query_sha256: str
    mentions: tuple
    suppressed_spans: tuple
    discarded_proposals: tuple
    resolution_status: str
    requires_clarification: bool
    context: ResolutionContext
    catalog_sha256: str
    aliases_sha256: str
    resolver_version: str


def _with_instances(candidate: EntityCandidate, instance_ids) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=candidate.candidate_id, entity_type=candidate.entity_type,
        canonical_name=candidate.canonical_name, tier=candidate.tier,
        sources=candidate.sources, scope=candidate.scope,
        instance_ids=tuple(instance_ids), score=candidate.score,
        alias_id=candidate.alias_id, family_id=candidate.family_id)


# ------------------------------------------------------------------ 解析器

class Resolver:
    """纯函数解析器。**只接收已验证、已过滤的 reviewed 记录**（ruling 7）。"""

    def __init__(self, catalog, reviewed_aliases=(), reviewed_families=(),
                 *, catalog_sha256, aliases_sha256,
                 embedding_provider=None, llm_provider=None):
        if hasattr(catalog, "aliases") or hasattr(catalog, "families"):
            raise TypeError(
                "Resolver 不接受 AliasBundle —— 请先用 reviewed_aliases() / "
                "reviewed_families() 取生产视图再传入")
        if not isinstance(catalog, dict) or "entities" not in catalog:
            raise ResolverError("Resolver 需要已严格加载的规范目录")
        _check_sha(catalog_sha256, "catalog_sha256")
        _check_sha(aliases_sha256, "aliases_sha256")

        self.catalog = catalog
        self.reviewed_aliases = _checked_reviewed(reviewed_aliases, "aliases")
        self.reviewed_families = _checked_reviewed(reviewed_families, "families")
        self.catalog_sha256 = catalog_sha256
        self.aliases_sha256 = aliases_sha256
        self.embedding_provider = embedding_provider
        self.llm_provider = llm_provider
        self._index()

    # -------------------------------------------------------- 索引

    def _index(self):
        self._row_of = {}
        self._instances = {}          # (type, name) -> [entity_id]
        self._name_by_key = {}        # join_key(正式名) -> [(type, name)]
        self._by_first_char = {}      # 名称首字 -> [(type, name)]
        self._prefixes = {}           # join_key(真前缀) -> [(type, name)]
        self._codes = {}              # 代码 -> [(type, name)]
        self._major_by_name = {}      # 专业正式名 -> 行（供 major_name 上下文用）
        self._family_of = {row["family_id"]: row for row in self.reviewed_families}
        self._max_len = 0

        for kind in ENTITY_TYPES:
            for row in self.catalog["entities"][kind]:
                name = row[_NAME_FIELD[kind]]
                self._row_of[row["entity_id"]] = row
                self._instances.setdefault((kind, name), []).append(row["entity_id"])
                if kind == "major":
                    self._major_by_name.setdefault(name, row)
                if kind in ("course", "major"):
                    code = row["course_code"] if kind == "course" else row["major_code"]
                    self._codes.setdefault(code, []).append((kind, name))

        for (kind, name) in self._instances:
            self._max_len = max(self._max_len, len(name))
            normalized = _join_key(name)
            self._name_by_key.setdefault(normalized, []).append((kind, name))
            self._by_first_char.setdefault(name[0], []).append((kind, name))
            for length in range(PREFIX_MIN, len(normalized)):
                self._prefixes.setdefault(normalized[:length], []).append((kind, name))

        for key in list(self._instances):
            self._instances[key] = tuple(sorted(self._instances[key]))
        for mapping in (self._name_by_key, self._by_first_char, self._prefixes,
                        self._codes):
            for key in list(mapping):
                mapping[key] = tuple(sorted(set(mapping[key])))

    # -------------------------------------------------------- 公开入口

    def resolve(self, query: str, context: ResolutionContext | None = None):
        """解析整句，返回 `QueryResolutionEnvelope`。**纯函数、确定性、与提及顺序无关。**

        ★ 本函数**只解析一趟**：上下文由调用方通过 `ResolutionContext` 传入。
          「问句本身明确指出了一个专业」这种来源叫 **query_explicit**，由**运行图**用
          `query_explicit_context()` 推出来后再调本函数第二趟 —— 见 `graph.resolve_entities`。
        """
        if not isinstance(query, str):
            raise ResolverError("query 必须是字符串")
        context = context or ResolutionContext()
        spans, suppressed = self._spans(query)
        mentions = [self._resolve_span(query, start, end, candidates, context)
                    for (start, end), candidates in spans]
        mentions.sort(key=lambda mention: mention.start)
        statuses = [mention.resolution_status for mention in mentions]
        return QueryResolutionEnvelope(
            original_query=query,
            normalized_query=_normalize(query, [m for m in mentions if m.selected]),
            query_sha256=_sha256_text(query),
            mentions=tuple(mentions),
            suppressed_spans=tuple(suppressed),
            discarded_proposals=tuple(self._discarded),
            resolution_status=(min(statuses, key=lambda s: STATUS_SEVERITY[s])
                               if statuses else "unknown"),
            requires_clarification=any(m.requires_clarification for m in mentions),
            context=context, catalog_sha256=self.catalog_sha256,
            aliases_sha256=self.aliases_sha256, resolver_version=RESOLVER_VERSION)

    def query_explicit_context(self, envelope):
        """**query_explicit 上下文来源**：问句里**恰好一个**已确认 major 时才给出上下文。

        判据（全部满足才返回 `ResolutionContext(source="query_explicit")`）：

          · 该提及已被**确定性**选中（`selected` 非空 ⇒ T1/T2/T3），且类型是 `major`；
          · 这些提及折算出的 `major_code` **去重后恰好 1 个**；
          · 与提及顺序无关；0 个或 ≥2 个专业一律返回 `None` —— 多专业问句
            （如 `compare_course`）必须各自解析，不能被其中一个专业过滤掉。

        ★ 纯查表：不猜、不排序、不涉及 LLM / embedding / BM25 / 相似度。
        ★ 本函数**不改 envelope**；由调用方（运行图）拿它再解析第二趟。
        """
        codes = set()
        for mention in envelope.mentions:
            selected = mention.selected
            if selected is None or selected.entity_type != "major":
                continue
            for entity_id in selected.instance_ids:
                row = self._row_of.get(entity_id)
                if row and row.get("major_code"):
                    codes.add(row["major_code"])
        if len(codes) != 1:
            return None
        return ResolutionContext(major_code=codes.pop(), source="query_explicit")

    # -------------------------------------------------------- 通道

    def _spans(self, query: str):
        """全部通道产出的 span → 重叠裁决 → (已接受, 被压制)。"""
        raw: dict[tuple, list] = {}
        self._discarded = []

        def add(start, end, candidate):
            raw.setdefault((start, end), []).append(candidate)

        for (start, end), hits in self._exact_spans(query).items():
            for kind, name in hits:
                add(start, end, self._name_candidate(kind, name, "T1"))
        for (start, end), rows in self._alias_spans(query).items():
            for row in rows:
                add(start, end, self._alias_candidate(row))
        for (start, end), hits in self._code_spans(query).items():
            for kind, name in hits:
                add(start, end, self._name_candidate(kind, name, "T3"))
        for (start, end), hits in self._abbrev_spans(query).items():
            for kind, name in hits:
                add(start, end, self._name_candidate(kind, name, "T4"))
        for (start, end), hits in self._lexical_spans(query).items():
            for kind, name in hits:
                add(start, end, self._name_candidate(kind, name, "T5"))
        for tier, provider in (("T6", self.embedding_provider),
                               ("T7", self.llm_provider)):
            if provider is None:
                continue
            for start, end, candidate in self._injected(query, provider, tier):
                add(start, end, candidate)

        # 同一个 span 可能被多个窗口/多条别名重复产出 —— **按身份去重**，
        # 否则同一实体出现两次会被判成「并列」（实测踩过：`*境内设计考察` 同时由
        # 长度 7 与长度 8 两个窗口命中）。
        for key, candidates in raw.items():
            unique = {}
            for candidate in candidates:
                unique.setdefault((candidate.tier, candidate.candidate_id), candidate)
            raw[key] = [unique[k] for k in sorted(unique)]

        ordered = sorted(raw.items(),
                         key=lambda item: (TIER_RANK[_best_tier(item[1])],
                                           item[0][0], -(item[0][1] - item[0][0])))
        accepted, suppressed = [], []
        for (start, end), candidates in ordered:
            clash = next((span for span in accepted
                          if start < span[1] and span[0] < end), None)
            if clash is None:
                accepted.append((start, end))
            else:
                suppressed.append(SuppressedSpan(
                    start=start, end=end, raw_text=query[start:end],
                    tier=_best_tier(candidates), reason="overlap:m:%d:%d" % clash))
        return (sorted((span, raw[span]) for span in accepted),
                sorted(suppressed, key=lambda s: s.start))

    def _exact_spans(self, query: str):
        """T1：窗口的**正式归一形式** == 某个正式名称（顺带吃掉空白差异）。"""
        spans: dict[tuple, list] = {}
        upper = self._max_len + 8
        for start in range(len(query)):
            for length in range(1, min(upper, len(query) - start) + 1):
                raw = query[start:start + length]
                trimmed = raw.strip()
                if not trimmed:
                    continue
                hits = self._name_by_key.get(_join_key(trimmed))
                if not hits:
                    continue
                lead = len(raw) - len(raw.lstrip())
                spans.setdefault((start + lead, start + lead + len(trimmed)),
                                 []).extend(hits)
        return spans

    def _alias_spans(self, query: str):
        """T2：**reviewed** 别名的逐字出现。"""
        spans: dict[tuple, list] = {}
        for row in self.reviewed_aliases:
            text = row["alias"]
            start = query.find(text)
            while start >= 0:
                spans.setdefault((start, start + len(text)), []).append(row)
                start = query.find(text, start + 1)
        return spans

    def _code_spans(self, query: str):
        """T3：代码逐字出现，且**不得**被更长的数字串包住。"""
        spans: dict[tuple, list] = {}
        for code in sorted(self._codes):
            start = query.find(code)
            while start >= 0:
                before = query[start - 1:start] if start else ""
                after = query[start + len(code):start + len(code) + 1]
                if not (before.isdigit() or after.isdigit()):
                    spans.setdefault((start, start + len(code)), []).extend(
                        self._codes[code])
                start = query.find(code, start + 1)
        return spans

    def _abbrev_spans(self, query: str):
        """T4：每个起点取**最长**的合法缩写窗口（每起点至多 1 个 span）。"""
        spans: dict[tuple, list] = {}
        for start in range(len(query)):
            best = None
            for length in range(ABBREV_MIN, ABBREV_MAX + 1):
                if start + length > len(query):
                    break
                short = query[start:start + length]
                hits = [(kind, name)
                        for kind, name in self._by_first_char.get(short[0], ())
                        if _abbrev_positions(short, name) is not None]
                if hits:
                    best = (start, start + length, hits)
            if best:
                spans[(best[0], best[1])] = best[2]
        return spans

    def _lexical_spans(self, query: str):
        """T5：窗口是某个正式名称的**真前缀**（长度 ≥ 3），每起点取最长。"""
        spans: dict[tuple, list] = {}
        upper = self._max_len + 8
        for start in range(len(query)):
            best = None
            for length in range(PREFIX_MIN, min(upper, len(query) - start) + 1):
                hits = self._prefixes.get(_join_key(query[start:start + length]))
                if hits:
                    best = (start, start + length, hits)
            if best:
                spans[(best[0], best[1])] = best[2]
        return spans

    def _injected(self, query: str, provider, tier: str):
        """注入式提案 —— **必须重新从问句取文本**，对不上就丢弃并留痕。"""
        source = TIER_SOURCE[tier]
        for item in provider(query) or ():
            start, end, text = item.get("start"), item.get("end"), item.get("text")
            if not isinstance(start, int) or not isinstance(end, int) \
                    or not 0 <= start < end <= len(query):
                self._discarded.append(DiscardedProposal(
                    source=source,
                    start=start if isinstance(start, int) else -1,
                    end=end if isinstance(end, int) else -1, text=str(text),
                    reason="proposal_span_out_of_range"))
                continue
            if query[start:end] != text:
                self._discarded.append(DiscardedProposal(
                    source=source, start=start, end=end, text=str(text),
                    reason="proposal_text_mismatch"))
                continue
            base = self._name_candidate_by_name(item.get("canonical_name"))
            if base is None:
                self._discarded.append(DiscardedProposal(
                    source=source, start=start, end=end, text=text,
                    reason="proposal_entity_not_in_catalog"))
                continue
            yield start, end, EntityCandidate(
                candidate_id=base.candidate_id, entity_type=base.entity_type,
                canonical_name=base.canonical_name, tier=tier, sources=(source,),
                scope={}, instance_ids=base.instance_ids, score=item.get("score"))

    # -------------------------------------------------------- 候选构造

    def _name_candidate(self, kind: str, name: str, tier: str) -> EntityCandidate:
        return EntityCandidate(
            candidate_id="name:%s:%s" % (kind, name), entity_type=kind,
            canonical_name=name, tier=tier, sources=(TIER_SOURCE[tier],),
            scope={}, instance_ids=self._instances[(kind, name)])

    def _name_candidate_by_name(self, name):
        for kind in ENTITY_TYPES:
            if (kind, name) in self._instances:
                return self._name_candidate(kind, name, "T1")
        return None

    def _alias_candidate(self, row: dict) -> EntityCandidate:
        if row["mapping_type"] == "one_to_one":
            entity_id = row["canonical"]
            entity = self._row_of.get(entity_id)
            name = entity[_NAME_FIELD[row["entity_type"]]] if entity else entity_id
            return EntityCandidate(
                candidate_id=entity_id, entity_type=row["entity_type"],
                canonical_name=name, tier="T2", sources=("reviewed_alias",),
                scope=dict(row["scope"] or {}), instance_ids=(entity_id,),
                alias_id=row["alias_id"])
        family = self._family_of[row["canonical_family"]]
        return EntityCandidate(
            candidate_id=family["family_id"], entity_type=family["entity_type"],
            canonical_name=family["canonical_label"], tier="T2",
            sources=("reviewed_alias",), scope={},
            instance_ids=tuple(sorted(family["members"])),
            alias_id=row["alias_id"], family_id=family["family_id"])

    # -------------------------------------------------------- 判定

    def _resolve_span(self, query, start, end, candidates, context) -> MentionResolution:
        filtered, kept, narrowed = [], [], False
        for candidate in candidates:
            if not _scope_ok(candidate.scope, context):
                filtered.append(candidate)
                continue
            instances = self._narrow(candidate, context)
            if len(instances) != len(candidate.instance_ids):
                narrowed = True
            judged = _with_instances(candidate, instances)
            (kept if instances else filtered).append(judged)

        ordered = tuple(sorted(kept, key=_candidate_sort_key))
        support = _best_tier(ordered) if ordered else None
        certified = [c for c in ordered if c.tier in CERTIFIED_TIERS]
        selected, status, basis, scope_absent = None, None, None, None

        if certified:
            best = min((c.tier for c in certified), key=lambda t: TIER_RANK[t])
            top = [c for c in certified if c.tier == best]
            # ruling 3：族必须**收敛到 1 个成员**才可选中
            converged = all(c.family_id is None or len(c.instance_ids) == 1 for c in top)
            if len(top) == 1 and converged:
                used_context = bool(filtered) or narrowed
                status = "resolved_by_context" if used_context else _TIER_STATUS[best]
                selected = _selected_from(top[0])
            else:
                status = "ambiguous"
        elif candidate_was_certified(candidates):
            # ★ 「规范实体全局存在、但目标专业作用域内不存在」不是实体歧义。
            #   只有唯一规范课程名 + 目标 major_code + 全局实例齐全时才升级为
            #   resolved_scope_absent；其余（同名多义、弱层级、多成员族、grade 冲突）
            #   仍是 scope_conflict。
            scope_absent = self._scope_absent_info(candidates, context)
            status = ("resolved_scope_absent" if scope_absent is not None
                      else "scope_conflict")
        elif ordered:
            # ★ 低层级（T4–T7）**唯一**的选中通路：确定性结构化上下文收敛。
            #   不是 top-1、不碰 LLM/embedding/BM25/相似度；条件不满足一律回到 ambiguous。
            chosen, basis = self._context_convergence(ordered, candidates, context,
                                                      narrowed, filtered)
            if chosen is not None:
                selected = _selected_from(chosen)
                status = "resolved_by_context"
            else:
                status = "ambiguous"
        else:
            status = "unknown"

        return MentionResolution(
            mention_id="m:%d:%d" % (start, end), raw_text=query[start:end],
            start=start, end=end,
            entity_type=(selected.entity_type if selected
                         else _single_type(ordered, support)),
            detection_sources=tuple(sorted({source for c in candidates
                                            for source in c.sources})),
            candidates=ordered, support_tier=support, selected=selected,
            resolution_status=status,
            requires_clarification=status in CLARIFYING_STATUSES,
            derivation=_mention_derivation(query[start:end], ordered),
            context_filter=basis, scope_absent=scope_absent)

    def _scope_absent_info(self, candidates, context):
        """唯一规范课程在目标专业作用域内没有实例 —— 返回可审计证据，否则 None。

        只接受 T1（正式课程名）/T2（已审核一对一 alias），且：
        唯一 canonical_name、唯一 entity_type=course、无 one_to_many 族、
        至少一个全局实例、上下文能确定 major_code、且没有任何实例落在该 code。
        """
        certified = [c for c in candidates if c.tier in CERTIFIED_TIERS]
        if not certified:
            return None
        names = {c.canonical_name for c in certified}
        types = {c.entity_type for c in certified}
        tiers = {c.tier for c in certified}
        if len(names) != 1 or types != {"course"} or not tiers <= {"T1", "T2"}:
            return None
        if any(c.family_id is not None for c in certified):
            return None
        instances = sorted({entity_id for c in certified
                            for entity_id in c.instance_ids})
        if not instances:
            return None
        code = context.major_code if context else None
        if not code and context and context.major_name:
            row = self._major_by_name.get(context.major_name)
            code = row.get("major_code") if row else None
        if not code:
            return None
        in_scope = [entity_id for entity_id in instances
                    if (self._row_of.get(entity_id) or {}).get("major_code") == code]
        if in_scope:
            return None
        return {
            "reason": "canonical_entity_absent_in_scope",
            "canonical_name": next(iter(names)),
            "entity_type": "course",
            "instance_ids": instances,
            "tier": min(tiers, key=lambda tier: TIER_RANK[tier]),
            "target_major_code": code,
        }

    def _narrow(self, candidate: EntityCandidate, context: ResolutionContext):
        """上下文只**过滤**实例 —— 不产生候选、不证明语义。"""
        grades, codes = _context_keys(context, self._major_by_name)
        if not grades and not codes:
            return tuple(candidate.instance_ids)
        out = []
        for entity_id in candidate.instance_ids:
            row = self._row_of.get(entity_id)
            if row is None:
                out.append(entity_id)
                continue
            if grades and row.get("grade") and row["grade"] not in grades:
                continue
            if codes and row.get("major_code") and row["major_code"] not in codes:
                continue
            out.append(entity_id)
        return tuple(out)

    def _context_convergence(self, ordered, candidates, context, narrowed, filtered):
        """**确定性上下文收敛** —— 低层级（T4–T7）提及唯一的被选中通路。

        全部条件满足才返回 `(candidate, basis)`；否则返回 `(None, None)`，调用方继续澄清：

          1. 候选全部来自当前 catalog 的真实实体（`_index` 只从目录建索引，天然成立）；
          2. 结构化上下文非空：`grade` / `major_code`（`major_name` 由**已确认的 major**折算成 code）；
          3. 上下文**真的做过过滤**（`narrowed` 或 `filtered` 非空）—— 否则它没有判别力；
          4. 过滤后**只剩一个候选**；
          5. 该候选**只剩一个实例**（等价于「无同等合理的 runner-up」）；
          6. 过滤前确实存在多个可能（候选数 > 1 或某候选实例数 > 1）。

        ★ 全程不调用 LLM / embedding / BM25，也不做相似度 top-1 —— 只有结构化的硬过滤。
        ★ **必须**用实例自己的 `_major_by_name` 折算 `major_name`：传空映射会让
          「已确认 major」这种上下文看不见，从而把该收敛的场景误退回澄清（实测踩过）。
        """
        grades, codes = _context_keys(context, self._major_by_name)
        if not grades and not codes:
            return None, None
        if not narrowed and not filtered:
            return None, None
        if len(ordered) != 1 or len(ordered[0].instance_ids) != 1:
            return None, None

        before = [{"candidate_id": item.candidate_id, "entity_type": item.entity_type,
                   "canonical_name": item.canonical_name, "tier": item.tier,
                   "instance_count": len(item.instance_ids)} for item in candidates]
        if len(before) < 2 and all(item["instance_count"] < 2 for item in before):
            return None, None

        chosen = ordered[0]
        basis = {
            "rule": "structured_context_convergence",
            "grade": sorted(grades),
            "major_code": sorted(codes),
            "narrowed": bool(narrowed),
            "candidates_before": before,
            "filtered_out": [{"candidate_id": item.candidate_id,
                              "canonical_name": item.canonical_name,
                              "tier": item.tier} for item in filtered],
            "candidates_after": 1,
            "instances_after": 1,
            "selected_entity_id": chosen.instance_ids[0],
            "selected_tier": chosen.tier,
        }
        return chosen, basis



def candidate_was_certified(candidates) -> bool:
    return any(c.tier in CERTIFIED_TIERS for c in candidates)


def _selected_from(candidate: EntityCandidate) -> SelectedEntity:
    instances = tuple(candidate.instance_ids)
    if len(instances) == 1:
        kind, entity_id = "instance", instances[0]
    else:
        # ruling 5：kind="name" 只表示**真实存在的正式课程名**及其多个实例，
        # entity_id 必须为 null，绝不用第一条实例冒充唯一实体。
        kind, entity_id = "name", None
    return SelectedEntity(kind=kind, entity_id=entity_id,
                          canonical_name=candidate.canonical_name,
                          entity_type=candidate.entity_type,
                          instance_ids=instances, via=candidate.tier,
                          alias_id=candidate.alias_id, family_id=candidate.family_id)


def _single_type(candidates, tier):
    if tier is None:
        return None
    types = {c.entity_type for c in candidates if c.tier == tier}
    return types.pop() if len(types) == 1 else None


def _context_keys(context, major_by_name):
    """上下文只认**已知维度**；`semester` 本轮不在目录里，故不参与过滤。"""
    grades = {context.grade} if context.grade else set()
    codes = {context.major_code} if context.major_code else set()
    if not codes and context.major_name:
        row = major_by_name.get(context.major_name)
        if row:
            codes = {row["major_code"]}
    return grades, codes


def _scope_ok(scope, context) -> bool:
    """**已知维度**冲突才算冲突；上下文缺失的维度不参与判定。"""
    if not scope:
        return True
    grades, codes = _context_keys(context, {})
    if "grade" in scope and grades and scope["grade"] not in grades:
        return False
    if "major_code" in scope and codes and scope["major_code"] not in codes:
        return False
    if "semester" in scope and context.semester is not None \
            and scope["semester"] != context.semester:
        return False
    return True


def _best_tier(candidates) -> str:
    return min((c.tier for c in candidates), key=lambda t: TIER_RANK[t])


def _candidate_sort_key(candidate: EntityCandidate):
    return (TIER_RANK[candidate.tier], candidate.entity_type, candidate.candidate_id)


def _mention_derivation(raw_text, candidates):
    for candidate in candidates:
        if candidate.tier == "T4":
            return _derivation(raw_text, candidate.canonical_name)
    return None


def _normalize(query: str, confirmed) -> str:
    """**只替换已唯一确认的提及**，按 span **从右向左**，禁止全局替换。"""
    out = query
    for mention in sorted(confirmed, key=lambda m: m.start, reverse=True):
        out = out[:mention.start] + mention.selected.canonical_name + out[mention.end:]
    return out


def _check_sha(value, label):
    if not isinstance(value, str) or not _SHA_RE.match(value):
        raise ResolverError("%s 必须是 64 位小写十六进制 SHA-256" % label)


_ALIAS_KEYS = ("alias_id", "alias", "entity_type", "canonical", "canonical_family",
               "mapping_type", "scope", "review_status")
_FAMILY_KEYS = ("family_id", "entity_type", "members", "review_status")


def _checked_reviewed(rows, label: str) -> tuple:
    """**只有 reviewed 能进来**（ruling 7）—— pending / rejected 是构造期错误。"""
    required = _ALIAS_KEYS if label == "aliases" else _FAMILY_KEYS
    checked = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ResolverError("%s[%d] 必须是对象" % (label, index))
        missing = sorted(set(required) - set(row))
        if missing:
            raise ResolverError("%s[%d] 缺字段：%s" % (label, index, missing))
        if row["review_status"] != "reviewed":
            raise ResolverError(
                "%s[%d] 的 review_status=%r —— 解析器只接受 reviewed 记录，"
                "pending / rejected 不得进入生产解析"
                % (label, index, row["review_status"]))
        checked.append(row)
    return tuple(sorted(checked, key=lambda row: row["alias_id" if label == "aliases"
                                                     else "family_id"]))


# ------------------------------------------------------------------ 对外接口

def narrow_candidates(mention, expected_entity_type):
    """按工具参数**期望的 entity_type** 收窄提及候选。

    **纯过滤函数**：不接运行图、不改提及、不做选择（选择仍是 Task 4 的事）。
    返回 `(kept, dropped)`，都是候选元组。
    """
    if expected_entity_type not in ENTITY_TYPES:
        raise ResolverError("未知的 entity_type：%r" % (expected_entity_type,))
    kept = tuple(c for c in mention.candidates if c.entity_type == expected_entity_type)
    dropped = tuple(c for c in mention.candidates if c.entity_type != expected_entity_type)
    return kept, dropped


def resolve_question(resolver, question, context=None):
    """**运行图口径的整句解析**：最多两趟；`query_explicit` 是唯一自走的上下文来源。

    · 调用方给了 `context` → 只解析一趟（调用方负责来源）；
    · 没给 → 先解析一趟；若问句**明确**指到**恰好一个**已确认专业，就用它的
      `major_code` 作为结构化上下文**再解析一趟**（`context.source == "query_explicit"`）；
      0 个 / ≥2 个专业一律只有一趟。

    ★ 这条规则放在这里而不是图里：资格考试重放器（`compiler.replay`）必须用**同一份**
      规则，而它不得为了这几行去 import `agent.runtime.graph` —— 那会把整套运行时
      （langgraph / llm_client / 工具）拖进离线编译器。
      **这是唯一一份实现**，`graph.resolve_entities` 也调它。
    """
    envelope = resolver.resolve(question, context=context)
    if context is not None:
        return envelope
    derive = getattr(resolver, "query_explicit_context", None)
    if not callable(derive):
        return envelope
    explicit = derive(envelope)
    if explicit is None:
        return envelope
    return resolver.resolve(question, context=explicit)


def build_production_resolver(bundle_dir=None) -> Resolver:
    """**唯一生产工厂**：严格加载 → 只取 reviewed → 构造解析器（设计 §17.7）。"""
    try:
        bundle = EA.load_alias_bundle(bundle_dir)
    except EA.AliasCatalogError as error:
        raise ResolverError("别名包加载失败：%s" % error) from error
    return Resolver(bundle.catalog,
                    EA.reviewed_aliases(bundle),
                    EA.reviewed_families(bundle),
                    catalog_sha256=bundle.catalog_sha256,
                    aliases_sha256=bundle.aliases_sha256)


def to_dict(envelope) -> dict:
    """envelope → 纯数据（**无时间戳、无随机量**），供 `dump_envelope` 与测试比对。"""
    return {
        "resolver_version": envelope.resolver_version,
        "original_query": envelope.original_query,
        "normalized_query": envelope.normalized_query,
        "query_sha256": envelope.query_sha256,
        "resolution_status": envelope.resolution_status,
        "requires_clarification": envelope.requires_clarification,
        "catalog_sha256": envelope.catalog_sha256,
        "aliases_sha256": envelope.aliases_sha256,
        "context": {"grade": envelope.context.grade,
                    "major_code": envelope.context.major_code,
                    "major_name": envelope.context.major_name,
                    "semester": envelope.context.semester,
                    "source": envelope.context.source},
        "mentions": [_mention_dict(m) for m in envelope.mentions],
        "suppressed_spans": [{"start": s.start, "end": s.end, "raw_text": s.raw_text,
                              "tier": s.tier, "reason": s.reason}
                             for s in envelope.suppressed_spans],
        "discarded_proposals": [{"source": d.source, "start": d.start, "end": d.end,
                                 "text": d.text, "reason": d.reason}
                                for d in envelope.discarded_proposals],
    }


def _mention_dict(mention) -> dict:
    return {
        "mention_id": mention.mention_id, "raw_text": mention.raw_text,
        "start": mention.start, "end": mention.end,
        "entity_type": mention.entity_type,
        "detection_sources": list(mention.detection_sources),
        "support_tier": mention.support_tier,
        "resolution_status": mention.resolution_status,
        "requires_clarification": mention.requires_clarification,
        "derivation": mention.derivation,
        "context_filter": mention.context_filter,
        "scope_absent": mention.scope_absent,
        "selected": (None if mention.selected is None else {
            "kind": mention.selected.kind, "entity_id": mention.selected.entity_id,
            "canonical_name": mention.selected.canonical_name,
            "entity_type": mention.selected.entity_type,
            "instance_ids": list(mention.selected.instance_ids),
            "via": mention.selected.via, "alias_id": mention.selected.alias_id,
            "family_id": mention.selected.family_id}),
        "candidates": [{"candidate_id": c.candidate_id, "entity_type": c.entity_type,
                        "canonical_name": c.canonical_name, "tier": c.tier,
                        "sources": list(c.sources), "scope": c.scope,
                        "instance_ids": list(c.instance_ids), "score": c.score,
                        "alias_id": c.alias_id, "family_id": c.family_id}
                       for c in mention.candidates],
    }


def dump_envelope(envelope) -> str:
    """envelope → 规范 JSON（逐字节可复现，用于两次运行一致性的比对）。"""
    return _dump(to_dict(envelope))
