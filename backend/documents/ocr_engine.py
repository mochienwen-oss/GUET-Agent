# ocr_engine.py —— 共用 OCR 引擎（RapidOCR，纯 CPU，不占显存）
#
# 两个调用方，分工不同、不能混：
#   scripts/ocr2md.py      —— **扫描件整页 OCR**：无文字层的页，整页就是一张图，
#                             OCR 它等于抽取全文。
#   backend/pdfextract.py  —— **正文插图 OCR**：有文字层的页里，抠出插图区域的文字。
#
# 为什么必须分开：实测某些文档的「图片」是整页扫描图（594x841pt = 整页），
# 无脑 OCR 会对扫描件重复劳动，把同一份正文灌两遍。所以 pdfextract 那边要求
# 「该页已有文字层」且「图不是整页」才 OCR。

import re

_engine = None

# ★ 页级「有文字层」的判定阈值 —— **单一来源**，backend/pdfextract.py 与
#   scripts/ocr2md.py 都 import 它。
#   两边一个负责扫描件整页 OCR、一个负责正文插图 OCR，阈值错开就会出现真空或重复劳动
#   （该 OCR 的页没人管 / 同一份正文被灌两遍）。原先靠注释维持「两处都是 50」——
#   注释不会报错，改成 import 才会。
#   实测教训：这里曾经是 200，把「170 字文字层 + 一张课表插图」的页误判成扫描页跳过了。
TEXT_LAYER_MIN = 50

# 网页外壳词：OCR 出这些说明识别的不是正文，而是网页导航/搜索框
_WEB_WORDS = ("信息公开年报", "请输入关键字", "【关闭】")
_MIN_HANZI = 20


def load_engine():
    """懒加载 RapidOCR（首次调用要几秒加载 onnx 模型，之后复用）。"""
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError(
                "缺少 OCR 依赖，先装：pip install rapidocr-onnxruntime pymupdf"
            ) from e
        _engine = RapidOCR()
    return _engine


def ocr_image(img, to_bgr: bool = True) -> list[str]:
    """OCR 一张图，返回**逐行**文本。

    img 是 H×W×3 的 uint8 数组（RGB）。行结构要保留——公文的「第X条」在行首，
    合并成段会让后续的标题识别全部失效。
    """
    if to_bgr:
        import cv2
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    res, _ = load_engine()(img)
    return [t for _box, t, _score in (res or [])]


def ocr_image_with_boxes(img, to_bgr: bool = True, engine=None) -> list[tuple]:
    """OCR 一张图，**保留坐标框** → `[(x0, y0, x1, y1, text), ...]`。

    与 ocr_image 的关系：那个只回文本行（公文正文要的就是行，行首「第X条」不能丢），
    这个多回坐标 —— **表格重建**（`backend/tablebuild.py`）必须靠坐标才能把列分开。
    两条路共用同一个引擎实例，不额外加载模型。

    `engine` 可注入：`scripts/ocr2md.py` 手上已有引擎实例（它还负责缺依赖时给人话提示），
    不传就按模块级单例取 —— 两条调用路径共用这**一份**取框逻辑，别再各写一遍。

    ⚠️ RapidOCR 的第三个元素是 **str 类型的置信度**（不是 float），这里转成 float。
    框是 4 点多边形，这里取外接矩形（表格是横平竖直的，外接矩形足够）。
    """
    if to_bgr:
        import cv2
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    res, _ = (engine or load_engine())(img)
    out = []
    for box, text, score in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((min(xs), min(ys), max(xs), max(ys), text))
    return out


def hanzi_count(text: str) -> int:
    return len(re.sub(r"[^一-鿿]", "", text or ""))


def is_content_text(text: str) -> bool:
    """OCR 结果质检：汉字够多、且不是网页外壳。

    实测插图里既有真内容（课表图形版，358~553 个汉字），也有纯装饰（0 个汉字），
    还有网页导航。这道质检决定要不要把它写进 markdown。
    """
    if not text or not text.strip():
        return False
    if any(w in text for w in _WEB_WORDS):
        return False
    return hanzi_count(text) >= _MIN_HANZI