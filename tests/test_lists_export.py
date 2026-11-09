import json

import httpx

MEMBER = {
    "kind": "member",
    "id": "m1",
    "entityId": "prs_ada",
    "externalId": None,
    "person": None,
    "company": None,
    "isFullProfile": False,
    "customColumns": [],
}
PENDING = {
    "kind": "pending",
    "id": "p1",
    "signal": {"email": "missing@example.com"},
    "customColumns": [{"id": "c1", "name": "Owner", "value": None}],
}


async def test_export_gives_the_csv_by_default(h):
    body = "linkedin_url,name\r\nhttps://www.linkedin.com/in/ada,Ada"
    h.responses.append(httpx.Response(200, text=body, headers={"content-type": "text/csv"}))
    text = await h.call(h.client.lists.export, "list_test")
    assert text == body
    assert h.requests[0].url.path == "/v2/lists/list_test/export"
    assert "format" not in h.requests[0].url.params


async def test_export_passes_the_csv_columns(h):
    h.responses.append(
        httpx.Response(200, text="name\r\nAda", headers={"content-type": "text/csv"})
    )
    await h.call(h.client.lists.export, "list_test", fields="name,linkedin_url")
    assert h.requests[0].url.params["fields"] == "name,linkedin_url"


async def test_export_gives_the_ndjson_as_text_when_asked(h):
    body = json.dumps(MEMBER) + "\n" + json.dumps(PENDING) + "\n"
    h.responses.append(
        httpx.Response(200, text=body, headers={"content-type": "application/x-ndjson"})
    )
    text = await h.call(h.client.lists.export, "list_test", format="ndjson")
    assert text == body
    assert [json.loads(value)["kind"] for value in text.splitlines()] == ["member", "pending"]
    assert h.requests[0].url.params["format"] == "ndjson"
