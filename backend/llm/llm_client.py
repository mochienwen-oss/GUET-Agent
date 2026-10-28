"""Shared instructions and small text tasks for the configured API model."""
from pathlib import Path
import re

from core.config import settings
from conversations import titles

SYSTEM_PROMPT = Path(settings.system_prompt_path).read_text(encoding="utf-8").strip()


def grade_relevance(question, context):
    from llm.llm_api_client import complete_sync
    result = complete_sync([{"role": "user", "content":
        "判断参考资料是否能回答问题，只回答是或否。\n问题：%s\n资料：%s" % (question, context)}], task="grading")
    return result["text"].strip() == "是"


async def generate_title(question):
    from llm.llm_api_client import complete
    try:
        result = await complete([
            {"role": "system", "content": "为用户请求写一个不超过12字的会话标题，只返回标题。"},
            {"role": "user", "content": question}], task="title")
        text = result["text"]
        return "" if re.search(r"</?think>|[{}\n]", text, re.I) else titles.clean_model_title(text)
    except Exception:
        return ""
