"""Pure observation renderer for regulation-search results."""
from agent.core import ToolResult
from agent.core import citations


def render(result: ToolResult) -> str:
    if result.status == "irrelevant_result":
        return "【未命中】查询已执行，但返回的内容与本次问题无关，不能作为回答依据。"
    blocks = result.data.get("blocks") or []
    if not blocks:
        return "未检索到相关资料。"
    out = []
    coverage = result.data.get("coverage")
    if coverage:
        out.append("【检索词匹配】%d/%d；未匹配：%s。仅为词面信号，不代表事实缺失或证据充分，不要求机械补查。" % (
            len(coverage["covered_terms"]), len(coverage["question_terms"]),
            "、".join(coverage["missing_terms"]) or "无"))
    if (result.data.get("evidence_units") or {}).get("incomplete"):
        out.append("【证据单元不完整】部分关联内容未能确认或受预算限制，不能据此声称条件或表格已经列全。")
    if result.data.get("reason") == "table_enumeration_incomplete":
        out.append("【证据不完整】本次表格证据未全部返回，不能据此声称已经列全。")
    for index, block in enumerate(blocks, 1):
        source = block.get("source") or "未知文档"
        path = " / ".join(citations.clean_path(block.get("heading_path") or [], source))
        head = "[%d]《%s》" % (index, citations.short_title(source))
        if path:
            head += " " + path
        unit = block.get("evidence_unit") or {}
        boundary = ""
        if unit:
            labels = {"article": "条款原块", "table": "同表行证据", "section": "同章节窗口"}
            boundary = "【" + labels.get(unit["kind"], "证据块") + "；各块保留独立来源"
            if unit.get("relation") == "table_first_row_candidate":
                boundary += "；表首行仅为表头候选"
            boundary += "】\n"
            relation = unit.get('relation') or 'selected'
            if relation != 'selected':
                boundary += '【补充资料：' + {
                    'prerequisite': '申请前提', 'applicability': '适用范围',
                    'measurement_basis': '数值口径', 'table_header': '原表首行'
                }.get(relation, '结构关联上下文') + '；不能自动作为其他规定的例外】\n'
        out.append(head + "\n" + boundary + (block.get("text") or "").strip())
    return "\n\n".join(out)
