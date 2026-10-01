"""Bounded Product-only error projection; never retain raw error bodies."""

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

BODY_LIMIT = 16384


def sanitize_message(value: str, secret: str) -> str:
    if not isinstance(value, str):
        raise ValueError("message is not text")
    # Decode JSON first, redact before truncating (including URL-encoded keys).
    for token in (secret, quote(secret, safe="")):
        if token:
            value = value.replace(token, "[REDACTED]")
    value = re.sub(r"(?i)(?:authorization|cookie|api[-_ ]?key|bearer)\s*[:= ]+[^\s,;]+", "[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"https?://[^\s<>]+", "[URL_REDACTED]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", "[EMAIL_REDACTED]", value)
    value = re.sub(r"[A-Za-z0-9_-]{32,}", "[IDENTIFIER_REDACTED]", value)
    if any(ord(c) < 32 and c not in "\r\n\t" for c in value):
        raise ValueError("control characters")
    return " ".join(value.split())[:1024]


def http_failure(exc: BaseException, *, secret: str) -> dict:
    chain = []
    current = exc
    http = None
    for _ in range(8):
        chain.append(type(current).__name__)
        if isinstance(current, urllib.error.HTTPError):
            http = current
            break
        if current.__cause__ is None:
            break
        current = current.__cause__
    result: dict[str, Any] = dict(http_status=None, response_content_type=None, request_id=None,
                  provider_error_code=None, provider_error_type=None,
                  sanitized_error_message=None, response_body_kind="OTHER",
                  body_truncated=False, transport_exception_type=chain)
    if http is None:
        return result
    result["http_status"] = http.code
    # Failure of any projection must not erase the original status.
    try:
        headers: Any = http.headers or {}
        content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", content_type) and secret not in content_type:
            result["response_content_type"] = content_type
        request_id = headers.get("x-request-id")
        if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) and secret not in request_id:
            result["request_id"] = request_id
    except Exception:
        result["header_projection_status"] = "SUPPRESSED"
    try:
        body = http.read(BODY_LIMIT + 1)  # Exactly one read, no logging elsewhere.
        result["body_truncated"] = len(body) > BODY_LIMIT
        if not body:
            result["response_body_kind"] = "EMPTY"
            return result
        text = body[:BODY_LIMIT].decode("utf-8", errors="strict").strip()
        if text.startswith("<"):
            result["response_body_kind"] = "HTML"
            result["message_projection_status"] = "HTML_BODY_OMITTED"
            return result
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            result["response_body_kind"] = "TEXT"
            # Arbitrary plaintext may echo private content. Only retain a tiny
            # generic error vocabulary; HTML/plain data never proves provenance.
            if re.fullmatch(r"(?i)(?:404\s+)?(?:page\s+)?not found[.!]?", text):
                result["sanitized_error_message"] = sanitize_message(text, secret)
            else:
                result["message_projection_status"] = "UNSTRUCTURED_BODY_OMITTED"
            return result
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if not isinstance(error, dict):
            result["response_body_kind"] = "OTHER"
            result["message_projection_status"] = "NO_ERROR_OBJECT"
            return result
        result["response_body_kind"] = "JSON_ERROR"
        for field in ("code", "type"):
            value = error.get(field)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", value) and secret not in value:
                result["provider_error_" + field] = value
        if not result["body_truncated"] and isinstance(error.get("message"), str):
            result["sanitized_error_message"] = sanitize_message(error["message"], secret)
        else:
            result["message_projection_status"] = "MISSING_OR_TRUNCATED"
    except Exception:
        result["sanitized_error_message"] = None
        result["message_projection_status"] = "SANITIZATION_OR_READ_FAILED"
    return result


class ProductDiagnosticTransport:
    """Reuse frozen transport; observe only allowed metadata on successful open."""

    def __init__(self):
        from ecomsre.model.gateway import RejectRedirectHandler, StdlibOpenAICompatibleTransport
        owner = self
        opener = urllib.request.build_opener(RejectRedirectHandler())

        class CaptureOpen:
            def open(self, request, **kwargs):
                response = opener.open(request, **kwargs)
                owner.last_response = {"http_status": response.status,
                                       "response_content_type": None, "request_id": None}
                try:
                    secret = request.get_header("Authorization", "").removeprefix("Bearer ")
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    request_id = response.headers.get("x-request-id")
                    if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", content_type) and secret not in content_type:
                        owner.last_response["response_content_type"] = content_type
                    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) and secret not in request_id:
                        owner.last_response["request_id"] = request_id
                except Exception:
                    owner.last_response["header_projection_status"] = "SUPPRESSED"
                return response

        self.last_response: dict[str, Any] = {}
        self.delegate = StdlibOpenAICompatibleTransport(opener=CaptureOpen())

    def post_json(self, **kwargs):
        self.last_response = {}
        return self.delegate.post_json(**kwargs)
