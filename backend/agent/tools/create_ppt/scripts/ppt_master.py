"""Thin, explicit engine evaluation; no shell tools exposed to the model."""
import json
from pathlib import Path
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET

from .render import FONT, sources_markdown

ROOT = Path(__file__).resolve().parents[5]


def _lines(text, pixels, font):
    lines, current, used = [], "", 0
    for character in text:
        width = font * (1 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.55)
        if character == "\n" or (current and used + width > pixels):
            lines.append(current)
            current, used = "", 0
        if character != "\n":
            current += character
            used += width
    if current:
        lines.append(current)
    return lines


def page_svg(slide, evidence):
    if slide.kind in {"chart", "table"}:
        raise ValueError("此适配切片尚未接通原生数据对象，不替换当前可编辑图表/表格制作器")
    root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": "1280", "height": "720",
        "viewBox": "0 0 1280 720", "font-family": FONT, "lang": "zh-CN",
        "data-pptx-page-role": {"cover": "cover", "agenda": "toc", "end": "ending"}.get(slide.kind, "content")})
    ET.SubElement(root, "rect", {"x": "0", "y": "0", "width": "1280", "height": "720", "fill": "#FFFFFF"})
    def text(value, x, y, width, size=28, fill="#1B2A3D", weight="normal"):
        lines = _lines(value, width, size)
        for i, line in enumerate(lines):
            node = ET.SubElement(root, "text", {"x": str(x), "y": str(y + i * (size + 12)),
                "font-size": str(size), "fill": fill, "font-weight": weight})
            node.text = line
        return y + len(lines) * (size + 12)
    text(slide.title, 72, 86, 1136, 42, weight="bold")
    if slide.kind in {"cover", "end"}:
        text(slide.subtitle, 76, 300, 1120, 30, "#1A6B75")
    elif slide.kind == "agenda":
        y = 170
        for i, item in enumerate(slide.items, 1):
            y = text("%02d  %s" % (i, item), 90, y, 1090) + 18
    elif slide.kind == "compare":
        for side, x, label in [("left", 80, slide.left_title), ("right", 670, slide.right_title)]:
            y = text(label, x, 175, 500, 30, "#1A6B75", "bold") + 25
            for claim in slide.claims:
                if claim.column == side:
                    y = text(claim.text, x, y, 510) + 25
            if y > 650:
                raise ValueError("对比页超出可用区域，请拆页")
    else:
        y = 175
        for claim in slide.claims:
            y = text(claim.text, 90, y, 1090) + 25
        if y > 650:
            raise ValueError("页面内容超出可用区域，请拆页")
    ids = {eid for claim in [*slide.claims, *slide.notes] for eid in claim.evidence_ids}
    titles = list(dict.fromkeys(evidence[eid].get("title", "") for eid in sorted(ids) if eid in evidence))
    if titles:
        text("来源：" + "；".join(titles)[:65], 72, 695, 1140, 14, "#1A6B75")
    return ET.tostring(root, encoding="unicode")


def export(slides, evidence: dict, project: Path, output: Path):
    from agent.skills.ppt_master import get
    installed = get('ppt_master')
    skill = installed.skill_dir
    if not (skill / "SKILL.md").is_file():
        raise ValueError("尚未安装固定版本的PPT Master完整Skill包")
    project, output = project.resolve(), output.resolve()
    svg = project / "svg_output"
    if svg.exists() and any(svg.iterdir()):
        raise ValueError("导出工作目录须为空，避免混入上一轮页面")
    svg.mkdir(parents=True, exist_ok=True)
    notes = project / "notes"
    notes.mkdir(exist_ok=True)
    for i, slide in enumerate(slides, 1):
        name = "%02d_page" % i
        (svg / (name + ".svg")).write_text(page_svg(slide, evidence), encoding="utf-8")
        ids = {eid for claim in [*slide.claims, *slide.notes] for eid in claim.evidence_ids}
        (notes / (name + ".md")).write_text("\n".join([*(c.text for c in slide.notes),
            *["来源：%s；%s；%s" % (evidence[eid].get("title", ""), evidence[eid].get("scope", ""),
                                        evidence[eid].get("url", "")) for eid in sorted(ids) if eid in evidence]]), encoding="utf-8")
    (project / "sources.md").write_text(sources_markdown(evidence, slides), encoding="utf-8")
    commands = [
        [sys.executable, str(skill / "scripts/svg_quality_checker.py"), str(project),
         "--quick-generate", "--canonical-authoring", "--stage", "final", "--json"],
        [sys.executable, str(skill / "scripts/svg_to_pptx.py"), str(project), "--quick-generate",
         "--with-notes", "--primary-language", "zh-CN", "-t", "none", "-a", "none", "-o", str(output)],
    ]
    for i, command in enumerate(commands):
        result = subprocess.run(command, timeout=120, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (project / ("check.log" if i == 0 else "export.log")).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode:
            raise ValueError("PPT Master%s未通过；保留日志，不降级绕过检查" % ("检查" if i == 0 else "导出"))
    if not output.is_file():
        raise ValueError("PPT Master未生成目标文件")
    return {"path": str(output), "engine": "ppt-master",
            "commit": installed.source.get("commit", ""), "pages": len(slides)}
