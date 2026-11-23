"""Session-scoped model discovery and nonstreaming JSON generation."""
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from ipaddress import ip_address
import json
from math import isfinite
import os
import re
import socket
from threading import Event, Thread
from time import monotonic
from urllib.parse import quote, unquote, urlencode, urlsplit

from app.ai.builtin import BuiltinDecision
from app.ai.model import LIMITS, ProviderError, SPACING_PROMPT
from app.configuration import CONFIG_DIR, read_config, unique_fields
from app.operations import LIMITS as OPERATIONS
from app.security import LOOPBACK_HOSTS


_CONFIG = read_config(CONFIG_DIR / "decision-providers.json")
DEFAULTS = {row["id"]: row["base_url"] for row in _CONFIG["providers"]}
STYLE_LIMITS = _CONFIG["style_suggestion"]
for key in ("max_tokens", "max_response_bytes"):
    if type(STYLE_LIMITS.get(key)) is not int or STYLE_LIMITS[key] <= 0:
        raise ValueError(f"decision-providers.json: invalid style_suggestion.{key}")
# 모델 카탈로그 페이지네이션 상한 검증 / Validate catalog pagination bounds from decision.json.
for key in ("max_catalog_pages", "max_catalog_cursor_chars"):
    if type(LIMITS.get(key)) is not int or LIMITS[key] <= 0:
        raise ValueError(f"decision.json: invalid {key}")
PROMPT = ('Choose one of the measured paragraph layouts. Balance line lengths including the last '
          'line, avoid isolated glyphs and split words, and prefer smooth spacing. Widths use sp '
          'and spacing ratios use 10000 as one unit. Return only a JSON object {"id":"..."} '
          'containing the exact ID of your chosen layout.')
# Use the same line fields a trained selector will see during real rendering.
_EXAMPLE_LINE = {"end_glyph": 4, "available_sp": 500, "start_sp": 0, "tracking_per_10000": 0,
                 "space_per_10000": 10000, "tracking_delta": 0, "space_deviation": 0,
                 "tracking_change": 0, "space_change": 0, "split_word": False,
                 "first_char_class": "Lo", "last_char_class": "Lo"}
EXAMPLE = {"a": [dict(_EXAMPLE_LINE, remaining_sp=100)], "b": [dict(_EXAMPLE_LINE, remaining_sp=200)]}


def _loopback(host):
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _requires_loopback(url, desktop, key):
    return url.scheme == "http" and (desktop or bool(key))


def _address(provider, base, *, desktop, key):
    try:
        url = urlsplit(base)
        port = url.port
    except ValueError:
        raise ProviderError("address") from None
    if (any(c.isspace() or not c.isprintable() for c in base)
            or any(c in base for c in "?#\\") or url.scheme not in ("https", "http")
            or not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[\w.-]+)(?::[0-9]+)?", url.netloc)
            or port == 0 or url.path.startswith("//")):
        raise ProviderError("address")
    for part in url.path.split("/"):
        decoded = unquote(part)
        if (decoded in (".", "..") or any(c in decoded for c in "/\\%?#")
                or not decoded.isprintable()):
            raise ProviderError("address")
    if _requires_loopback(url, desktop, key) and url.hostname not in LOOPBACK_HOSTS and not _loopback(url.hostname):
        raise ProviderError("address")
    if not desktop:
        allowed = os.environ.get("JJOKGYEOL_DECISION_BASE_URLS", "").split(",")
        official = url.scheme == "https" and base.rstrip("/") == DEFAULTS[provider]
        if not official and base not in (entry.strip() for entry in allowed):
            raise ProviderError("address")


def parse_connection(raw, *, desktop=False):
    if raw == {"provider": "builtin"}:
        return BuiltinDecision.installed()
    return _external_connection(raw, desktop=desktop, catalog=False)


