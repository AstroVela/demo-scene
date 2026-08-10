#!/usr/bin/env python3
"""Loopback reverse proxy for the Vane procurement audit demo.

The demo's runtime.yml requires ai.base_url / ai.health_url to be loopback
HTTP URLs (http://127.0.0.1:<port>). This proxy listens on loopback and
forwards every request to an upstream HTTPS endpoint, so the config
validation passes while still reaching a remote OpenAI-compatible model.

Usage:
    python scripts/reverse_proxy.py
    # then in runtime.yml set:
    #   ai.base_url:   http://127.0.0.1:8000/api/models
    #   ai.health_url: http://127.0.0.1:8000/api/health

Edit UPSTREAM below to match your remote service.
"""

from __future__ import annotations

import http.server
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Configuration — edit these to match your remote model service.
# ---------------------------------------------------------------------------
UPSTREAM = "https://ai-model.chint.com"          # remote HTTPS base (no trailing slash)
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8000
UPSTREAM_TIMEOUT = 300                            # seconds; VLM vision calls can be slow

# Hop-by-hop headers that must not be forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ core
    def _forward(self) -> None:
        upstream_url = UPSTREAM + self.path

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        out_headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() in _HOP_BY_HOP:
                continue
            out_headers[key] = value
        # The upstream TLS endpoint needs its own Host header / SNI.
        out_headers["Host"] = urlsplit(UPSTREAM).hostname

        req = urllib.request.Request(
            upstream_url, data=body, headers=out_headers, method=self.command
        )
        ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=UPSTREAM_TIMEOUT) as resp:
                payload = resp.read()
                self._write_response(resp.status, resp.headers, payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self._write_response(exc.code, exc.headers, payload)
        except Exception as exc:  # noqa: BLE001 — surface upstream errors as 502
            payload = f"proxy upstream error: {exc}".encode()
            self._write_response(502, {}, payload)

    def _write_response(self, status: int, headers, payload: bytes) -> None:
        self.send_response(status)
        if hasattr(headers, "items"):
            for key, value in headers.items():
                if key.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    # ------------------------------------------------------------ HTTP verbs
    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_DELETE = _forward
    do_PATCH = _forward
    do_HEAD = _forward

    # ------------------------------------------------------------ logging
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[proxy] {self.command} {self.path} -> {fmt % args}\n")


def main() -> None:
    server = http.server.ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), _ProxyHandler)
    print(
        f"[proxy] listening on http://{LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
