"""Lexical numeric/article audit, not an entailment judge or answer gate."""
from decimal import Decimal
import re
import unicodedata

_CN = "零〇一二两三四五六七八九十百千"
_ARTICLE = re.compile(r"第\s*([" + _CN + r"\d]+)\s*条")
_UNIT = r"个工作日|工作日|个月|学年|学分|学时|小时|分钟|年|月|天|门|分|次|届|人|%"
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(?:\s*(" + _UNIT + r"))?")


def _cn_number(text):
    if text.isdigit():
        return int(text)
    digits = {char: value for value, chars in enumerate(("零〇", "一", "二两", "三", "四", "五", "六", "七", "八", "九"))
              for char in chars}
    total, current = 0, 0
    for char in text:
        if char in digits:
            current = digits[char]
        elif char in "十百千":
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
        else:
            return None
    return total + current


def _normalize(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"([" + _CN + r"]+)\s*([~—–\-至到])\s*([" + _CN + r"]+)(?=\s*(?:" + _UNIT + r"))",
                  lambda m: str(_cn_number(m[1])) + "~" + str(_cn_number(m[3])), text)
    text = re.sub(r"([" + _CN + r"]+)(?=\s*(?:" + _UNIT + r"))",
                  lambda m: str(_cn_number(m[1])), text)
    return text


def _numbers(text):
    text = _ARTICLE.sub("", _normalize(text))
    text = re.sub(r"(?m)^\s*\d+[.、)）]\s+", "", text)  # List indices, not quantities.
    return [(m[0].strip(), Decimal(m[1]), (m[2] or "").replace("个工作日", "工作日"))
            for m in _NUMBER.finditer(text)]


def _verify_support(text, evidence_rows, authorized_text=""):
    """Check only this answer block's cited rows, never other retrieved rows.

    Matching tokens prove occurrence, not that the condition/object is correct.
    Missing units and academic-year conversions remain soft, unverified signals.
    """
    source = "\n".join((row.get("text") or "") + "\n" + "\n".join(row.get("heading_path") or [])
                       for row in evidence_rows)
    source_numbers = {(value, unit) for _, value, unit in _numbers(source)}
    # Explicit field projections, e.g. 学分：3, carry units before the number.
    for match in re.finditer(r"(学分|学时|成绩|分数)\s*[:：]\s*(\d+(?:\.\d+)?)", _normalize(source)):
        source_numbers.add((Decimal(match[2]), "分" if match[1] in {"成绩", "分数"} else match[1]))
    authorized = {(value, unit) for _, value, unit in _numbers(authorized_text)}
    if re.search(r"毕业总?学分|总学分", text):
        for row in evidence_rows:
            ruling = row.get("human_ruling") or {}
            if ruling.get("field") == "total_credits":
                authorized.update({(Decimal(str(ruling["value"])), "学分"), (Decimal(str(ruling["value"])), "")})
    source_articles = {_cn_number(m[1]) for m in _ARTICLE.finditer(_normalize(source))}
    unsupported_articles, unsupported_numbers, warnings = [], [], []
    total, supported = 0, 0
    for match in _ARTICLE.finditer(_normalize(text)):
        total += 1
        if _cn_number(match[1]) in source_articles:
            supported += 1
        else:
            unsupported_articles.append(match[0])
    for display, value, unit in _numbers(text):
        total += 1
        if (value, unit) in source_numbers | authorized:
            supported += 1
        elif (unit == "个月" and value == 12 and (Decimal(1), "学年") in source_numbers
              or unit == "学年" and value == 1 and (Decimal(12), "个月") in source_numbers):
            warnings.append({"claim": display, "reason": "academic_year_conversion_unverified"})
        elif (value, "") in source_numbers or not unit and any(v == value for v, _ in source_numbers):
            warnings.append({"claim": display, "reason": "unit_unverified"})
        else:
            unsupported_numbers.append(display)
    return {"unsupported_articles": list(dict.fromkeys(unsupported_articles)),
            "unsupported_numbers": list(dict.fromkeys(unsupported_numbers)),
            "soft_warnings": warnings, "checked_mentions": total, "matched_mentions": supported,
            "support_ratio": round(supported / total, 4) if total else None}


def audit_blocks(blocks):
    reports = [dict(_verify_support(block["text"], block["evidence"], block.get("authorized_text", "")),
                    block_index=index, evidence_ids=[row.get("evidence_id") for row in block["evidence"]])
               for index, block in enumerate(blocks)]
    total, matched = sum(row["checked_mentions"] for row in reports), sum(row["matched_mentions"] for row in reports)
    return {"support_audit_mode": "lexical_log_only_not_semantic_validation",
            "unsupported_articles": list(dict.fromkeys(x for row in reports for x in row["unsupported_articles"])),
            "unsupported_numbers": list(dict.fromkeys(x for row in reports for x in row["unsupported_numbers"])),
            "soft_warnings": [warning for row in reports for warning in row["soft_warnings"]],
            "support_ratio": round(matched / total, 4) if total else None,
            "checked_mentions": total, "matched_mentions": matched, "block_support": reports}