def _external_connection(raw, *, desktop, catalog):
    fields = {"provider", "base_url", "api_key"}
    if not catalog:
        fields.add("model")
    if not isinstance(raw, dict) or raw.keys() - fields:
        raise ProviderError("invalid_connection")
    provider = raw.get("provider")
    base = raw.get("base_url")
    key = raw.get("api_key", "")
    if (not isinstance(provider, str) or provider not in DEFAULTS or not isinstance(base, str)
            or not isinstance(key, str) or not key.isascii()
            or any(not c.isprintable() or c.isspace() for c in key)):
        raise ProviderError("invalid_connection")
    model = None if catalog else _model_id(provider, raw.get("model"))
    _address(provider, base, desktop=desktop, key=key)
    return Connection(provider, base, model, key, desktop)


def _model_id(provider, model):
    if not isinstance(model, str):
        raise ProviderError("invalid_connection")
    if provider == "gemini":
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]*", model):
            raise ProviderError("model")
    elif not model.strip() or not model.isprintable():
        raise ProviderError("invalid_connection")
    return model


def list_models(raw, *, desktop=False) -> list[str]:
    """List selectable model IDs without inference, within one request deadline."""
    conn = _external_connection(raw, desktop=desktop, catalog=True)
    deadline = monotonic() + LIMITS["decision_seconds"]
    suffix = "/api/tags" if conn.provider == "ollama" else "/models"
    path = suffix
    models = {}
    # Both workers print the default ASCII-escaped JSON envelope followed by a newline.
    reply_bytes = len(json.dumps({"models": []}) + os.linesep)
    seen_cursors = set()
    pages_fetched = 0
    max_pages = LIMITS["max_catalog_pages"]
    while True:
        pages_fetched += 1
        if pages_fetched > max_pages:
            raise ProviderError("invalid_response")
        page = conn._request_json("GET", path, None, deadline, max_response_bytes=LIMITS["max_response_bytes"])
        try:
            ids, cursor = _catalog_page(conn.provider, page)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("invalid_response") from None
        for model in ids:
            if model not in models:
                reply_bytes += len(json.dumps(model)) + (len(", ") if models else 0)
                models[model] = None
        if reply_bytes > OPERATIONS.request_body_bytes:
            raise ProviderError("invalid_response")
        if monotonic() >= deadline:
            raise ProviderError("timeout")
        if cursor is None:
            return list(models)
        if cursor in seen_cursors:
            raise ProviderError("invalid_response")
        seen_cursors.add(cursor)
        param = "after_id" if conn.provider == "claude" else "pageToken"
        path = suffix + "?" + urlencode({param: cursor})


def _catalog_page(provider, page):
    if not isinstance(page, dict) or page.get("error") is not None:
        raise ValueError
    named = provider in ("gemini", "ollama")
    rows_field, id_field = ("models", "name") if named else ("data", "id")
    rows = page.get(rows_field, [] if provider == "gemini" else None)
    if not isinstance(rows, list):
        raise ValueError
    ids = []
    for row in rows:
        if provider == "claude" and row.get("lifecycle") == "retired":
            continue
        if provider == "gemini":
            methods = row.get("supportedGenerationMethods", [])
            if not isinstance(methods, list):
                raise ValueError
            if "generateContent" not in methods:
                continue
        ids.append(_model_id(provider, row[id_field]))
    cursor = None
    if provider == "gemini":
        cursor = page.get("nextPageToken")
        if cursor == "":
            cursor = None
    if provider == "claude":
        more = page.get("has_more", False)
        if not isinstance(more, bool):
            raise ValueError
        if more:
            cursor = page["last_id"]
            if cursor is None:
                raise ValueError
    if cursor is not None and (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > LIMITS["max_catalog_cursor_chars"]
        or not cursor.isprintable()
    ):
        raise ValueError
    return ids, cursor


def check_connection(raw, *, desktop=False):
    parse_connection(raw, desktop=desktop).predict(EXAMPLE)


def parse_render_connection(raw, *, desktop=False):
    try:
        return parse_connection(raw, desktop=desktop), None
    except ProviderError as error:
        if error.code not in ("model", "address", "builtin_unavailable"):
            raise
        return None, error.code


