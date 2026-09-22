"""Helpers for loading the adapters and standing up mock backends.

The adapters live in hyphenated directories (`presidio-adapter/`,
`cedar-shim/`), which are not importable as packages, so they are loaded by
path. Their configuration is read from the environment at import time, so each
test module sets env vars *before* loading.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module(relative_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MockBackend:
    """A tiny HTTP server whose routes are supplied per test.

    `routes` maps a path to a callable taking the parsed request body and
    returning (status, json_body). Every call is recorded in `.calls`.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode() or "{}")
                except Exception:
                    body = {}
                outer.calls.append({"path": self.path, "body": body,
                                    "headers": dict(self.headers)})
                handler = outer.routes.get(self.path)
                if handler is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = handler(body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def serve(module, port_attr="LISTEN_PORT"):
    """Run an adapter's Handler on an ephemeral port; returns (base_url, stop)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{server.server_address[1]}", stop
