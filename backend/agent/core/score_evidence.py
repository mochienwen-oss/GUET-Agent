"""Source-row recognition and explicit score inputs for retrieval; no calculation tool."""
import re
import hashlib
import json
from decimal import Decimal
from html import unescape


def conversion_rules(evidence: dict, revision=None) -> list[dict]:
    """Recognize only the already-supported CET policy, retaining exact rows."""
    rules = []
    for eid, item in evidence.items():
        text = unescape(item.snippet)
        if not re.search(r"四舍五入.*取整|取整.*四舍五入", text):
            continue
        head = re.split(r"同节正文[／/]|表外说明", text)[0]
        for row in (head.splitlines() if "|" in head else [head]):
            compact = re.sub(r"\s+", "", row).replace("CET-", "CET")
            exams = re.findall(r"CET[46](?!\d)", compact)
            if len(set(exams)) != 1:
                continue
            interval = re.search(r"(\d+)(≤|<)A(≤|<)(\d+)", compact)
            high = re.search(r"A(≥|>)(\d+)", compact)
            multiplier = re.search(r"A[×*](0\.\d+)", compact)
            constant = re.search(r"(?:大学英语成绩|换算成绩|折算成绩|\|)[：:=|]*(100)(?!\d)", compact)
            if interval and multiplier:
                low, left, right, upper = interval.groups()
                case = {"low": low, "low_inclusive": left == "≤", "high": upper,
                        "high_inclusive": right == "≤", "multiplier": multiplier[1]}
            elif high and constant:
                case = {"low": high[2], "low_inclusive": high[1] == "≥", "high": "710",
                        "high_inclusive": True, "constant": constant[1]}
            else:
                continue
            identity = json.dumps([eid, row, revision], ensure_ascii=False)
            rules.append({"rule_id": "r" + hashlib.sha256(identity.encode()).hexdigest()[:10],
                          "exam": exams[0], "case": case, "source_row": row,
                          "source_refs": [eid], "source_revision": revision,
                          "rounding": "ROUND_HALF_UP", "unit": "分"})
    return rules


def user_score(question: str, history: list, exam: str) -> tuple[Decimal, str]:
    """Use the nearest user turn with an explicit, unambiguous score and exam."""
    continuation = bool(re.match(r"^(?:那|那么|继续|还是|改成|改为|换成|成绩|分数|考了)", question.strip()))
    continuation = continuation or bool(re.fullmatch(r"\s*\d{3}(?:\.\d+)?\s*分?[。！？!?]?\s*", question))
    users = ([x.get("content") or "" for x in history if x.get("role") == "user"]
             if continuation else []) + [question]
    score = None
    origin = ""
    chosen_exam = None
    for text in reversed(users):
        values = set()
        matches = re.findall(r"CET\s*[-－]?\s*([46])|([四六])级", text, re.I)
        exams = {"CET" + (a or {"四": "4", "六": "6"}[b]) for a, b in matches}
        if len(exams) > 1:
            raise ValueError("用户考试类别不唯一，需先澄清")
        if chosen_exam is not None and exams and chosen_exam not in exams:
            raise ValueError("前序成绩属于另一考试，不能跨考试补齐")
        if score is None:
            if re.search(r"\d\s*分\s*(?:及?以上|及?以下|以外)|[≥≤<>]|\d\s*[至到—~～]\s*\d", text):
                raise ValueError("政策区间不是用户明确成绩")
            values = set(re.findall(r"(?<![\d.])(\d{3}(?:\.\d+)?)(?![\d.])", text))
            values = {value for value in values if not re.search(
                re.escape(value) + r"\s*(?:元|年|人|天|小时|分钟)", text)}
            explicit_score = bool(re.search(r"\d{3}(?:\.\d+)?\s*分(?!钟)", text))
            bare_reply = bool(re.fullmatch(r"\s*\d{3}(?:\.\d+)?\s*", text))
            if not explicit_score and not bare_reply:
                values = set()
            if len(values) > 1:
                raise ValueError("用户成绩不唯一，需先澄清")
            if values:
                score, origin = Decimal(next(iter(values))), text
        if matches and chosen_exam is None:
            chosen_exam = next(iter(exams))
        if score is not None and chosen_exam is not None:
            break
        if text != question and not exams and not values:
            break
    if score is None or chosen_exam != exam:
        raise ValueError("缺少可追溯的考试类别或成绩")
    if not Decimal(0) <= score <= Decimal(710):
        raise ValueError("成绩超出CET有效范围")
    return score, origin


def is_single_conversion(question):
    return bool(re.fullmatch(
        r"\s*(?:那|那么|请问)?(?:我)?(?:CET\s*[-－]?\s*[46]|[四六]级)?"
        r"(?:成绩)?(?:考了|考到|是|为)?\s*\d{3}(?:\.\d+)?\s*分[，,\s]*"
        r"(?:按(?:学校规定|规定|学校))?(?:大学英语(?:成绩)?)?(?:能|可以|可)?"
        r"(?:认定成|认定为|认定|换算成|换算为|换算|折算成|折算为|折算)?"
        r"(?:多少分?|几分)?(?:呢)?[？?。.!！]*\s*", question, re.I))