@dataclass(frozen=True)
class Connection:
    provider: str
    base_url: str
    model: str | None
    api_key: str = field(repr=False)
    desktop: bool = field(repr=False)

    def _request_json(self, method, suffix, body, deadline, *, max_response_bytes):
        headers = {} if body is None else {"Content-Type": "application/json"}
        if self.provider == "claude":
            headers["anthropic-version"] = "2023-06-01"
        if self.api_key:
            if self.provider == "gemini":
                headers["x-goog-api-key"] = self.api_key
            elif self.provider == "claude":
                headers["x-api-key"] = self.api_key
            else:
                headers["Authorization"] = "Bearer " + self.api_key
        url = urlsplit(self.base_url)
        base_path = url.path.rstrip("/")
        if self.provider == "ollama":
            base_path = base_path.removesuffix("/api")
        path = quote(base_path, safe="/!$&'()*+,;=:@%") + suffix
        status, wire = _http_request(method, url, path, headers, body, deadline,
                                     max_response_bytes=max_response_bytes,
                                     local_only=_requires_loopback(url, self.desktop, self.api_key))
        if status != 200:
            raise _request_error(status, wire, self.model)
        try:
            return _json(wire.decode("utf-8"))
        except (ValueError, RecursionError):
            raise ProviderError("invalid_response") from None

    def _generation_request(self, prompt, data, max_tokens, schema, structured_chat):
        if schema is not None and not structured_chat:
            prompt += "\n다음 스키마에 맞는 JSON 객체만 반환하세요.\n" + json.dumps(schema, ensure_ascii=False)
        user = {"role": "user", "content": data}
        if self.provider == "responses":
            path = "/responses"
            body = {"model": self.model, "instructions": prompt, "input": data, "store": False}
            if max_tokens is not None:
                body["max_output_tokens"] = max_tokens
        elif self.provider == "gemini":
            path = f"/models/{self.model}:generateContent"
            body = {"systemInstruction": {"parts": [{"text": prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": data}]}]}
            if max_tokens is not None:
                body["generationConfig"] = {"maxOutputTokens": max_tokens}
        elif self.provider == "claude":
            path = "/messages"
            body = {"model": self.model, "system": prompt, "messages": [user],
                    "max_tokens": 1024 if max_tokens is None else max_tokens}
        else:
            path = "/api/chat" if self.provider == "ollama" else "/chat/completions"
            body = {"model": self.model, "stream": False,
                    "messages": [{"role": "system", "content": prompt}, user]}
            if structured_chat:
                body["temperature"] = 0
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "style_edits", "schema": schema}}
            if max_tokens is not None:
                if self.provider == "ollama":
                    body["options"] = {"num_predict": max_tokens}
                else:
                    body["max_tokens" if self.provider == "lmstudio" else "max_completion_tokens"] = max_tokens
        return path, body

    def predict(self, options, *, timeout=None):
        return self._predict(options, PROMPT, timeout)

    def predict_spacing(self, options, *, timeout):
        return self._predict(options, SPACING_PROMPT, timeout)

    def _predict(self, options, prompt, timeout):
        seconds = LIMITS["decision_seconds"]
        if timeout is not None:
            seconds = min(seconds, timeout)
        answer = self._generate_json(prompt, options, timeout=seconds, max_tokens=None, ensure_ascii=True,
                                     max_response_bytes=LIMITS["max_response_bytes"])
        if (not isinstance(answer, dict) or answer.keys() != {"id"}
                or not isinstance(answer["id"], str) or answer["id"] not in options):
            raise ProviderError("invalid_response")
        return answer["id"]

    def generate_json(self, prompt, payload, *, timeout, schema=None):
        """Generate a JSON style suggestion using the configured output budgets."""
        return self._generate_json(prompt, payload, timeout=timeout, schema=schema, **STYLE_LIMITS)

    def _generate_json(self, prompt, payload, *, timeout, max_tokens, max_response_bytes, schema=None,
                       ensure_ascii=False):
        if not isfinite(timeout) or timeout <= 0:
            raise ProviderError("timeout")
        deadline = monotonic() + timeout
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=ensure_ascii)

        def request(structured_chat):
            path, body = self._generation_request(prompt, data, max_tokens, schema, structured_chat)
            wire = json.dumps(body, ensure_ascii=ensure_ascii).encode("utf-8", "backslashreplace")
            return self._request_json("POST", path, wire, deadline, max_response_bytes=max_response_bytes)

        structured_chat = schema is not None and self.provider in ("chat", "lmstudio")
        try:
            reply = request(structured_chat)
        except _UnsupportedChatFormat:
            if not structured_chat or self.provider != "chat":
                raise
            reply = request(False)
        try:
            answer = _json(_answer_text(self.provider, reply))
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ProviderError("invalid_response") from None
        if monotonic() >= deadline:
            raise ProviderError("timeout")
        return answer


def _http_request(method, url, path, headers, body, deadline, *, local_only, max_response_bytes):
    # http.client never follows redirects or consults proxy variables. A socket
    # timeout alone cannot bound DNS resolution or a continuously trickled reply.
    transport = HTTPSConnection if url.scheme == "https" else HTTPConnection
    conn = transport(url.hostname, url.port, timeout=max(0, deadline - monotonic()))
    conn.auto_open = False
    result = Future()
    cancelled = Event()
    active_socket = None

    def exchange():
        nonlocal active_socket
        try:
            conn.connect()
            active_socket = conn.sock
            if cancelled.is_set() or monotonic() >= deadline:
                raise ProviderError("timeout")
            if local_only and not _loopback(active_socket.getpeername()[0]):
                raise ProviderError("address")
            conn.request(method, path, body, headers)
            with conn.getresponse() as response:
                reply = response.status, _read_response(response, max_response_bytes)
            result.set_result(reply)
        except ProviderError as error:
            result.set_exception(error)
        except TimeoutError:
            result.set_exception(ProviderError("timeout"))
        except HTTPException:
            result.set_exception(ProviderError("invalid_response"))
        except (OSError, ValueError):
            result.set_exception(ProviderError("unavailable"))
        finally:
            conn.close()

    Thread(target=exchange, daemon=True).start()
    try:
        return result.result(timeout=max(0, deadline - monotonic()))
    except TimeoutError:
        raise ProviderError("timeout") from None
    finally:
        cancelled.set()
        if not result.done() and active_socket is not None:
            with suppress(OSError):
                active_socket.shutdown(socket.SHUT_RDWR)


def _read_response(response, limit):
    status = response.status
    if 300 <= status < 400:
        raise ProviderError("address")
    if status in (401, 403):
        raise ProviderError("authentication")
    if status == 429:
        raise ProviderError("rate_limit")
    if status in (408, 504):
        raise ProviderError("timeout")
    if status not in (200, 400, 404, 422):
        raise ProviderError("unavailable")
    unreadable = "address" if status == 404 else "invalid_response"
    if response.length is not None and response.length > limit:
        raise ProviderError(unreadable)
    wire = response.read(limit + 1)
    if len(wire) > limit or response.length:
        raise ProviderError(unreadable)
    return wire


# Some APIs identify the model only in a text field. Match the model as the
# subject so a missing route containing the word "model" stays an address error.
MODEL_ERROR_MESSAGE = re.compile(
    r"""(?:(?:the |this )?model(?: ['"`][^'"`\r\n]+['"`]| (?P<name>(?!is |was )\S+))?|models/\S+) """
    r"(?:(?:is|was) )?(?:not found|not supported|unsupported|does not exist|does not support)\b"
    r"|(?:unknown|unsupported) model\b", re.IGNORECASE)


class _UnsupportedChatFormat(ProviderError):
    """The server rejected structured generation before accepting the completion."""


def _request_error(status, wire, model):
    fallback = ProviderError("address" if status == 404 else "invalid_connection")
    try:
        error = _json(wire.decode("utf-8"))["error"]
    except (ValueError, TypeError, KeyError, RecursionError):
        return fallback
    message = error
    if isinstance(error, dict):
        message = error.get("message")
        if (error.get("param") == "model"
                or error.get("code") in ("model_not_found", "unsupported_model", "model_not_supported")):
            return ProviderError("model")
        if (error.get("type") == "not_found_error" and isinstance(message, str)
                and re.match(r"model:\s*\S", message.strip(), re.IGNORECASE)):
            return ProviderError("model")
        if status != 404 and error.get("code") == "invalid_api_key":
            return ProviderError("authentication")
        if (status in (400, 422) and error.get("param") in ("response_format", "temperature")
                and (error.get("code") in ("unsupported_parameter", "unsupported_value")
                     or isinstance(message, str) and re.search(r"unsupported|not supported", message, re.I))):
            return _UnsupportedChatFormat("invalid_connection")
        details = error.get("details")
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                if status != 404 and detail.get("reason") == "API_KEY_INVALID":
                    return ProviderError("authentication")
                if (error.get("status") == "NOT_FOUND" and detail.get("resourceName") == f"models/{model}"
                        and detail.get("@type") == "type.googleapis.com/google.rpc.ResourceInfo"):
                    return ProviderError("model")
    if isinstance(message, str):
        match = MODEL_ERROR_MESSAGE.match(message.strip())
        if match and match["name"] in (None, model):
            return ProviderError("model")
    return fallback


def _invalid_constant(_):
    raise ValueError


def _json(text):
    if not isinstance(text, str):
        raise ValueError
    return json.loads(text, object_pairs_hook=unique_fields, parse_constant=_invalid_constant)


def _answer_text(provider, reply):
    if not isinstance(reply, dict) or reply.get("error") is not None:
        raise ValueError
    if provider == "responses":
        if reply["status"] != "completed" or reply.get("incomplete_details") is not None:
            raise ValueError
        final = None
        for item in reply["output"]:
            if final is not None:
                raise ValueError
            if item["type"] == "reasoning" and item.get("status", "completed") == "completed":
                continue
            if item["type"] != "message" or item["role"] != "assistant" or item["status"] != "completed":
                raise ValueError
            text = _typed_text(item["content"], "output_text")
            phase = item.get("phase")
            if phase in (None, "final_answer"):
                final = text
            elif phase != "commentary":
                raise ValueError
        return final
    if provider == "gemini":
        candidate, = reply["candidates"]
        content = candidate["content"]
        if (candidate["finishReason"] != "STOP" or content.get("role", "model") != "model"
                or reply.get("promptFeedback", {}).get("blockReason")
                or any(rating.get("blocked") for rating in candidate.get("safetyRatings", []))):
            raise ValueError
        texts = []
        for part in content["parts"]:
            if part.keys() - {"text", "thought", "thoughtSignature", "partMetadata"}:
                raise ValueError
            text = part["text"]
            if not part.get("thought"):
                texts.append(text)
        return "".join(texts)
    if provider == "claude":
        if reply["type"] != "message" or reply["role"] != "assistant" or reply["stop_reason"] != "end_turn":
            raise ValueError
        return _typed_text(reply["content"], "text", ("thinking", "redacted_thinking"))
    if provider == "ollama":
        if reply["done"] is not True or reply["done_reason"] != "stop":
            raise ValueError
        message = reply["message"]
    else:
        choice, = reply["choices"]
        if choice["finish_reason"] != "stop":
            raise ValueError
        message = choice["message"]
    if (message.get("role", "assistant") != "assistant" or message.get("tool_calls")
            or message.get("function_call") or message.get("refusal")):
        raise ValueError
    return message["content"]


def _typed_text(parts, text_type, reasoning=()):
    text = []
    for part in parts:
        if part["type"] in reasoning:
            continue
        if part["type"] != text_type:
            raise ValueError
        text.append(part["text"])
    return "".join(text)
