from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .runtime_exec import configure_local_lwrclpy_wheel, local_lwrclpy_wheel
from .server import _server_lock_path, cleanup_framework_processes


def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout_sec: float = 15.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"desktop app server did not become ready in time: {url}")


def _stop_existing_app_server() -> None:
    lock_path = _server_lock_path()
    if not lock_path.exists():
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except Exception:
        pid = 0
    if pid > 0 and pid != os.getpid():
        try:
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            pass
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _server_command(host: str, port: int) -> list[str]:
    wheel_args: list[str] = []
    wheel = local_lwrclpy_wheel()
    if wheel is not None:
        wheel_args = ["--lwrclpy-wheel", str(wheel)]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--server", "--host", host, "--port", str(port), *wheel_args]
    project_root = Path(__file__).resolve().parents[1]
    main_py = project_root / "main.py"
    return [str(Path(sys.executable).resolve()), str(main_py), "--server", "--host", host, "--port", str(port), *wheel_args]


def _shutdown_runtime(server_proc: subprocess.Popen[str] | None, app_url: str) -> None:
    if server_proc is None:
        return
    try:
        req = urllib.request.Request(
            f"{app_url}/api/stop",
            data=b'{"force": true}',
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception:
        pass
    try:
        server_proc.terminate()
    except Exception:
        pass
    try:
        server_proc.wait(timeout=2.0)
    except Exception:
        pass
    try:
        server_proc.kill()
    except Exception:
        pass
    try:
        cleanup_framework_processes(force=True)
    except Exception:
        pass


def _run_external_browser_window(app_url: str, title: str, width: int, height: int, server_proc: subprocess.Popen[str]) -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget
    except Exception:
        try:
            webbrowser.open(app_url, new=1)
        except Exception:
            pass
        try:
            while server_proc.poll() is None:
                time.sleep(0.25)
        except KeyboardInterrupt:
            return 0
        return int(server_proc.returncode or 0)

    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle(title)
    window.resize(max(480, min(max(800, int(width)), 900)), max(240, min(max(360, int(height)), 600)))

    root = QWidget(window)
    layout = QVBoxLayout(root)
    label = QLabel(
        "lwrclpy Web Node Editor is running in your browser.\n"
        f"{app_url}\n\n"
        "Close this window to stop the local server."
    )
    label.setWordWrap(True)
    open_button = QPushButton("Open Browser")
    open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(app_url)))
    layout.addWidget(label)
    layout.addWidget(open_button)
    window.setCentralWidget(root)

    QDesktopServices.openUrl(QUrl(app_url))
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--title", default="lwrclpy Web Node Editor")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--lwrclpy-wheel", default="", help="Use this local lwrclpy .whl for the server and every node venv.")
    args = parser.parse_args(argv)
    if args.lwrclpy_wheel:
        try:
            configure_local_lwrclpy_wheel(args.lwrclpy_wheel)
        except Exception as exc:
            print(f"Failed to configure local lwrclpy wheel: {exc}", file=sys.stderr)
            return 1

    port = args.port if int(args.port) > 0 else _find_free_port(args.host)
    startup_cleanup = cleanup_framework_processes(force=True)
    killed_count = len(startup_cleanup.get("killed", []))
    if killed_count:
        print(f"Cleaned up {killed_count} stale lwrclpy Web Node Editor worker process(es).")

    _stop_existing_app_server()
    server_proc = subprocess.Popen(
        _server_command(args.host, port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    app_url = f"http://{args.host}:{port}"
    print(f"lwrclpy Web Node Editor Desktop: {app_url}")

    try:
        _wait_for_server(f"{app_url}/api/health")
    except Exception as exc:
        _shutdown_runtime(server_proc, app_url)
        print(str(exc), file=sys.stderr)
        return 1

    desktop_backend = os.environ.get("LWRCLPY_DESKTOP_BACKEND", "").strip().lower()
    if not desktop_backend and getattr(sys, "frozen", False) and sys.platform.startswith("win"):
        desktop_backend = "pyside6"
    webview = None
    if desktop_backend != "pyside6":
        try:
            import webview
        except Exception:
            webview = None

    if webview is not None:
        def on_closed() -> None:
            _shutdown_runtime(server_proc, app_url)

        window = webview.create_window(
            args.title,
            app_url,
            width=max(800, int(args.width)),
            height=max(600, int(args.height)),
            min_size=(800, 600),
        )
        window.events.closed += on_closed
        try:
            webview.start(debug=False, http_server=False, gui=os.environ.get("LWRCLPY_WEBVIEW_GUI") or None)
        except Exception as exc:
            print(f"pywebview failed, falling back to PySide6: {exc}", file=sys.stderr)
        else:
            _shutdown_runtime(server_proc, app_url)
            return 0

    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMainWindow
    except Exception as exc:
        if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
            print(f"PySide6 WebEngine unavailable, opening external browser: {exc}", file=sys.stderr)
            try:
                return _run_external_browser_window(app_url, args.title, args.width, args.height, server_proc)
            finally:
                _shutdown_runtime(server_proc, app_url)
        _shutdown_runtime(server_proc, app_url)
        print("pywebview or PySide6 is required for desktop mode. Install with: pip install pywebview", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle(args.title)
    window.resize(max(800, int(args.width)), max(600, int(args.height)))
    web = QWebEngineView(window)
    web.setUrl(QUrl(app_url))
    window.setCentralWidget(web)
    window.show()

    exit_code = 0
    try:
        exit_code = int(app.exec())
    finally:
        _shutdown_runtime(server_proc, app_url)
    return exit_code