def conversion_request(question):
    """Narrow positive shortcut; unmatched language stays with the planner."""
    if re.search(r"雅思|托福|IELTS|TOEFL", question, re.I):
        return False
    if re.search(r"(?:还|并|同时).*(?:材料|流程|申请|条件|办理)", question):
        return False
    if is_single_conversion(question) and re.search(r"英语|CET\s*[-－]?\s*[46]|[四六]级", question, re.I):
        return True
    if not re.fullmatch(r"[^？?。]*(?:多少(?:分)?|几分|认定分数|大学英语(?:成绩|分数)|\d{3}(?:\.\d+)?分|折算一下)(?:呢|吗)?[？?。.!！]*", question.strip()):
        return False
    return bool(re.search(r"英语|CET\s*[-－]?\s*[46]|[四六]级", question, re.I)
                and re.search(r"换算|折算|换成|认定成|认定为|认定多少|算一下|算算", question)
                and not (re.search(r"规则|政策|办法|标准|条件", question)
                         and not re.search(r"帮我|替我|我.*(?:考|成绩)|\d{3}\s*分", question)))


def conversion_inputs(question, *, history=(), pending=None, turn_index=None):
    """Recover only a live pending task and explicit user scores/exams."""
    active = (pending is not None and pending.task_type == "score_conversion"
              and turn_index is not None and pending.last_updated_turn == turn_index - 1)
    reply = bool(re.fullmatch(r"\s*(?:(?:我|成绩|分数|考了|是|为|改为|改成|不是|而是|和|或|还是|、|，|,|。|\s)*"
                             r"(?:CET\s*[-－]?\s*[46]|[四六]级|\d{3}(?:\.\d+)?分?))+[。！？!?]?\s*", question, re.I))
    continuing = active and reply
    values = {item.name: (item.value, item.source_turn, "pending_user") for item in pending.inputs} if continuing else {}
    exams = {"CET" + (a or {"四": "4", "六": "6"}[b]) for a, b in
             re.findall(r"CET\s*[-－]?\s*([46])|([四六])级", question, re.I)}
    if not exams and not active and re.match(r"^(?:那|那么|继续|改成|改为)", question.strip()) and is_single_conversion(question):
        previous = next((row.get("content") or "" for row in reversed(history) if row.get("role") == "user"), "")
        prior_exams = {"CET" + (a or {"四": "4", "六": "6"}[b]) for a, b in
            re.findall(r"CET\s*[-－]?\s*([46])|([四六])级", previous, re.I)}
        if len(prior_exams) == 1 and conversion_request(previous):
            exam = next(iter(prior_exams))
            try:
                user_score(question, list(history), exam)
            except ValueError:
                pass
            else:
                values["exam"] = (exam, None, "prior_user")
                continuing = True
    scores = set(re.findall(r"(?<![\d.])(\d{3}(?:\.\d+)?)(?![\d.])\s*分(?!钟)", question))
    correction = re.search(r"不是\d{3}(?:\.\d+)?分[，,\s]*(?:而)?是(\d{3}(?:\.\d+)?)分", question)
    if correction:
        scores = {correction[1]}
    if continuing and re.fullmatch(r"\s*\d{3}(?:\.\d+)?\s*", question):
        scores.add(question.strip())
    invalid = bool(re.search(r"[≥≤<>]|\d\s*[至到—~～]\s*\d|\d\s*(?:以上|以下)", question))
    gaps = []
    if len(exams) > 1:
        # An explicit correction replaces the negated old exam, not two exams.
        correction = re.search(r"不是(?:CET\s*[-－]?\s*[46]|[四六]级)[，,\s]*(?:而)?是(CET\s*[-－]?\s*[46]|[四六]级)", question, re.I)
        if correction:
            exams = {"CET4" if re.search(r"4|四", correction[1]) else "CET6"}
        else:
            values.pop("exam", None)
            values.pop("score", None)
            gaps.append(("exam", "conflicting"))
    if len(exams) == 1:
        exam = next(iter(exams))
        if "exam" in values and values["exam"][0] != exam:
            values.pop("score", None)
        values["exam"] = (exam, turn_index, "current_user")
    if len(scores) > 1 or invalid:
        values.pop("score", None)
        gaps.append(("score", "conflicting"))
    elif scores:
        score = next(iter(scores))
        if not 0 <= Decimal(score) <= 710:
            values.pop("score", None)
            gaps.append(("score", "out_of_range"))
        else:
            values["score"] = (score, turn_index, question)
    # Preserve established full-input history handling for explicit follow-ups.
    if "exam" in values and "score" not in values and not gaps and not continuing:
        try:
            score, origin = user_score(question, list(history), values["exam"][0])
        except ValueError:
            pass
        else:
            values["score"] = (str(score), turn_index, origin)
    return values, gaps, continuing
