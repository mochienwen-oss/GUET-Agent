"""Source-exact course records, not a semantic completeness checker."""
import json
import re
from decimal import Decimal


def course_units(eid: str, text: str, row: dict) -> list[dict]:
    data = json.loads(text)
    owner = {"major": data["major"], "course": data["course"]["name"]}
    # Exact field/record spans avoid repeating the entire card as another unit.
    units = []
    decoder = json.JSONDecoder()
    for key in ("major", "college", "course"):
        field = re.search(r'"' + key + r'"\s*:\s*', text)
        if field is None:
            continue
        _, end = decoder.raw_decode(text, field.end())
        units.append({"unit_id": eid + ":" + key, "evidence_id": eid,
                      "unit_type": "course_field", "text": text[field.start():end],
                      "start": field.start(), "end": end, "owner": owner,
                      "heading_path": row.get("heading_path") or []})
    match = re.search(r'"indicators"\s*:\s*\[', text)
    if not match:
        raise ValueError("课程证据缺少indicators数组")
    cursor = match.end()
    for index, record in enumerate(data["indicators"], 1):
        cursor += len(text[cursor:]) - len(text[cursor:].lstrip(" \t\r\n,"))
        parsed, end = decoder.raw_decode(text, cursor)
        if parsed != record or not isinstance(record.get("id"), str) or not isinstance(record.get("text"), str):
            raise ValueError("课程指标记录格式错误")
        units.append({"unit_id": "%s:r%d" % (eid, index), "evidence_id": eid,
                      "unit_type": "indicator_record", "text": text[cursor:end],
                      "start": cursor, "end": end, "owner": owner,
                      "record_id": record["id"], "record": record,
                      "heading_path": row.get("heading_path") or []})
        cursor = end
    return units


def _indicator_text(claim, units):
    """Exclude only source-matched course quantities, retaining other ID spans."""
    quantities = {"学分": set(), "学时": set()}
    for uid in claim["unit_ids"]:
        unit = units.get(uid, {})
        if unit.get("unit_type") != "course_field":
            continue
        course = json.loads("{" + unit["text"] + "}").get("course")
        if not isinstance(course, dict):
            continue
        for field, measure in (("credits", "学分"), ("hours", "学时"),
                               ("theory", "学时"), ("lab", "学时"), ("practice", "学时")):
            if isinstance(course.get(field), (int, float)):
                quantities[measure].add(Decimal(str(course[field])))
    text = claim["text"]
    excluded = set()
    for match in re.finditer(r"(?<![\d.])(\d+\.\d+)(?![\d.])\s*(学分|学时)", text):
        if Decimal(match[1]) not in quantities[match[2]]:
            raise ValueError("课程数量未与引用字段一致")
        excluded.add(match.span(1))
    chars = list(text)
    for start, end in excluded:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def check_enumerations(packet: dict, delivered: list[dict], *, require_unique: bool = True) -> None:
    if not packet.get("enumerations"):
        return
    units = {unit["unit_id"]: unit for unit in packet.get("evidence_units") or []}
    indicator_texts = [_indicator_text(claim, units) for claim in delivered]
    for enumeration in packet.get("enumerations") or []:
        for record in enumeration["records"]:
            pattern = r"(?<![\d.])" + re.escape(record["record_id"]) + r"(?![\d.])"
            matches = [claim for claim, text in zip(delivered, indicator_texts)
                       if record["unit_id"] in claim["unit_ids"]
                       and re.search(pattern, text)]
            if not matches or (require_unique and len(matches) != 1):
                reason = "未唯一交付" if require_unique else "未交付或未正确引用"
                raise ValueError("完整枚举记录%s：%s" % (reason, record["record_id"]))
        expected = {r["record_id"] for r in enumeration["records"]}
        record_units = {r["unit_id"] for r in enumeration["records"]}
        for claim, text in zip(delivered, indicator_texts):
            if record_units.intersection(claim["unit_ids"]):
                ids = set(re.findall(r"(?<![\d.])\d+\.\d+(?![\d.])", text))
                if ids - expected:
                    raise ValueError("完整枚举出现未知指标编号")
    if not require_unique:
        records = [record for enumeration in packet.get("enumerations") or [] for record in enumeration["records"]]
        by_unit = {record["unit_id"]: record["record_id"] for record in records}
        by_unit.update({unit["unit_id"]: unit["record_id"] for unit in packet.get("evidence_units") or []
                        if unit.get("record_id")})
        for claim, text in zip(delivered, indicator_texts):
            mentioned = {record["record_id"] for record in records
                         if re.search(r"(?<![\d.])" + re.escape(record["record_id"]) + r"(?![\d.])", text)}
            bound = {by_unit[uid] for uid in claim["unit_ids"] if uid in by_unit}
            if mentioned - bound:
                raise ValueError("完整枚举编号与记录引用不对应：%s" % "、".join(sorted(mentioned - bound)))
