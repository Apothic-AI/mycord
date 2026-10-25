"""The passthrough: one Slack API method per call, straight through `slack_sdk`.

This is the whole point of the component. An `xoxc-` token plus the `d` cookie operates
exactly like the owning human's browser session and carries no OAuth scope limits, so
any method that account may call is callable here. Curating a verb list would re-impose
the scope limits the approach exists to avoid.

Two deliberate non-features:

* **No retries.** Slack's own rate-limit response (`ratelimited`) is surfaced verbatim so
  the caller decides; a hidden retry loop would make a long batch look slow instead of
  rate-limited.
* **No error translation.** `invalid_auth`, `not_in_channel`, `admin_unauthorized` and
  friends come back exactly as Slack sent them, with the HTTP status and any
  `response_metadata.messages`, because guessing what an error "really means" is how a
  role limit gets mistaken for a token limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import api_base
from .session import Session


@dataclass
class ApiResult:
    """A Slack API response, normalised for output but never reinterpreted."""

    ok: bool
    method: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    status_code: int | None = None
    detail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "method": self.method}
        if self.error is not None:
            out["error"] = self.error
        if self.status_code is not None:
            out["status_code"] = self.status_code
        if self.detail:
            out["detail"] = self.detail
        out["data"] = self.data
        return out


def client_for(session: Session, *, base_url: str | None = None) -> WebClient:
    """A WebClient carrying both halves of the session.

    The `d` cookie rides in a header because Slack accepts the client token as the
    request's token and the cookie as the *session* proof; passing only the token gets
    `invalid_auth`. `base_url` stays unset in normal use so slack_sdk uses
    `https://slack.com/api/`, which is what routes an `xoxc-` token to its own workspace.
    """
    kwargs: dict[str, Any] = {
        "token": session.token,
        "headers": {"Cookie": f"d={session.cookie}"},
    }
    base = base_url or api_base()
    if base:
        # Only pass base_url when set. slack_sdk's own default (https://slack.com/api/)
        # is what routes an xoxc token to its workspace, and it is not a str|None param.
        kwargs["base_url"] = base
    return WebClient(**kwargs)


def _body_dict(response: Any) -> dict[str, Any]:
    """The response body as a dict, tolerating slack_sdk's `dict | bytes` union.

    `SlackResponse.data` is `dict[str, Any] | bytes` — bytes only when a method returned a
    binary payload, which none of the JSON methods do. Anything unexpected becomes an
    empty dict so a caller sees Slack's `error` field rather than a type error.
    """
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return dict(data)
    return {}


def _detail_from(response: Any) -> list[str]:
    """Slack's own explanation, when it gives one.

    `response_metadata.messages` is where Slack says *why* (e.g. "missing required field:
    query", or the org-permission reason behind `admin_unauthorized`). It is often more
    useful than the error code and almost always ignored.
    """
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        messages = (data.get("response_metadata") or {}).get("messages")
        if isinstance(messages, list):
            return [str(m) for m in messages]
    return []


def _encode(payload: dict[str, Any]) -> dict[str, Any]:
    """Serialise nested values to JSON before slack_sdk turns everything into a string.

    `api_call(params=...)` form-encodes each value, and for a non-string it falls back to
    Python's `str()` — which renders a dict with single quotes and `True` instead of
    `true`. Slack parses that as neither: a `blocks` value passed as a real structure
    comes back `invalid_blocks_format`, while the same structure JSON-encoded is accepted
    (verified against the live API). Scalars are left alone, since `str()` is correct for
    them and Slack's form encoding expects strings.
    """
    encoded: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            encoded[key] = json.dumps(value)
        else:
            encoded[key] = value
    return encoded


def call(
    session: Session,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    http_verb: str = "POST",
    base_url: str | None = None,
    client: WebClient | None = None,
) -> ApiResult:
    """Invoke `method` with `payload` and return the response without interpretation.

    Slack's web API is form-encoded, so the payload is sent as `params`, not as a JSON
    body. That matters: `api_call(..., json={...})` sends JSON, which some methods accept
    and others silently ignore or reject with `invalid_arguments` (verified against
    `search.messages`). Passing every key as a form field works for both simple scalars
    and nested objects, provided the nested ones are JSON-encoded — see `_encode`.

    Errors are returned, not raised: a caller scripting a batch wants the error code, not
    a traceback, and `ApiResult.ok` is already the branch it needs.
    """
    web = client or client_for(session, base_url=base_url)
    try:
        response = web.api_call(method, http_verb=http_verb, params=_encode(payload or {}))
    except SlackApiError as exc:
        body = exc.response
        data = _body_dict(body)
        return ApiResult(
            ok=False,
            method=method,
            data=data,
            error=str(data.get("error") or "unknown_error"),
            status_code=getattr(body, "status_code", None),
            detail=_detail_from(body),
        )
    data = _body_dict(response)
    ok = bool(data.get("ok"))
    return ApiResult(
        ok=ok,
        method=method,
        data=data,
        error=None if ok else str(data.get("error") or "unknown_error"),
        status_code=getattr(response, "status_code", None),
        detail=[] if ok else _detail_from(response),
    )


def parse_payload(raw_payload: str) -> dict[str, Any]:
    """Parse a command-line payload into the method's arguments.

    Split out from `call_json` so the CLI can reject a malformed payload *before* it
    touches the session: a JSON typo is a usage error, and reporting it as "no session"
    sends the caller looking in the wrong place.
    """
    if raw_payload.strip() in {"", "-"}:
        return {}
    try:
        parsed = json.loads(raw_payload)
    except ValueError as exc:
        raise ValueError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "payload must be a JSON object of API arguments, e.g. "
            '\'{"channel": "C0123", "text": "hi"}\''
        )
    return parsed


def call_json(session: Session, method: str, raw_payload: str, **kwargs: Any) -> ApiResult:
    """`call`, taking the payload as a JSON object string from the command line."""
    return call(session, method, parse_payload(raw_payload), **kwargs)


def verify(session: Session, *, base_url: str | None = None) -> ApiResult:
    """`auth.test` — the cheapest proof that both halves of the session still work."""
    return call(session, "auth.test", {}, base_url=base_url)
