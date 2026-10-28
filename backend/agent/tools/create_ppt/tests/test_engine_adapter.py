import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent.tools.create_ppt.scripts.model import Claim, Slide
from agent.tools.create_ppt.scripts.ppt_master import page_svg


def test_svg_escapes_material_text_and_keeps_cjk_language():
    root = ET.fromstring(page_svg(Slide("bullets", "课程", [Claim("A < B & 中文")]), {}))
    assert root.attrib["lang"] == "zh-CN"
    assert "A < B & 中文" in "".join(root.itertext())


def test_adapter_does_not_silently_flatten_native_data():
    with pytest.raises(ValueError, match="原生数据"):
        page_svg(Slide("chart", "统计", chart={}), {})
