"""Evidence-bound linear score conversion; no eval or model arithmetic."""
import re
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from html import unescape


def validate_recorded_text(text, refs, units, calculations, identity=""):
    """Shared mechanical calculation binding for every composition protocol."""
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in calculations for ref in refs):
        raise ValueError("claim引用了不存在的计算记录：%s" % identity)
    for ref in refs:
        record = calculations[ref]
        if not set(record["source_refs"]) <= {unit["evidence_id"] for unit in units}:
            raise ValueError("计算记录与claim引用来源不一致：%s" % identity)
        if not re.search(r"(?<![\d.])" + re.escape(record["final_result"]) + r"(?:\.0+)?\s*分", text):
            raise ValueError("计算结果未正确交付：%s" % identity)
        declared = re.findall(
            r"(?:你(?:的)?(?:四级|六级|CET\s*[-－]?\s*[46])?(?:考试)?(?:成绩|分数)?|"
            r"本次输入(?:成绩)?|输入成绩|实际成绩)(?:是|为|考了|考到)?\s*(\d{3}(?:\.\d+)?)\s*分", text, re.I)
        if any(Decimal(value) != Decimal(record["inputs"]["score"]) for value in declared):
            raise ValueError("正文输入成绩与计算记录不一致：%s" % identity)
        source = "\n".join(unit["text"] for unit in units)
        allowed = set(re.findall(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?", source))
        allowed.update([record["inputs"]["score"], record["raw_result"], record["final_result"]])
        values = {Decimal(value) for value in allowed}
        if any(Decimal(value) not in values for value in re.findall(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?", text)):
            raise ValueError("计算正文含未授权数值：%s" % identity)
    if not refs and re.search(r"\d+(?:\.\d+)?\s*[×÷*/]\s*\d+(?:\.\d+)?\s*=", text):
        raise ValueError("实际运算缺少计算记录：%s" % identity)


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


def _evaluate(rule: dict, value: Decimal):
    case = rule["case"]
    low, high = Decimal(case["low"]), Decimal(case["high"])
    if not (value >= low if case["low_inclusive"] else value > low):
        return None
    if not (value <= high if case["high_inclusive"] else value < high):
        return None
    if "constant" in case:
        return Decimal(case["constant"]), None
    return value * Decimal(case["multiplier"]), "%s×%s" % (value, case["multiplier"])


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


def calculation_task(packet: dict) -> dict:
    """One shared obligation for routing and every final-answer exit.

    Only select an applicable source-bound rule here. Arithmetic stays in the
    actual calculate tool, whose execution is logged by the normal graph path.
    """
    question = packet.get("question") or ""
    brief = packet.get("question_brief") or {}
    parameters = {item["name"]: item["value"] for item in brief.get("parameters") or []}
    committed = "operation" in brief
    # Positive, narrow fast path: unrecognized wording keeps Agent planning.
    # Do not infer a single goal merely from the absence of conjunctions.
    can_skip_planning = brief["operation"] == "score_conversion" if committed else is_single_conversion(question)
    result_question = bool(re.search(r"多少|几分|认定成|认定为|换成", question))
    operation = bool(re.search(r"换算|折算|认定|大学英语", question))
    followup_score = bool(re.match(r"^那.*\d{3}(?:\.\d+)?\s*分.*呢", question))
    policy_query = bool(re.search(r"规则|政策|办法|标准|条件", question))
    if committed:
        if any(gap["field"] in {"exam", "score"} for gap in brief.get("input_gaps") or []):
            return {"status": "unresolved", "reason": "input_not_confirmed"}
        if not {"exam", "score"} <= set(parameters):
            return {"status": "unresolved", "reason": "input_not_confirmed"} if can_skip_planning else {"status": "not_required"}
    elif not ((operation and (result_question or not policy_query)) or followup_score):
        return {"status": "not_required"}
    history = [{"role": "user", "content": text} for text in packet.get("user_history") or []]
    exams = re.findall(r"CET\s*[-－]?\s*([46])|([四六])级", question, re.I)
    kinds = {parameters["exam"]} if committed else {"CET" + (a or {"四": "4", "六": "6"}[b]) for a, b in exams}
    if not kinds:
        kinds = {r["exam"] for r in packet.get("calculation_rules") or []}
    candidates = []
    for exam in kinds:
        try:
            value = Decimal(parameters["score"]) if committed else user_score(question, history, exam)[0]
        except ValueError:
            continue
        for rule in packet.get("calculation_rules") or []:
            if rule["exam"] != exam:
                continue
            case = rule["case"]
            lower, upper = Decimal(case["low"]), Decimal(case["high"])
            if ((value >= lower if case["low_inclusive"] else value > lower)
                    and (value <= upper if case["high_inclusive"] else value < upper)):
                candidates.append((rule, str(value)))
    if not candidates:
        return {"status": "unresolved", "reason": "input_or_rule_not_confirmed"}
    signatures = {(r["exam"], score, json.dumps(r["case"], sort_keys=True)) for r, score in candidates}
    if len(signatures) != 1:
        return {"status": "unresolved", "reason": "conflicting_rules"}
    rule, score = candidates[0]
    for record in packet.get("calculations") or []:
        if (record.get("rule_id") == rule["rule_id"] and record.get("exam") == rule["exam"]
                and record.get("inputs") == {"score": score}
                and record.get("source_revision") == rule["source_revision"]
                and record.get("source_refs") == rule["source_refs"]):
            return {"status": "complete", "calculation_id": record["calculation_id"],
                    "can_skip_planning": can_skip_planning}
    return {"status": "pending", "call": {"name": "calculate", "arguments": {
        "rule_id": rule["rule_id"], "inputs": {"score": score}}},
        "can_skip_planning": can_skip_planning}


def calculate(rule_id: str, inputs: dict, *, rules: list[dict], question: str,
              history: list, task=None) -> dict:
    rule = next((r for r in rules if r["rule_id"] == rule_id), None)
    if rule is None:
        raise ValueError("规则不属于本轮可用证据")
    if set(inputs) != {"score"} or not isinstance(inputs["score"], str) or not re.fullmatch(r"\d{3}(?:\.\d+)?", inputs["score"]):
        raise ValueError("inputs只接受明确的score字符串")
    if task is not None:
        if task.parameter("exam") != rule["exam"] or any(g.field in {"exam", "score"} for g in task.unresolved):
            raise ValueError("计算规则与本轮已确认输入不一致")
        score_parameter = next((p for p in task.parameters if p.name == "score"), None)
        if score_parameter is None or not score_parameter.origin:
            raise ValueError("缺少本轮可信成绩")
        value, origin = Decimal(score_parameter.value), score_parameter.origin
    else:
        value, origin = user_score(question, history, rule["exam"])
    if not 0 <= value <= 710:
        raise ValueError("成绩超出CET有效范围")
    if Decimal(inputs["score"]) != value:
        raise ValueError("计算输入与用户明确成绩不一致")
    evaluated = _evaluate(rule, value)
    if evaluated is None:
        raise ValueError("成绩不在所选规则区间")
    other = [_evaluate(r, value) for r in rules if r["exam"] == rule["exam"]]
    if len({x[0] for x in other if x is not None}) != 1:
        raise ValueError("本轮存在冲突计算规则，不能选择其中一个")
    raw, expression = evaluated
    final = str(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    cid = "k" + hashlib.sha256((rule_id + str(value)).encode()).hexdigest()[:12]
    return {"calculation_id": cid, "rule_id": rule_id, "exam": rule["exam"],
            "inputs": {"score": str(value)}, "input_origin": origin,
            "matched_case": rule["case"], "expression": expression,
            "raw_result": str(raw), "final_result": final, "unit": rule["unit"],
            "rounding": rule["rounding"], "source_refs": rule["source_refs"],
            "source_row": rule["source_row"],
            "source_revision": rule["source_revision"]}

