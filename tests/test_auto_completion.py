from conftest import ok

VALUES = [{"value": "python", "label": "python"}]


async def test_auto_completion_fields_calls_the_new_path(h):
    h.responses.append(
        ok(
            {
                "object": "search_auto_completions",
                "entity": "person",
                "field": "skills",
                "completions": VALUES,
            }
        )
    )
    result = await h.call(h.client.search.people_auto_completion_fields, field="skills", query="py")
    assert h.requests[0].url.path == "/v2/search/people/auto-completion-fields"
    assert [item.value for item in result.data.completions] == ["python"]


async def test_the_deprecated_method_keeps_the_former_path_and_answer(h):
    h.responses.append(
        ok(
            {
                "object": "search_auto_suggestions",
                "entity": "person",
                "field": "skills",
                "suggestions": VALUES,
            }
        )
    )
    result = await h.call(h.client.search.people_auto_suggestion, field="skills", query="py")
    assert h.requests[0].url.path == "/v2/search/people/auto-suggestion"
    assert [item.value for item in result.data.suggestions] == ["python"]
