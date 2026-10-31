"""机械 QA：容量、空页、逐主张证据与来源双向检查。

★ **校验对象是「落盘的 `.sources.md` 文本」**，不是内存里那串 —— 见
  `graph/ppt/subgraph.py::_write_sources`。所以下面的 `unlisted_evidence` /
  `unused_source` 是**渲染器的守卫**：正常路径下来源清单由引用集合派生，
  两条都不会响；一旦 `sources_markdown` 漏写、多写或写盘出问题，它们就会响。
  （`tests/test_ppt_stage4.py` 里有用例证明它们**确实能失败**。）
"""
import re

from agent.tools.create_ppt.scripts.rendering.layouts import LAYOUTS
from agent.tools.create_ppt.scripts.content.model import QaIssue, QaReport


_SOURCE_ID = re.compile(r"(?m)^- \[([a-f0-9]{12})\] ")


def check(slides, evidence: dict, sources_md: str) -> QaReport:
    issues = []
    referenced = set()
    listed = set(_SOURCE_ID.findall(sources_md or ""))
    for number, slide in enumerate(slides, 1):
        limits = LAYOUTS.get(slide.kind)
        if limits is None:
            issues.append(QaIssue("unknown_layout", number, slide.kind))
            continue
        if not slide.title.strip():
            issues.append(QaIssue("empty_page", number, "缺少标题"))
        if slide.kind in ("bullets", "compare") and not slide.claims:
            issues.append(QaIssue("empty_page", number, "内容页没有主张"))
        if slide.kind == "agenda" and not slide.items:
            issues.append(QaIssue("empty_page", number, "目录没有条目"))
        if slide.kind in ("cover", "agenda", "end") and slide.claims:
            issues.append(QaIssue("unrendered_content", number, "此版式不显示主张"))
        if slide.kind != "agenda" and slide.items:
            issues.append(QaIssue("unrendered_content", number, "此版式不显示目录条目"))
        content = [*slide.claims, *slide.notes]
        lines = [c.text for c in content] + slide.items
        if len(slide.title) > limits.max_title or len(lines) > limits.max_items or \
                any(len(t) > limits.max_item_chars for t in lines) or \
                len(slide.title) + len(slide.subtitle) + sum(map(len, lines)) > limits.max_total_chars:
            issues.append(QaIssue("overflow", number, "超过版式容量"))
        if slide.kind == "compare" and not (slide.left_title.strip() and slide.right_title.strip()):
            issues.append(QaIssue("empty_page", number, "对比页缺少列标题"))
        if slide.kind == "compare" and any(
                sum(c.column == side for c in slide.claims) > 4 for side in ("left", "right")):
            issues.append(QaIssue("overflow", number, "单列超过四条"))
        for claim in content:
            if not claim.text.strip():
                issues.append(QaIssue("empty_claim", number, "主张文本为空"))
            if claim.kind not in {"fact", "suggestion", "analogy"}:
                issues.append(QaIssue("invalid_claim_kind", number, claim.kind))
            if not claim.evidence_ids and not (claim.gap or "").strip() and claim.kind == "fact":
                issues.append(QaIssue("missing_source", number, claim.text[:40]))
            for eid in claim.evidence_ids:
                referenced.add(eid)
                if eid not in evidence:
                    # 悬空引用（§6.3 校验 3）—— 引了一个台账里没有的 ID。
                    issues.append(QaIssue("dangling_evidence", number, eid))
                if eid not in listed:
                    # ★ 这一条查的是**写出去的那份清单**：引用了却没写进文件，
                    #   读者就没法核对。它比 dangling_evidence 更宽 —— 写盘/格式
                    #   出问题时同样会响（见模块 docstring）。
                    issues.append(QaIssue("unlisted_evidence", number, eid))
            if slide.kind == "compare" and claim in slide.claims and claim.column not in ("left", "right"):
                issues.append(QaIssue("missing_column", number, claim.text[:40]))
        if slide.kind in {"chart", "table"}:
            data = slide.chart if slide.kind == "chart" else slide.table
            if not data or data.get("material_id") not in evidence:
                issues.append(QaIssue("missing_data_source", number, "图表缺少任务数据来源"))
                continue
            eid = data["material_id"]
            referenced.add(eid)
            if eid not in listed:
                issues.append(QaIssue("unlisted_evidence", number, eid))
            if slide.kind == "chart":
                if data.get("chart_type") not in {"bar", "line"} or not data.get("analysis_id"):
                    issues.append(QaIssue("invalid_chart", number, "图表须引用确定性分析"))
                if not 1 <= len(data.get("categories", [])) <= 12 or len(data.get("categories", [])) != len(data.get("values", [])):
                    issues.append(QaIssue("overflow", number, "图表类别需1—12项且数值对应"))
            elif not data.get("rows") or len(data["rows"]) > 8 or not 1 <= len(data.get("columns", [])) <= 6:
                issues.append(QaIssue("overflow", number, "表格单页至多8行、6列，请拆页"))
    for eid in sorted(listed - referenced):
        issues.append(QaIssue("unused_source", 0, eid))
    return QaReport(issues)
