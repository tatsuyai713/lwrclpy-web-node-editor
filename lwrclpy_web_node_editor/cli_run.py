from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .runtime_exec import (
    LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV,
    configure_local_lwrclpy_wheel,
    local_lwrclpy_wheel,
    local_lwrclpy_wheel_marker,
)


def _install_local_lwrclpy_for_current_python() -> None:
    wheel = local_lwrclpy_wheel()
    if wheel is None:
        return
    marker = local_lwrclpy_wheel_marker(wheel)
    if os.environ.get(LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV) == marker:
        return
    uv = shutil.which("uv")
    if uv:
        command = [
            uv,
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache",
            "--python",
            sys.executable,
            str(wheel),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            str(wheel),
        ]
    print(f"[lwrclpy-web-node-editor-cli] Installing local lwrclpy wheel: {wheel}", flush=True)
    subprocess.run(command, check=True)
    os.environ[LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV] = marker
    importlib.invalidate_caches()


def _load_project(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"project JSON must contain an object: {path}")
    nodes = payload.get("nodes")
    links = payload.get("links")
    if not isinstance(nodes, list):
        raise ValueError(f"project JSON has no nodes array: {path}")
    if links is None:
        links = []
    if not isinstance(links, list):
        raise ValueError(f"project JSON links must be an array: {path}")
    return {"nodes": nodes, "links": links}


def _node_summary(result: dict[str, Any]) -> str:
    nodes = result.get("nodes") if isinstance(result, dict) else {}
    if not isinstance(nodes, dict):
        return ""
    parts: list[str] = []
    for node_id, payload in sorted(nodes.items()):
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
        env = str(meta.get("environment") or "").strip()
        status = str(view.get("status") or "").strip()
        if status:
            parts.append(f"{node_id}:{status}")
        elif env:
            parts.append(f"{node_id}:{env}")
    return " | ".join(parts[:8])


def _print_status(tick: int, started_at: float, result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"tick": tick, "elapsedSec": time.time() - started_at, **result}, ensure_ascii=False, default=str), flush=True)
        return
    setup = result.get("setup") if isinstance(result, dict) else {}
    complete = bool(setup.get("complete", True)) if isinstance(setup, dict) else True
    summary = _node_summary(result)
    suffix = f" {summary}" if summary else ""
    print(f"[tick {tick}] elapsed={time.time() - started_at:.1f}s setup={'ok' if complete else 'not-ready'}{suffix}", flush=True)


def run_project(args: argparse.Namespace) -> int:
    if args.lwrclpy_wheel:
        configure_local_lwrclpy_wheel(args.lwrclpy_wheel)
        if not getattr(sys, "frozen", False):
            _install_local_lwrclpy_for_current_python()

    project_path = Path(args.project).expanduser().resolve()
    payload = _load_project(project_path)

    if args.cwd:
        workdir = Path(args.cwd).expanduser().resolve()
    else:
        script_path = Path(sys.argv[0]).expanduser()
        workdir = script_path.resolve().parent if script_path.exists() else project_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    os.environ.setdefault("LWRCLPY_CLI_VERBOSE_INSTALL", "1")
    print(f"[lwrclpy-web-node-editor-cli] Working directory: {workdir}", flush=True)

    from .graph import GraphRuntime

    runtime = GraphRuntime()
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        payload["runHz"] = max(0.1, float(args.hz))
        if not args.no_prepare:
            print(f"[lwrclpy-web-node-editor-cli] Preparing {project_path}", flush=True)
            prepared = runtime.prepare(payload)
            if args.print_prepare:
                print(json.dumps(prepared, ensure_ascii=False, default=str), flush=True)
            setup = prepared.get("setup") if isinstance(prepared, dict) else {}
            if not bool(prepared.get("ready")) or not bool(setup.get("complete", False)):
                print("[lwrclpy-web-node-editor-cli] Prepare failed", file=sys.stderr, flush=True)
                print(json.dumps(prepared, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
                return 1

        hz = max(0.1, float(args.hz))
        payload["runHz"] = hz
        period = 1.0 / hz
        duration = None if args.duration is None else max(0.0, float(args.duration))
        status_period = max(0.1, float(args.status_interval))
        started_at = time.time()
        next_tick_at = time.perf_counter()
        next_status_at = 0.0
        tick = 0
        latest: dict[str, Any] = {}
        print(
            f"[lwrclpy-web-node-editor-cli] Running {project_path.name} at {hz:g} Hz"
            + (f" for {duration:g}s" if duration is not None else " until interrupted"),
            flush=True,
        )
        while not stop_requested:
            if duration is not None and time.time() - started_at >= duration:
                break
            now_perf = time.perf_counter()
            if now_perf < next_tick_at:
                time.sleep(min(0.01, next_tick_at - now_perf))
                continue
            latest = runtime.run(payload)
            tick += 1
            now = time.time()
            if now >= next_status_at:
                _print_status(tick, started_at, latest, as_json=bool(args.json_status))
                next_status_at = now + status_period
            next_tick_at += period
            if next_tick_at < time.perf_counter() - period:
                next_tick_at = time.perf_counter()
        if latest and args.final_status:
            _print_status(tick, started_at, latest, as_json=bool(args.json_status))
        print(f"[lwrclpy-web-node-editor-cli] Stopped after {tick} ticks", flush=True)
        return 0
    finally:
        try:
            runtime.stop(force=bool(args.force_stop), lock_timeout=2.0)
            runtime.close()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a saved lwrclpy Web Node Editor project JSON from the CLI.")
    parser.add_argument("project", help="Path to a saved project JSON file.")
    parser.add_argument("--duration", "-d", type=float, default=None, help="Run duration in seconds. Omit to run until Ctrl-C.")
    parser.add_argument("--hz", type=float, default=60.0, help="Graph tick rate. Default: 60.")
    parser.add_argument("--status-interval", type=float, default=1.0, help="Seconds between CLI status lines. Default: 1.")
    parser.add_argument("--cwd", default="", help="Working directory for .node_workers, .node_envs, and relative output paths.")
    parser.add_argument("--lwrclpy-wheel", default="", help="Install and use a local lwrclpy wheel before loading the graph runtime.")
    parser.add_argument("--no-prepare", action="store_true", help="Skip the prepare step and start ticking immediately.")
    parser.add_argument("--print-prepare", action="store_true", help="Print the full prepare result JSON.")
    parser.add_argument("--json-status", action="store_true", help="Print status updates as JSON lines.")
    parser.add_argument("--no-final-status", dest="final_status", action="store_false", help="Do not print one final status line on exit.")
    parser.add_argument("--graceful-stop", dest="force_stop", action="store_false", help="Terminate workers gracefully instead of force stopping them.")
    parser.set_defaults(final_status=True, force_stop=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_project(args)


if __name__ == "__main__":
    raise SystemExit(main())
