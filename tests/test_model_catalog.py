"""Catalog requests list selectable model IDs without running inference."""
import json
import os
from time import sleep
from urllib.parse import parse_qs, urlsplit

import pytest

from app.ai import providers
from test_decision_providers import KEY, api, assert_code


@pytest.mark.parametrize("provider,suffix,header,page", [
    ("responses", "/models", "Authorization", {"data": [{"id": "selected"}]}),
    ("chat", "/models", "Authorization", {"data": [{"id": "selected"}]}),
    ("lmstudio", "/models", "Authorization", {"data": [{"id": "selected"}]}),
    ("ollama", "/api/tags", "Authorization", {"models": [{"name": "selected"}]}),
    ("claude", "/models", "x-api-key", {"data": [{"id": "selected"}], "has_more": False}),
    ("gemini", "/models", "x-goog-api-key", {"models": [
        {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/selected", "supportedGenerationMethods": ["generateContent"]}]}),
])
def test_get_catalog_without_model_or_inference(api, provider, suffix, header, page):
    api.respond = lambda body: (200, {}, page)
    raw = {"provider": provider, "base_url": api.base, "api_key": KEY}
    assert providers.list_models(raw) == ["selected"]
    (path, headers, body), = api.calls
    assert path == "/proxy" + suffix and body == b""
    assert headers[header] == ("Bearer " + KEY if header == "Authorization" else KEY)
    if provider == "claude":
        assert headers["anthropic-version"] == "2023-06-01"


def test_desktop_ollama_api_base_and_empty_keyless_list(api, monkeypatch):
    monkeypatch.delenv("JJOKGYEOL_DECISION_BASE_URLS")
    raw = {"provider": "ollama", "base_url": api.base + "/api/"}
    assert_code("address", lambda: providers.list_models(raw))
    assert not api.calls
    api.respond = lambda body: (200, {}, {"models": []})
    assert providers.list_models(raw, desktop=True) == []
    (path, headers, _), = api.calls
    assert path == "/proxy/api/tags" and "Authorization" not in headers


def test_gemini_empty_page_can_omit_repeated_models_field(api):
    pages = iter(({"nextPageToken": "more"}, {}))
    api.respond = lambda _: (200, {}, next(pages))
    assert providers.list_models({"provider": "gemini", "base_url": api.base}) == []
    assert [call[0] for call in api.calls] == ["/proxy/models", "/proxy/models?pageToken=more"]


def test_claude_omits_retired_models(api):
    api.respond = lambda _: (200, {}, {"data": [{"id": "old", "lifecycle": "retired"}, {"id": "current"}]})
    assert providers.list_models({"provider": "claude", "base_url": api.base}) == ["current"]


@pytest.mark.parametrize("change,code", [
    ({"provider": "builtin"}, "invalid_connection"), ({"model": "unused"}, "invalid_connection"),
    ({"api_key": "line\nbreak"}, "invalid_connection"), ({"base_url": "http://192.0.2.10"}, "address"),
])
def test_catalog_rejects_invalid_connection_before_io(api, change, code):
    raw = {"provider": "chat", "base_url": api.base, **change}
    assert_code(code, lambda: providers.list_models(raw, desktop=True))
    assert not api.calls


@pytest.mark.parametrize("provider,query,first,second", [
    ("claude", "after_id", {"data": [{"id": "b"}], "last_id": "b &/+?", "has_more": True},
     {"data": [{"id": "b"}, {"id": "a"}], "has_more": False}),
    ("gemini", "pageToken", {"models": [{"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}],
                             "nextPageToken": "b &/+?"},
     {"models": [{"name": "models/" + id, "supportedGenerationMethods": ["generateContent"]}
                 for id in ("b", "b", "a")]}),
])
def test_pages_use_opaque_cursors_and_keep_distinct_ids_in_order(api, provider, query, first, second):
    pages = iter((first, second))
    api.respond = lambda body: (200, {}, next(pages))
    assert providers.list_models({"provider": provider, "base_url": api.base, "api_key": KEY}) == ["b", "a"]
    start, end = api.calls
    assert start[0] == urlsplit(end[0]).path == "/proxy/models"
    assert parse_qs(urlsplit(end[0]).query) == {query: ["b &/+?"]}
    assert dict(start[1]) == dict(end[1])


@pytest.mark.parametrize("provider", ["gemini", "claude"])
@pytest.mark.parametrize("extra_byte", [0, 1], ids=["at-cap", "over-cap"])
def test_paginated_ids_fit_serialized_worker_reply(api, provider, extra_byte):
    from app.operations import LIMITS

    first = '한😀"\\' if provider == "claude" else "first"
    pages = [[first, "x" * 200_000 + str(i), f"cursor{i}"] for i in range(5)]
    ids = list(dict.fromkeys(model for page in pages for model in page))
    # Include the worker's default JSON escaping, separators, envelope and newline.
    size = len((json.dumps({"models": [*ids, "", "last"]}) + os.linesep).encode())
    tail = "z" * (LIMITS.request_body_bytes - size + extra_byte)
    pages.extend(([tail, "last"], []))

    def respond(_):
        index = len(api.calls) - 1
        rows = pages[index]
        if provider == "claude":
            body = {"data": [{"id": model} for model in rows], "has_more": bool(rows)}
            if rows:
                body["last_id"] = rows[-1]
        else:
            body = {"models": [{"name": "models/" + model, "supportedGenerationMethods": ["generateContent"]}
                               for model in rows]}
            if rows:
                body["nextPageToken"] = str(index + 1)
        return 200, {}, body

    api.respond = respond
    connection = {"provider": provider, "base_url": api.base}
    if extra_byte:
        assert_code("invalid_response", lambda: providers.list_models(connection, desktop=True))
        assert len(api.calls) == len(pages) - 1
    else:
        assert providers.list_models(connection, desktop=True) == [*ids, tail, "last"]
        assert len(api.calls) == len(pages)


@pytest.mark.parametrize("provider,page", [
    ("claude", {"data": [], "has_more": True, "last_id": "same"}),
    ("gemini", {"models": [], "nextPageToken": "same"}),
])
def test_repeated_page_token_fails_without_partial_list(api, provider, page):
    api.respond = lambda body: (200, {}, page)
    assert_code("invalid_response", lambda: providers.list_models({"provider": provider, "base_url": api.base}))
    assert len(api.calls) == 2


def test_pages_share_one_total_deadline(api, monkeypatch):
    monkeypatch.setitem(providers.LIMITS, "decision_seconds", 0.30)

    def delayed_page(body):
        sleep(0.19)
        return 200, {}, {"data": [{"id": "a"}], "has_more": len(api.calls) == 1, "last_id": "a"}

    api.respond = delayed_page
    assert_code("timeout", lambda: providers.list_models({"provider": "claude", "base_url": api.base}))
    assert len(api.calls) == 2


@pytest.mark.parametrize("provider,status,page,code", [
    ("chat", 307, {}, "address"), ("chat", 401, {}, "authentication"), ("chat", 404, {}, "address"),
    ("chat", 200, {"data": {}}, "invalid_response"), ("lmstudio", 200, {"data": [{"id": 3}]}, "invalid_response"),
    ("ollama", 200, {"error": "private", "models": []}, "invalid_response"),
    ("gemini", 200, {"models": [], "nextPageToken": 42}, "invalid_response"),
    ("gemini", 200, {"models": [], "nextPageToken": False}, "invalid_response"),
    ("gemini", 200, {"models": [{"name": "models/../bad", "supportedGenerationMethods": ["generateContent"]}]},
     "invalid_response"),
    ("claude", 200, {"data": [], "has_more": True}, "invalid_response"),
    ("claude", 200, {"data": [], "has_more": True, "last_id": None}, "invalid_response"),
    ("claude", 200, {"data": [], "has_more": 0}, "invalid_response"),
    ("chat", 200, b'{"data":[],"data":[]}', "invalid_response"), ("chat", 200, b" " * 262145, "invalid_response"),
])
def test_catalog_errors_are_sanitized(api, provider, status, page, code):
    api.respond = lambda body: (status, {"Location": api.base + "/elsewhere"}, page)
    assert_code(code, lambda: providers.list_models({"provider": provider, "base_url": api.base}))
    assert len(api.calls) == 1
