from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

LWRCLPY_LOCAL_WHEEL_ENV = "LWRCLPY_LOCAL_WHEEL"
LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV = "LWRCLPY_LOCAL_WHEEL_INSTALLED"
WorkerKind = Literal["node", "video", "dds_tap", "builtin_source", "mcap_record"]

_WORKER_FLAGS: dict[WorkerKind, str] = {
    "node": "--worker-node",
    "video": "--worker-video",
    "dds_tap": "--worker-dds-tap",
    "builtin_source": "--worker-builtin-source",
    "mcap_record": "--worker-mcap-record",
}

_WORKER_SCRIPT_NAMES: dict[WorkerKind, str] = {
    "node": "node_worker.py",
    "video": "video_dds_worker.py",
    "dds_tap": "dds_tap_worker.py",
    "builtin_source": "builtin_source_worker.py",
    "mcap_record": "mcap_record_worker.py",
}


def _frozen_worker_script_path(worker: WorkerKind) -> Path | None:
    script_name = _WORKER_SCRIPT_NAMES[worker]
    candidates: list[Path] = []
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "_internal" / "lwrclpy_web_node_editor" / script_name)
    candidates.append(exe_dir / "lwrclpy_web_node_editor" / script_name)
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        base = Path(meipass)
        candidates.insert(0, base / "lwrclpy_web_node_editor" / script_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def standalone_app_home() -> Path:
    env_home = os.environ.get("LWRCLPY_WEB_NODE_EDITOR_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "lwrclpy-web-node-editor").resolve()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return (base / "lwrclpy-web-node-editor").resolve()
    return (Path.home() / ".local" / "share" / "lwrclpy-web-node-editor").resolve()


def find_lwrclpy_installer() -> Path | None:
    candidates: list[Path] = []
    if is_frozen_app():
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "scripts" / "install_lwrclpy.py")
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "scripts" / "install_lwrclpy.py")
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "scripts" / "install_lwrclpy.py")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def configure_local_lwrclpy_wheel(path: str | Path | None) -> Path | None:
    if path is None or not str(path).strip():
        os.environ.pop(LWRCLPY_LOCAL_WHEEL_ENV, None)
        return None
    wheel = Path(path).expanduser().resolve()
    if not wheel.exists():
        raise FileNotFoundError(f"lwrclpy wheel not found: {wheel}")
    if wheel.suffix != ".whl":
        raise ValueError(f"lwrclpy wheel must be a .whl file: {wheel}")
    os.environ[LWRCLPY_LOCAL_WHEEL_ENV] = str(wheel)
    return wheel


def local_lwrclpy_wheel() -> Path | None:
    raw = os.environ.get(LWRCLPY_LOCAL_WHEEL_ENV, "").strip()
    if not raw:
        return None
    wheel = Path(raw).expanduser().resolve()
    return wheel if wheel.exists() else None


def local_lwrclpy_wheel_marker(wheel: Path | None = None) -> str:
    wheel = wheel or local_lwrclpy_wheel()
    if wheel is None:
        return ""
    try:
        stat = wheel.stat()
        return f"{wheel}:{stat.st_size}:{stat.st_mtime_ns}"
    except Exception:
        return str(wheel)


def framework_worker_tokens() -> list[str]:
    if is_frozen_app():
        return list(_WORKER_FLAGS.values())
    package_dir = Path(__file__).resolve().parent
    return [str(package_dir / name) for name in _WORKER_SCRIPT_NAMES.values()]


def resolve_worker_command(worker: WorkerKind, config_path: Path, python_bin: Path | None = None) -> list[str]:
    if is_frozen_app():
        frozen_exe = Path(sys.executable).resolve()
        if python_bin is not None and Path(python_bin).resolve() != frozen_exe:
            worker_script = _frozen_worker_script_path(worker)
            if worker_script is not None:
                return [str(python_bin), str(worker_script), str(config_path)]
        return [str(frozen_exe), _WORKER_FLAGS[worker], str(config_path)]
    package_dir = Path(__file__).resolve().parent
    script_path = package_dir / _WORKER_SCRIPT_NAMES[worker]
    launcher = python_bin if python_bin is not None else Path(sys.executable)
    return [str(launcher), str(script_path), str(config_path)]
