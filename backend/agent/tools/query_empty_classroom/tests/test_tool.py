from agent.runtime import registry


def test_pointer_tool_has_no_fake_parameters_and_only_code_owned_pointer_source():
    schema = next(item for item in registry.schemas()
                  if item["function"]["name"] == "query_empty_classroom")
    assert schema["function"]["parameters"].get("properties") == {}
    result = registry.call("query_empty_classroom", {})
    assert result.status == "success" and len(result.sources) == 1
    assert result.sources[0].title == "空教室查询入口"
    assert result.data["entry_url"] in result.sources[0].text
    assert "实时名单" not in result.sources[0].text
    assert result.data["entry_url"].startswith("https://bkjwtest.guet.edu.cn/")
    assert "实时空教室名单我这边**查不到**" in registry.render(result)
