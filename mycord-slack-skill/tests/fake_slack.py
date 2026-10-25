"""In-process fake Slack API, so the offline suite never touches the network.

Two jobs:

* prove the passthrough sends what it was told to send, as form fields;
* prove an error response is reported verbatim, with no translation layer in between.

It is a real `http.server` on a loopback port rather than a mock object because the thing
under test is precisely the *transport* — `slack_sdk`'s encoding of `params` — and a fake
client object would test nothing but itself.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs


@dataclass
class Recorded:
    """One request the fake server received."""

    method: str
    path: str
    form: dict[str, str]
    headers: dict[str, str]


@dataclass
class FakeSlack:
    """A loopback Slack API. `responses` maps a method name to its response envelope."""

    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/api/"

    def default_response(self, method: str) -> dict[str, Any]:
        if method in self.responses:
            return self.responses[method]
        # Deliberately empty beyond `ok`: a body invented by the fake would leak into
        # assertions about what the CLI prints.
        return {"ok": True}

    def start(self) -> FakeSlack:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler schema
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                form = {k: v[0] for k, v in parse_qs(raw).items()}
                method = self.path.rstrip("/").rsplit("/", 1)[-1]
                fake.requests.append(
                    Recorded(
                        method=method,
                        path=self.path,
                        form=form,
                        headers={k.lower(): v for k, v in self.headers.items()},
                    )
                )
                body = json.dumps(fake.default_response(method)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler schema
                self.do_POST()

            def log_message(self, *args: Any) -> None:
                return  # keep the test output clean

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def last(self) -> Recorded:
        assert self.requests, "no request reached the fake server"
        return self.requests[-1]

    def __enter__(self) -> FakeSlack:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
