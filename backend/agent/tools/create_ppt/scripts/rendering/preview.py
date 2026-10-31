"""Render the exported PPTX, never substitute authoring SVG for the final deck."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json


def _windows_preview(pptx, output, slide_ids):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = deck = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        app.DisplayAlerts = 1
        deck = app.Presentations.Open(str(pptx.resolve()), ReadOnly=True, Untitled=False, WithWindow=False)
        if deck.Slides.Count != len(slide_ids):
            raise ValueError("导出PPT的页数与内容不一致")
        for i, sid in enumerate(slide_ids, 1):
            deck.Slides(i).Export(str((output / (sid + ".png")).resolve()), "PNG", 1280, 720)
    finally:
        if deck is not None:
            deck.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def render_previews(pptx: Path, output: Path, slide_ids: list[str]) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        job = output / "render-input.json"
        job.write_text(json.dumps({"pptx": str(pptx.resolve()), "output": str(output.resolve()),
                                   "slide_ids": slide_ids}), encoding="utf-8")
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(job.resolve())],
                capture_output=True, timeout=90, text=True, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise ValueError("PowerPoint预览超时，未交付未经预览的草稿") from None
        (output / "render.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode:
            raise ValueError("PowerPoint预览未完成，请检查任务render.log中的本机运行错误")
    else:
        binary = shutil.which("soffice") or shutil.which("libreoffice")
        if not binary:
            raise ValueError("未安装最终PPT预览引擎LibreOffice，不能声明预览已完成")
        profile = (output / "office-profile").resolve().as_uri()
        subprocess.run([binary, "-env:UserInstallation=" + profile, "--headless", "--convert-to", "pdf",
                        "--outdir", str(output.resolve()), str(pptx.resolve())],
                       check=True, timeout=120, capture_output=True)
        import fitz
        with fitz.open(output / (pptx.stem + ".pdf")) as doc:
            if len(doc) != len(slide_ids):
                raise ValueError("预览页数与PPT不一致")
            for page, sid in zip(doc, slide_ids):
                page.get_pixmap(matrix=fitz.Matrix(1280 / page.rect.width, 1280 / page.rect.width),
                                alpha=False).save(output / (sid + ".png"))
    files = [str(output / (sid + ".png")) for sid in slide_ids]
    if not all(Path(p).is_file() and Path(p).stat().st_size for p in files):
        raise ValueError("未取得完整页面预览")
    return files


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    _windows_preview(Path(job["pptx"]), Path(job["output"]), job["slide_ids"])
