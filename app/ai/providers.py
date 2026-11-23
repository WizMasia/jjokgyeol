"""Session-scoped, nonstreaming decision APIs for measured paragraph candidates."""
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from ipaddress import ip_address
import json
import os
import re
import socket
from threading import Event, Thread
from time import monotonic
from urllib.parse import quote, unquote, urlsplit

from app.ai.builtin import BuiltinDecision
from app.ai.model import LIMITS, ProviderError
from app.configuration import CONFIG_DIR, read_config, unique_fields
from app.security import LOOPBACK_HOSTS


DEFAULTS = {row["id"]: row["base_url"] for row in
            read_config(CONFIG_DIR / "decision-providers.json")["providers"]}
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
    if not isinstance(raw, dict) or raw.keys() - {"provider", "base_url", "model", "api_key"}:
        raise ProviderError("invalid_connection")
    provider = raw.get("provider")
    model = raw.get("model")
    base = raw.get("base_url")
    key = raw.get("api_key", "")
    if (not isinstance(provider, str) or provider not in DEFAULTS or not isinstance(base, str)
            or not isinstance(model, str)
            or not isinstance(key, str) or not key.isascii()
            or any(not c.isprintable() or c.isspace() for c in key)):
        raise ProviderError("invalid_connection")
    if provider == "gemini":
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]*", model):
            raise ProviderError("model")
    elif not model.strip() or not model.isprintable():
        raise ProviderError("invalid_connection")
    _address(provider, base, desktop=desktop, key=key)
    return Connection(provider, base, model, key, desktop)


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
    model: str
    api_key: str = field(repr=False)
    desktop: bool = field(repr=False)

    def _request(self, options):
        data = json.dumps(options, separators=(",", ":"))
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.provider == "gemini":
                headers["x-goog-api-key"] = self.api_key
            elif self.provider == "claude":
                headers["x-api-key"] = self.api_key
            else:
                headers["Authorization"] = "Bearer " + self.api_key
        user = {"role": "user", "content": data}
        if self.provider == "responses":
            path = "/responses"
            body = {"model": self.model, "instructions": PROMPT, "input": data, "store": False}
        elif self.provider == "gemini":
            path = f"/models/{self.model}:generateContent"
            body = {"systemInstruction": {"parts": [{"text": PROMPT}]},
                    "contents": [{"role": "user", "parts": [{"text": data}]}]}
        elif self.provider == "claude":
            headers["anthropic-version"] = "2023-06-01"
            path = "/messages"
            body = {"model": self.model, "system": PROMPT, "messages": [user], "max_tokens": 1024}
        else:
            path = "/chat/completions" if self.provider == "chat" else "/api/chat"
            body = {"model": self.model, "stream": False,
                    "messages": [{"role": "system", "content": PROMPT}, user]}
        return path, headers, json.dumps(body).encode()

    def predict(self, options, *, timeout=None):
        seconds = LIMITS["decision_seconds"]
        if timeout is not None:
            seconds = min(seconds, timeout)
        if seconds <= 0:
            raise ProviderError("timeout")
        deadline = monotonic() + seconds
        url = urlsplit(self.base_url)
        suffix, headers, body = self._request(options)
        base_path = url.path.rstrip("/")
        if self.provider == "ollama":
            base_path = base_path.removesuffix("/api")
        path = quote(base_path, safe="/!$&'()*+,;=:@%") + suffix
        status, wire = _post(url, path, headers, body, deadline,
                             local_only=_requires_loopback(url, self.desktop, self.api_key))
        if status != 200:
            raise ProviderError(_request_error_code(status, wire, self.model))
        try:
            answer = _json(_answer_text(self.provider, _json(wire.decode("utf-8"))))
            if (not isinstance(answer, dict) or answer.keys() != {"id"}
                    or not isinstance(answer["id"], str) or answer["id"] not in options):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ProviderError("invalid_response") from None
        if monotonic() >= deadline:
            raise ProviderError("timeout")
        return answer["id"]


def _post(url, path, headers, body, deadline, *, local_only):
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
            conn.request("POST", path, body, headers)
            with conn.getresponse() as response:
                reply = response.status, _read_response(response)
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


def _read_response(response):
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
    limit = LIMITS["max_response_bytes"]
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


def _request_error_code(status, wire, model):
    fallback = "address" if status == 404 else "invalid_connection"
    try:
        error = _json(wire.decode("utf-8"))["error"]
    except (ValueError, TypeError, KeyError, RecursionError):
        return fallback
    message = error
    if isinstance(error, dict):
        message = error.get("message")
        if (error.get("param") == "model"
                or error.get("code") in ("model_not_found", "unsupported_model", "model_not_supported")):
            return "model"
        if (error.get("type") == "not_found_error" and isinstance(message, str)
                and re.match(r"model:\s*\S", message.strip(), re.IGNORECASE)):
            return "model"
        if status != 404 and error.get("code") == "invalid_api_key":
            return "authentication"
        details = error.get("details")
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                if status != 404 and detail.get("reason") == "API_KEY_INVALID":
                    return "authentication"
                if (error.get("status") == "NOT_FOUND" and detail.get("resourceName") == f"models/{model}"
                        and detail.get("@type") == "type.googleapis.com/google.rpc.ResourceInfo"):
                    return "model"
    if isinstance(message, str):
        match = MODEL_ERROR_MESSAGE.match(message.strip())
        if match and match["name"] in (None, model):
            return "model"
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
