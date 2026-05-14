from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .graph import GraphRuntime, LWRCLPY_TYPE_TREE


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_default(value):
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


class Handler(BaseHTTPRequestHandler):
    runtime = GraphRuntime()

    def log_message(self, fmt: str, *args) -> None:
        if self.path == "/api/run" and args and str(args[1]) == "200":
            return
        print("[lwrclpy_web_node_editor]", fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/message-types":
            self._send_json({"types": LWRCLPY_TYPE_TREE})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "lwrclpy": {"available": self.runtime.ros.available, "error": self.runtime.ros.error}})
            return
        self._send_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/run":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = self.runtime.run(payload)
            self._send_json(result)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_static(self, path: str):
        if path == "/":
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_error(403)
            return
        if not target.exists() or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("content-type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno == 48:
            print(f"Port {args.port} is already in use. Stop the existing server or run with --port {args.port + 1}.", file=sys.stderr)
            return 1
        raise
    print(f"lwrclpy Web Node Editor: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.runtime.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
