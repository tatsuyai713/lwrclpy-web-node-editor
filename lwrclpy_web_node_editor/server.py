from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import math
import mimetypes
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .graph import GraphRuntime, LWRCLPY_TYPE_TREE


STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_DIR = Path.cwd()
WORKER_SCRIPT = Path(__file__).resolve().parent / "node_worker.py"
VIDEO_WORKER_SCRIPT = Path(__file__).resolve().parent / "video_dds_worker.py"
DDS_TAP_WORKER_SCRIPT = Path(__file__).resolve().parent / "dds_tap_worker.py"
BUILTIN_SOURCE_WORKER_SCRIPT = Path(__file__).resolve().parent / "builtin_source_worker.py"
WORKER_DIR = PROJECT_DIR / ".node_workers"
GUI_DISPLAY_HZ = 30.0


def _json_default(value):
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _select_video_file() -> dict[str, object]:
    if sys.platform == "darwin":
        script = (
            'set f to choose file with prompt "Select video file" '
            'of type {"mp4", "mov", "m4v", "avi", "mkv", "webm"}\n'
            "POSIX path of f"
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            if "User canceled" in text or result.returncode == 1:
                return {"ok": True, "canceled": True}
            raise RuntimeError(text or "video file selection failed")
        path = result.stdout.strip()
    else:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(
                title="Select video file",
                filetypes=[
                    ("Video files", "*.mp4 *.mov *.m4v *.avi *.mkv *.webm"),
                    ("All files", "*.*"),
                ],
            )
        finally:
            root.destroy()
        if not path:
            return {"ok": True, "canceled": True}
    selected = Path(path).expanduser()
    if not selected.is_file():
        raise RuntimeError(f"selected file does not exist: {selected}")
    result: dict[str, object] = {"ok": True, "path": str(selected), "fileName": selected.name}
    try:
        result.update(_probe_video_file(selected))
    except Exception as exc:
        result["probeError"] = str(exc)
    return result


def _probe_video_file(path: Path) -> dict[str, object]:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open video: {path}")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    if fps <= 0 or fps >= 10000:
        fps = 30.0
    return {"width": width, "height": height, "fps": fps, "frameCount": frame_count}


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
    video_worker_script = str(VIDEO_WORKER_SCRIPT)
    dds_tap_worker_script = str(DDS_TAP_WORKER_SCRIPT)
    builtin_source_worker_script = str(BUILTIN_SOURCE_WORKER_SCRIPT)
    worker_dir = str(WORKER_DIR)
    return (
        worker_script in command
        or video_worker_script in command
        or dds_tap_worker_script in command
        or builtin_source_worker_script in command
    ) and worker_dir in command


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
        self._pending_param_updates: list[dict] = []
        self._stopping = False
        self._phase = "idle"

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
            # A new Run must start from a clean graph runtime. In particular,
            # non-looping video workers can finish with an ended status, and
            # keeping the old instance would prevent the next Run from
            # recreating the DDS publisher process.
            self.runtime.close()
            self._payload = graph_payload
            self._hz = hz
            self._duration_sec = duration_sec
            self._tick_count = 0
            self._started_at = time.time()
            self._stopped_at = 0.0
            self._error = ""
            self._latest = {"nodes": {}, "setup": {"complete": True}, "lwrclpy": self.runtime.ros.status()}
            self._stop_event.clear()
            self._stopping = False
            self._phase = "starting"
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="lwrclpy-web-node-editor-runner", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            return self.status()

    def update_payload(self, payload: dict) -> dict:
        graph_payload = {
            "nodes": payload.get("nodes", []),
            "links": payload.get("links", []),
        }
        with self._lock:
            if self._running and not self._stopping:
                self._payload = graph_payload
            return self.status()

    def update_node_params(self, payload: dict) -> dict:
        updates = payload.get("updates") or []
        if not isinstance(updates, list):
            return {"ok": True, "updated": 0}
        clean_updates = [item for item in updates if isinstance(item, dict)]
        if not clean_updates:
            return {"ok": True, "updated": 0}
        with self._lock:
            if self._running and not self._stopping:
                self._pending_param_updates.extend(clean_updates)
                self._pending_param_updates = self._coalesce_param_updates(self._pending_param_updates)
                return {"ok": True, "queued": len(clean_updates), "pending": len(self._pending_param_updates)}
        updated = self.runtime.update_node_params(clean_updates)
        return {"ok": True, "updated": updated}

    def _coalesce_param_updates(self, updates: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        order: list[str] = []
        for item in updates:
            node_id = str(item.get("nodeId") or "")
            params = item.get("params")
            if not node_id or not isinstance(params, dict):
                continue
            if node_id not in merged:
                merged[node_id] = {"nodeId": node_id, "params": {}}
                order.append(node_id)
            merged[node_id]["params"].update(params)
        return [merged[node_id] for node_id in order]

    def _stop_locked(self) -> None:
        thread = self._thread
        self._stopping = True
        self._payload = None
        self._pending_param_updates = []
        if thread is not None and thread.is_alive():
            self._stop_event.set()
            thread.join(timeout=1.0)
            if thread.is_alive():
                self._error = "runner stop timed out"
            else:
                self._thread = None
        else:
            self._thread = None
        self._stopped_at = time.time()
        self._running = bool(self._thread is not None and self._thread.is_alive())
        self._stop_event.clear()
        if not self._running:
            self._stopping = False
            self._phase = "stopped"

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
                    "phase": self._phase if self._running else "stopped",
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
                pending_updates = self._pending_param_updates
                self._pending_param_updates = []
            if payload is None:
                break
            if self._stop_event.is_set():
                break
            now = time.time()
            if duration_sec is not None and now - started_at >= duration_sec:
                stop_runtime = True
                break
            try:
                if pending_updates:
                    self.runtime.update_node_params(pending_updates)
                result = self.runtime.run(payload)
                with self._lock:
                    self._latest = result
                    self._tick_count += 1
                    self._phase = "running"
                    self._error = ""
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)
                    self._phase = "error"
                    self._latest = {"error": str(exc), "nodes": {}, "setup": {"complete": False}}
                stop_runtime = True
                break
            # All built-in and custom nodes run in isolated worker processes.
            # The server loop only starts workers and samples their status for
            # the Web UI, so spinning it at the model run frequency can starve
            # the HTTP server without improving DDS timing.
            status_hz = GUI_DISPLAY_HZ
            next_at += 1.0 / status_hz
            sleep_for = next_at - time.time()
            if sleep_for > 0:
                self._stop_event.wait(sleep_for)
            else:
                next_at = time.time()
        with self._lock:
            if threading.current_thread() is self._thread:
                self._thread = None
            self._running = False
            self._stopping = False
            if self._phase != "error":
                self._phase = "stopped"
            self._stopped_at = time.time()
        if stop_runtime:
            self.runtime.stop(force=False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    runtime = GraphRuntime()
    runner = ContinuousGraphRunner(runtime)
    _last_image_view_data_urls: dict[str, str] = {}
    _last_image_view_raw_signatures: dict[str, str] = {}
    _ready_signature = ""

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def finish(self) -> None:
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    @classmethod
    def _payload_signature(cls, payload: dict) -> str:
        nodes = []
        for node in payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            nodes.append({
                "id": str(node.get("id") or ""),
                "toolType": str(node.get("toolType") or ""),
                "requirements": str(node.get("requirements") or ""),
                "importCode": str(node.get("importCode") or ""),
                "inputs": [
                    {
                        "id": str(port.get("id") or ""),
                        "dataType": str(port.get("dataType") or ""),
                    }
                    for port in node.get("inputs", [])
                    if isinstance(port, dict)
                ],
                "outputs": [
                    {
                        "id": str(port.get("id") or ""),
                        "dataType": str(port.get("dataType") or ""),
                    }
                    for port in node.get("outputs", [])
                    if isinstance(port, dict)
                ],
            })
        encoded = json.dumps({"nodes": nodes}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def log_message(self, fmt: str, *args) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/api/run", "/api/run-status"} and args and str(args[1]) == "200":
            return
        if path == "/api/node-frame" and args and str(args[1]) in {"200", "204"}:
            return
        print("[lwrclpy_web_node_editor]", fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/message-types":
            self._send_json({"types": LWRCLPY_TYPE_TREE})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "lwrclpy": self.runtime.ros.status()})
            return
        if path == "/api/run-status":
            self._send_json(self._compact_run_status(self.runner.status()))
            return
        if path == "/api/node-frame":
            query = parse_qs(parsed.query)
            self._send_node_frame(str((query.get("nodeId") or [""])[0]))
            return
        self._send_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/select-video-file":
            try:
                self._send_json(_select_video_file())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return
        if path not in {"/api/run", "/api/ready", "/api/start", "/api/update-run-payload", "/api/update-node-params", "/api/stop", "/api/force-stop"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/run":
                signature = self._payload_signature(payload)
                if self.__class__._ready_signature != signature:
                    self._send_json({
                        "error": "Ready is required before Run",
                        "ready": False,
                        "setup": {"complete": False},
                    }, status=409)
                    return
                result = self.runtime.run(payload)
            elif path == "/api/ready":
                self.runner.stop()
                self.runtime.stop(force=True)
                cleanup_framework_processes(force=True)
                shutil.rmtree(Path.cwd() / ".node_envs", ignore_errors=True)
                shutil.rmtree(WORKER_DIR, ignore_errors=True)
                WORKER_DIR.mkdir(parents=True, exist_ok=True)
                result = self.runtime.prepare(payload)
                if result.get("ready"):
                    self.__class__._ready_signature = self._payload_signature(payload)
                    result["signature"] = self.__class__._ready_signature
                else:
                    self.__class__._ready_signature = ""
            elif path == "/api/start":
                signature = self._payload_signature(payload)
                if self.__class__._ready_signature != signature:
                    self._send_json({
                        "error": "Ready is required before Run",
                        "ready": False,
                        "setup": {"complete": False},
                        "run": {"running": False, "tickCount": 0, "error": "Ready is required before Run"},
                    }, status=409)
                    return
                self._last_image_view_data_urls.clear()
                self._last_image_view_raw_signatures.clear()
                result = self.runner.start(payload)
            elif path == "/api/update-run-payload":
                result = self.runner.update_payload(payload)
            elif path == "/api/update-node-params":
                result = self.runner.update_node_params(payload)
            elif path == "/api/force-stop":
                self.runner.stop()
                result = self.runtime.stop(force=True)
                result["orphanProcesses"] = cleanup_framework_processes(force=True)
            else:
                self.runner.stop()
                result = self.runtime.stop(force=bool(payload.get("force", True)))
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

    def _send_node_frame(self, node_id: str):
        frame = self.runtime.get_node_frame(node_id)
        if not frame:
            self._send_no_content()
            return
        data = frame.get("data")
        frame_path = Path(str(frame.get("path") or "")) if not isinstance(data, (bytes, bytearray)) else None
        if frame_path is not None and not frame_path.is_file():
            self._send_no_content()
            return
        encoding = str(frame.get("encoding") or "rgb8").lower()
        try:
            length = len(data) if isinstance(data, (bytes, bytearray)) else frame_path.stat().st_size
            self.send_response(200)
            content_type = {
                "jpeg": "image/jpeg",
                "jpg": "image/jpeg",
                "bmp": "image/bmp",
                "png": "image/png",
                "webp": "image/webp",
            }.get(encoding, "application/octet-stream")
            self.send_header("content-type", content_type)
            self.send_header("cache-control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("pragma", "no-cache")
            self.send_header("expires", "0")
            self.send_header("content-length", str(length))
            self.send_header("x-frame-seq", str(frame.get("seq") or 0))
            self.send_header("x-frame-width", str(frame.get("width") or 0))
            self.send_header("x-frame-height", str(frame.get("height") or 0))
            self.send_header("x-frame-source-width", str(frame.get("sourceWidth") or frame.get("width") or 0))
            self.send_header("x-frame-source-height", str(frame.get("sourceHeight") or frame.get("height") or 0))
            self.send_header("x-frame-encoding", encoding)
            self.end_headers()
            if isinstance(data, (bytes, bytearray)):
                self.wfile.write(data)
            else:
                with frame_path.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, length=256 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_no_content(self) -> None:
        try:
            self.send_response(204)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _compact_run_status(self, payload):
        if not isinstance(payload, dict):
            return payload
        nodes = payload.get("nodes")
        if not isinstance(nodes, dict):
            return payload
        compact_nodes = {}
        changed = False
        for node_id, node_payload in nodes.items():
            view = node_payload.get("view") if isinstance(node_payload, dict) else None
            if isinstance(view, dict) and view.get("kind") == "plot" and isinstance(view.get("series"), list):
                series = self._compact_plot_series(view.get("series"), view.get("xAxisSeconds"))
                if series is not view.get("series"):
                    next_view = dict(view)
                    next_view["series"] = series
                    next_node_payload = dict(node_payload)
                    next_node_payload["view"] = next_view
                    compact_nodes[node_id] = next_node_payload
                    changed = True
                    continue
            if not isinstance(view, dict) or view.get("kind") != "image" or not isinstance(view.get("dataUrl"), str):
                raw = view.get("raw") if isinstance(view, dict) else None
                if isinstance(raw, dict) and isinstance(raw.get("data"), str):
                    raw_data = raw.get("data") or ""
                    signature = f"{raw.get('width')}x{raw.get('height')}:{raw.get('encoding')}:{hashlib.blake2s(raw_data.encode('ascii'), digest_size=12).hexdigest()}"
                    if signature and self._last_image_view_raw_signatures.get(str(node_id)) == signature:
                        next_raw = dict(raw)
                        next_raw["data"] = ""
                        next_view = dict(view)
                        next_view["raw"] = next_raw
                        next_node_payload = dict(node_payload)
                        next_node_payload["view"] = next_view
                        compact_nodes[node_id] = next_node_payload
                        changed = True
                        continue
                    self._last_image_view_raw_signatures[str(node_id)] = signature
                compact_nodes[node_id] = node_payload
                continue
            data_url = view.get("dataUrl") or ""
            if data_url and self._last_image_view_data_urls.get(str(node_id)) == data_url:
                next_view = dict(view)
                next_view["dataUrl"] = ""
                next_node_payload = dict(node_payload)
                next_node_payload["view"] = next_view
                compact_nodes[node_id] = next_node_payload
                changed = True
                continue
            if data_url:
                self._last_image_view_data_urls[str(node_id)] = data_url
            compact_nodes[node_id] = node_payload
        if not changed:
            return payload
        compact = dict(payload)
        compact["nodes"] = compact_nodes
        return compact

    def _compact_plot_series(self, series, x_axis_seconds):
        points = []
        for item in series:
            if not isinstance(item, dict):
                continue
            try:
                t = float(item.get("t"))
                y = float(item.get("y"))
            except Exception:
                continue
            if math.isfinite(t) and math.isfinite(y):
                points.append({"t": t, "y": y})
        if len(points) <= 600:
            return series
        latest_t = max(point["t"] for point in points)
        try:
            window_sec = max(0.1, float(x_axis_seconds or 10.0))
        except Exception:
            window_sec = 10.0
        window_points = [point for point in points if point["t"] >= latest_t - window_sec] or points[-1:]
        if len(window_points) <= 600:
            return window_points
        step = len(window_points) / 600
        sampled = [window_points[min(len(window_points) - 1, int(index * step))] for index in range(600)]
        if sampled[-1] is not window_points[-1]:
            sampled.append(window_points[-1])
        return sampled

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


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    if hasattr(ThreadingHTTPServer, "allow_reuse_port"):
        allow_reuse_port = True


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
        server = ReusableThreadingHTTPServer((args.host, args.port), Handler)
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
