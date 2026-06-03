from __future__ import annotations

import argparse
import atexit
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .graph import GraphRuntime, LWRCLPY_TYPE_TREE


STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_DIR = Path.cwd()
WORKER_SCRIPT = Path(__file__).resolve().parent / "node_worker.py"
WORKER_DIR = PROJECT_DIR / ".node_workers"


def _json_default(value):
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def cleanup_framework_processes(force: bool = True) -> dict[str, list[int]]:
    targets = _framework_worker_pids()
    killed: list[int] = []
    failed: list[int] = []
    for pid in sorted(targets):
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            continue
        except Exception:
            failed.append(pid)
    _cleanup_pid_files()
    return {"killed": killed, "failed": failed}


def _framework_worker_pids() -> set[int]:
    pids: set[int] = set()
    pids.update(_pid_file_pids())
    pids.update(_command_line_worker_pids())
    return pids


def _pid_file_pids() -> set[int]:
    pids: set[int] = set()
    if not WORKER_DIR.exists():
        return pids
    for pid_file in WORKER_DIR.glob("*.pid"):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        if pid > 0 and _is_framework_worker_pid(pid):
            pids.add(pid)
    return pids


def _is_framework_worker_pid(pid: int) -> bool:
    if os.name == "nt":
        return False
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], check=True, capture_output=True, text=True)
    except Exception:
        return False
    return _is_framework_worker_command(result.stdout)


def _command_line_worker_pids() -> set[int]:
    if os.name == "nt":
        return set()
    try:
        result = subprocess.run(["ps", "-eo", "pid=,command="], check=True, capture_output=True, text=True)
    except Exception:
        return set()
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if _is_framework_worker_command(command):
            pids.add(pid)
    return pids


def _is_framework_worker_command(command: str) -> bool:
    worker_script = str(WORKER_SCRIPT)
    worker_dir = str(WORKER_DIR)
    return worker_script in command and worker_dir in command


def _cleanup_pid_files() -> None:
    if not WORKER_DIR.exists():
        return
    for pid_file in WORKER_DIR.glob("*.pid"):
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


def _cleanup_at_exit() -> None:
    try:
        cleanup_framework_processes(force=True)
    except Exception:
        pass


class ContinuousGraphRunner:
    def __init__(self, runtime: GraphRuntime) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._payload: dict | None = None
        self._latest: dict = {"nodes": {}, "setup": {"complete": True}}
        self._running = False
        self._tick_count = 0
        self._started_at = 0.0
        self._stopped_at = 0.0
        self._hz = 1000.0
        self._duration_sec: float | None = None
        self._error = ""

    def start(self, payload: dict) -> dict:
        graph_payload = {
            "nodes": payload.get("nodes", []),
            "links": payload.get("links", []),
        }
        hz = max(1.0, min(float(payload.get("runHz") or 1000.0), 1000.0))
        duration_value = payload.get("durationSec")
        duration_sec = None
        if duration_value is not None:
            duration_sec = max(0.1, float(duration_value))
        with self._lock:
            self._stop_locked()
            self._payload = graph_payload
            self._hz = hz
            self._duration_sec = duration_sec
            self._tick_count = 0
            self._started_at = time.time()
            self._stopped_at = 0.0
            self._error = ""
            self._latest = {"nodes": {}, "setup": {"complete": True}, "lwrclpy": {"available": self.runtime.ros.available, "error": self.runtime.ros.error}}
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="lwrclpy-web-node-editor-runner", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            return self.status()

    def _stop_locked(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._stop_event.set()
            thread.join(timeout=2.0)
        self._thread = None
        if self._running:
            self._stopped_at = time.time()
        self._running = False
        self._stop_event.clear()

    def status(self) -> dict:
        with self._lock:
            latest = self._latest if isinstance(self._latest, dict) else {}
            return {
                **latest,
                "run": {
                    "running": self._running,
                    "tickCount": self._tick_count,
                    "startedAt": self._started_at,
                    "stoppedAt": self._stopped_at,
                    "hz": self._hz,
                    "durationSec": self._duration_sec,
                    "error": self._error,
                },
            }

    def _loop(self) -> None:
        next_at = time.time()
        stop_runtime = False
        while not self._stop_event.is_set():
            with self._lock:
                payload = self._payload
                duration_sec = self._duration_sec
                started_at = self._started_at
                hz = self._hz
            if payload is None:
                break
            now = time.time()
            if duration_sec is not None and now - started_at >= duration_sec:
                stop_runtime = True
                break
            try:
                result = self.runtime.run(payload)
                with self._lock:
                    self._latest = result
                    self._tick_count += 1
                    self._error = ""
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)
                    self._latest = {"error": str(exc), "nodes": {}, "setup": {"complete": False}}
                stop_runtime = True
                break
            next_at += 1.0 / max(1.0, hz)
            sleep_for = next_at - time.time()
            if sleep_for > 0:
                self._stop_event.wait(sleep_for)
            else:
                next_at = time.time()
        with self._lock:
            self._running = False
            self._stopped_at = time.time()
        if stop_runtime:
            self.runtime.stop(force=False)


class Handler(BaseHTTPRequestHandler):
    runtime = GraphRuntime()
    runner = ContinuousGraphRunner(runtime)

    def log_message(self, fmt: str, *args) -> None:
        if self.path in {"/api/run", "/api/run-status"} and args and str(args[1]) == "200":
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
        if path == "/api/run-status":
            self._send_json(self.runner.status())
            return
        self._send_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/run", "/api/start", "/api/stop", "/api/force-stop"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/run":
                result = self.runtime.run(payload)
            elif path == "/api/start":
                result = self.runner.start(payload)
            elif path == "/api/force-stop":
                self.runner.stop()
                result = self.runtime.stop(force=True)
                result["orphanProcesses"] = cleanup_framework_processes(force=True)
            else:
                self.runner.stop()
                result = self.runtime.stop(force=bool(payload.get("force", False)))
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
    startup_cleanup = cleanup_framework_processes(force=True)
    killed_count = len(startup_cleanup.get("killed", []))
    if killed_count:
        print(f"Cleaned up {killed_count} stale lwrclpy Web Node Editor worker process(es).")
    atexit.register(_cleanup_at_exit)
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno == 48:
            print(f"Port {args.port} is already in use. Stop the existing server or run with --port {args.port + 1}.", file=sys.stderr)
            return 1
        raise
    print(f"lwrclpy Web Node Editor: http://{args.host}:{args.port}")

    def handle_shutdown_signal(signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, handle_shutdown_signal)
        signal.signal(signal.SIGINT, handle_shutdown_signal)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.runner.stop()
        Handler.runtime.stop(force=True)
        Handler.runtime.close()
        cleanup_framework_processes(force=True)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
