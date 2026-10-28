import pytest
from pydantic import ValidationError

from agent.runtime import registry
from agent.tools.search_regulations.schemas import SearchRegulationsArgs


def test_keywords_are_optional_and_default_to_empty_list():
    args = SearchRegulationsArgs.model_validate({"query": "补考有什么规定"})
    assert args.query == "补考有什么规定"
    assert args.keywords == []


def test_keywords_are_trimmed_deduplicated_and_drop_blanks():
    args = SearchRegulationsArgs.model_validate({
        "query": "  补考有什么规定  ",
        "keywords": [" 补考 ", "课程考核", "", "补考", "   "],
    })
    assert args.query == "补考有什么规定"
    assert args.keywords == ["补考", "课程考核"]


@pytest.mark.parametrize("keywords", [
    ["词%d" % i for i in range(13)],
    ["超" * 33],
    "补考 课程考核",
])
def test_invalid_keywords_are_rejected(keywords):
    with pytest.raises(ValidationError):
        SearchRegulationsArgs.model_validate({"query": "补考有什么规定", "keywords": keywords})


def test_runtime_schema_uses_the_execution_input_model():
    runtime_search = registry.schemas()[0]["function"]["parameters"]
    assert runtime_search == SearchRegulationsArgs.model_json_schema()
    assert "keywords" in runtime_search["properties"]
    assert runtime_search["required"] == ["query"]
