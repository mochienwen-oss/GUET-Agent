"""Current task interpretation; no graph, model calls or policy conclusions."""
from dataclasses import dataclass, replace
import re

from .scope import RequestScope, request_scope, REFERENCE_DESCRIPTION
from .calculations import conversion_inputs, conversion_request


@dataclass(frozen=True)
class TaskGap:
    kind: str
    field: str
    reason: str


@dataclass(frozen=True)
class Parameter:
    name: str
    value: str
    origin: str
    source_turn: int | None = None


@dataclass(frozen=True)
class TaskBoundary:
    goal: str
    mode: str
    answer_type: str
    subject_scope: RequestScope
    reference_cohorts: tuple[str, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    unresolved: tuple[TaskGap, ...] = ()
    operation: str = "investigate"
    subject_required: bool = True
    describes_reference: bool = False

    def parameter(self, name):
        return next((item.value for item in self.parameters if item.name == name), None)


def question_type(question):
    q = question or ""
    if re.search(r"重复|累加|同时|冲突|区别|分别|一样|相同|不同|一致", q):
        return "comparison"
    if re.search(r"哪些类别|哪些成果|哪些项目|哪些内容|根据什么|依据什么", q):
        return "other"
    permission = r"能否|可以|能不能|是否允许|需不需要|要不要|可不可以|能.{0,30}吗|可以.{0,30}吗|是否.{0,30}"
    quantity = r"多少|多久|多长时间|最高|最低|不低于|不超过|上限|下限|比例|年限|几分|几个|几门|几次"
    if re.search(permission, q) and not re.search(quantity, q):
        return "permission"
    if re.search(quantity, q):
        return "numeric"
    if re.search(r"哪些条件|手续|流程|材料|怎么办|如何办理|步骤|怎么申请|哪些时间|什么时候|时间安排|几点|程序", q):
        return "procedure"
    if re.search(r"能否|可以|能不能|是否允许|需不需要|要不要|可不可以|能(?:用于|申请|认定|加分|办理|参加|保留|转|重复|算|使用|报销|补办|毕业)", q):
        return "permission"
    if re.search(r"适用范围|适用对象|针对谁|哪些专业|哪个学院|年级范围|适用于", q):
        return "scope"
    return "other"


def requested_scope(question, references, has_identity):
    """Recognize explicit reference description and personal conclusions.

    This is not general task decomposition. A declared identity alone does
    not make the following general rule depend on that identity.
    """
    clauses = [clause.strip() for clause in re.split(r"[，,。；;]|并(?=说明|介绍|解释|查询)", question)
               if clause.strip()]
    requests = [clause for clause in clauses if not re.fullmatch(
        r"(?:(?:我|本人|我们)(?:不是|是|为)|也是|而是).{0,50}(?:学生|本科生|研究生|教师|职工|\d{4}级)", clause)]
    subject_required, describes_reference = False, False
    for clause in requests:
        personal = bool(re.search(r"我|本人|我们", clause))
        personal_permission = bool(has_identity and re.search(
            r"能否|能不能|是否|可不可以|(?:能|可以).{0,30}吗", clause))
        explicit_description = bool(re.search(REFERENCE_DESCRIPTION, clause))
        description = bool(references and not personal and re.search(r"方案|文件|材料|规定", clause)
                           and (explicit_description or (not personal_permission and
                                question_type(clause) in {"numeric", "procedure", "scope"})))
        describes_reference |= description
        subject_required |= personal or (personal_permission and not description)
    return subject_required or not requests, describes_reference


def interpret_task(question, *, history=(), resolution=None, scope=None, pending_task=None, turn_index=None,
                   operation_hint=None):
    subject = scope or request_scope(question, history=history, resolution=resolution)
    mode = "personal" if re.search(
        r"(?:我|本人)(?:是|的|能|可以|已经|已|今年|现在|想|有|参加|获得|考|没|未)|我们(?:能|可以)", question) else "general_rule"
    if subject.provenance.get("cohort") == "current_user_identity":
        mode = "personal"
    if ((subject.cohort and any(year != subject.cohort for year in subject.reference_cohorts))
            or len(subject.reference_cohorts) > 1 or len(subject.reference_levels) > 1
            or re.search(r"区别|对比|比较|有什么不同", question)):
        mode = "comparison"
    parameters = []
    for key in ("cohort", "level", "college", "subject"):
        value = getattr(subject, key)
        if value:
            parameters.append(Parameter(key, value, subject.provenance.get(key, "request_scope")))
    names = {}
    for mention in getattr(resolution, "mentions", ()):
        selected = mention.selected
        if selected is not None and mention.support_tier in {"T1", "T2", "T3"}:
            names.setdefault(selected.entity_type, set()).add(selected.canonical_name)
        elif mention.support_tier in {"T1", "T2", "T3"} and question[mention.end:].startswith("专业"):
            # Reuse the existing certified argument-type ruling; never choose
            # a same-type ambiguous entity or alter the frozen resolver data.
            from entities import entity_calls
            checked = entity_calls.validate_calls([{"name": "query_major_requirements",
                "arguments": {"major": mention.raw_text}}], resolution)
            if checked and checked[0].decision == "allow":
                names.setdefault("major", set()).add(checked[0].canonical_args["major"])
    for key, values in names.items():
        if len(values) == 1:
            parameters.append(Parameter(key, next(iter(values)), "catalog_resolution"))
    if len(names.get("major", ())) == 1:
        subject = replace(subject, majors=tuple(names["major"]),
                          provenance={**subject.provenance, "majors": "catalog_resolution"})
    operation = "investigate"
    inputs, input_errors, continuing = conversion_inputs(question, history=history,
        pending=pending_task, turn_index=turn_index)
    conversion = conversion_request(question) or continuing or (
        operation_hint == "score_conversion" and not re.search(r"雅思|托福|IELTS|TOEFL", question, re.I))
    has_conversion_part = conversion or (re.search(r"大学英语|换算|折算|认定", question)
        and re.search(r"英语|CET\s*[-－]?\s*[46]|[四六]级", question, re.I)
        and re.search(r"多少|几分|认定成|认定为", question))
    if has_conversion_part and not re.search(r"雅思|托福|IELTS|TOEFL", question, re.I):
        parameters += [Parameter(key, value, origin, source_turn)
                       for key, (value, source_turn, origin) in inputs.items()]
        operation = "score_conversion" if conversion else "investigate"
        mode = "personal"
        if "专业" not in question:
            subject = replace(subject, majors=(), provenance={k:v for k,v in subject.provenance.items() if k != "majors"})
            parameters = [item for item in parameters if item.name != "major"]
    unresolved = tuple(TaskGap("input", key, value) for key, value in subject.provenance.items()
                       if value.endswith("ambiguous"))
    if conversion:
        errors = dict(input_errors)
        unresolved += tuple(TaskGap("input", key, errors.get(key, "missing"))
                            for key in ("exam", "score") if key not in inputs)
    subject_required, describes_reference = requested_scope(
        question, subject.reference_cohorts,
        mode == "personal" or subject.provenance.get("cohort") == "current_user_identity" or bool(unresolved))
    if (not subject_required and mode == "personal" and operation != "score_conversion"
            and not any(item.name == "exam" for item in parameters)):
        mode = "general_rule"
    return TaskBoundary(question, mode, question_type(question), subject,
                        subject.reference_cohorts, tuple(parameters), unresolved, operation,
                        subject_required, describes_reference)
