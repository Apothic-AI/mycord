"""Sending, behind a dry-run default and a hard confirm gate.

Two separate safety rules, because they stop different mistakes:

* **Dry run is the default.** Calling ``send`` without ``--confirm`` prints the exact JSON-RPC
  request that *would* be sent and exits 0. An agent exploring the surface cannot reach a
  human by accident, and a human reviewing a plan sees the literal payload.
* **``--confirm`` is required for the real call.** It is a separate flag rather than a
  ``--dry-run`` opt-out so that the unsafe path is the one you have to type.

Neither is a policy check — the repo's rule is that nothing reaches another person without
the operator's say-so, and that lives at the caller. This module only makes the safe default
the effortless one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from . import rpc

#: ``send`` accepts recipients positionally in the CLI but as the ``recipient`` list over
#: JSON-RPC (`ns.<String>getList("recipient")` in SendCommand).
MAX_MESSAGE_CHARS: Final = 2000


@dataclass
class SendPlan:
    """A fully-resolved send, printable before it happens and replayable afterwards."""

    recipients: list[str] = field(default_factory=list)
    message: str | None = None
    attachments: list[str] = field(default_factory=list)
    group_ids: list[str] = field(default_factory=list)
    quote_timestamp: int | None = None
    edit_timestamp: int | None = None

    def params(self) -> dict[str, Any]:
        """Build the JSON-RPC params.

        Field names match ``SendCommand``'s namespace keys exactly; ``JsonRpcNamespace`` maps
        camelCase to dash-separated names, so both spellings work, and these are the ones the
        live daemon accepted when probed.
        """
        params: dict[str, Any] = {}
        if self.recipients:
            params["recipient"] = list(self.recipients)
        if self.group_ids:
            params["groupId"] = list(self.group_ids)
        if self.message is not None:
            params["message"] = self.message
        if self.attachments:
            params["attachment"] = list(self.attachments)
        if self.quote_timestamp is not None:
            params["quoteTimestamp"] = self.quote_timestamp
        if self.edit_timestamp is not None:
            params["editTimestamp"] = self.edit_timestamp
        return params

    def problems(self) -> list[str]:
        """Reasons this plan must not be sent. Empty means it is well-formed."""
        issues: list[str] = []
        if not self.recipients and not self.group_ids:
            issues.append("no recipient and no group: send would be refused by the daemon")
        if self.message is None and not self.attachments:
            issues.append("nothing to send: no message and no attachment")
        if self.message is not None and len(self.message) > MAX_MESSAGE_CHARS:
            issues.append(
                f"message is {len(self.message)} chars, over the {MAX_MESSAGE_CHARS} limit"
            )
        return issues


def preview(plan: SendPlan) -> dict[str, Any]:
    """The exact request body, for dry-run output and for logs."""
    return {"jsonrpc": "2.0", "method": rpc.METHOD_SEND, "params": plan.params()}


def send(plan: SendPlan, client: rpc.RpcClient, *, dry_run: bool = True) -> dict[str, Any]:
    """Send, or say what would be sent.

    Refuses an ill-formed plan locally (no recipient, nothing to send) instead of letting the
    daemon reject it, so the dry-run output is a real preview of something that would work.
    """
    issues = plan.problems()
    if issues:
        raise ValueError("; ".join(issues))
    payload = preview(plan)
    if dry_run:
        return {"dry_run": True, "request": payload}
    result = client.call(rpc.METHOD_SEND, plan.params(), timeout=120.0)
    return {"dry_run": False, "request": payload, "result": result}


def result_summary(result: Any) -> str:
    """Render a send result without trusting its shape.

    ``send`` answers with a timestamp object on success and raises on failure; a naive caller
    that assumed success would report a sent message that never left. This keeps the daemon's
    own words in the output.
    """
    if isinstance(result, dict):
        timestamp = result.get("timestamp")
        if timestamp is not None:
            return f"sent (timestamp {timestamp})"
        return "sent: " + ", ".join(f"{key}={value}" for key, value in result.items())
    return f"sent: {result!r}"
