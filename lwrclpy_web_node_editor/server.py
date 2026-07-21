from __future__ import annotations

import argparse
import atexit
import errno
import hashlib
import io
import importlib
import json
import math
import mimetypes
import os
import re
import select
import signal
import shutil
import socket
import subprocess
import struct
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import shared_memory
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from . import graph as graph_module
from .cpp_codegen import render_cpp_node_cmake, render_cpp_node_source, render_cpp_workspace_cmake
from .graph import GraphRuntime
from .runtime_exec import (
    LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV,
    configure_local_lwrclpy_wheel,
    framework_worker_tokens,
    local_lwrclpy_wheel,
    local_lwrclpy_wheel_marker,
    standalone_app_home,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_DIR = Path.cwd()
WORKER_DIR = PROJECT_DIR / ".node_workers"
APP_SETTINGS_DIR = PROJECT_DIR / ".app_settings"
CUSTOM_NODE_DIR = APP_SETTINGS_DIR / "custom_nodes"
SAMPLES_DIR = PROJECT_DIR / "samples"
GRAPH_RUN_HZ = 60.0
STREAM_HEADER = struct.Struct("<4sI Q I I I I I I d")
STREAM_CHUNK_HEADER = struct.Struct("<4sQIIIIII")
STREAM_MAGIC = b"IPNF"
STREAM_CHUNK_MAGIC = b"IPFS"
STREAM_ENCODINGS = {1: "rgb8", 2: "bgr8", 3: "mono8", 10: "jpeg", 11: "bmp", 12: "png"}
LWRCLPY_RELEASES_API_URL = "https://api.github.com/repos/tatsuyai713/lwrclpy/releases"


def _server_lock_path() -> Path:
    if getattr(sys, "frozen", False):
        root = standalone_app_home()
    else:
        root = APP_SETTINGS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / "server.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_server_lock(host: str, port: int) -> tuple[Path, bool]:
    lock_path = _server_lock_path()
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({
                    "pid": os.getpid(),
                    "host": host,
                    "port": int(port),
                    "startedAt": time.time(),
                }, handle)
            return lock_path, True
        except FileExistsError:
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                pid = int(payload.get("pid") or 0)
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                return lock_path, False
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                return lock_path, False
    return lock_path, False


def _release_server_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except Exception:
        pid = 0
    if pid and pid != os.getpid():
        return
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _json_default(value):
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _dependency_site_dir() -> Path:
    if getattr(sys, "frozen", False):
        root = standalone_app_home()
    else:
        root = APP_SETTINGS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / "python_site"


def _ensure_mcap_dependencies() -> None:
    site_dir = _dependency_site_dir()
    if site_dir.is_dir():
        site_text = str(site_dir)
        if site_text not in sys.path:
            sys.path.insert(0, site_text)
    try:
        import mcap  # noqa: F401
        import yaml  # noqa: F401
        return
    except Exception:
        pass
    site_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(site_dir),
            "--prefer-binary",
            "mcap",
            "mcap-ros2-support",
            "PyYAML",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    site_text = str(site_dir)
    if site_text not in sys.path:
        sys.path.insert(0, site_text)
    import mcap  # noqa: F401
    import yaml  # noqa: F401


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


def _select_urdf_file() -> dict[str, object]:
    if sys.platform == "darwin":
        script = (
            'set f to choose file with prompt "Select URDF or Xacro file" '
            'of type {"urdf", "xacro", "xml"}\n'
            "POSIX path of f"
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            if "User canceled" in text or result.returncode == 1:
                return {"ok": True, "canceled": True}
            raise RuntimeError(text or "URDF/Xacro file selection failed")
        path = result.stdout.strip()
    else:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(
                title="Select URDF or Xacro file",
                filetypes=[
                    ("URDF/Xacro files", "*.urdf *.xacro *.xml"),
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
    if selected.suffix.lower() not in {".urdf", ".xacro", ".xml"}:
        raise RuntimeError(f"selected file is not a URDF/Xacro file: {selected}")
    return {"ok": True, "path": str(selected), "fileName": selected.name}


def _load_urdf_or_xacro(path: Path) -> str:
    if path.suffix.lower() == ".xacro":
        try:
            import xacro

            return xacro.process_file(str(path)).toxml()
        except Exception:
            pass
        try:
            result = subprocess.run(["xacro", str(path)], capture_output=True, text=True, check=True)
            return result.stdout
        except Exception as exc:
            raise RuntimeError(f"failed to process xacro: {exc}") from exc
    return path.read_text(encoding="utf-8")


def _float_list(text: str, count: int, default: float = 0.0) -> list[float]:
    values = []
    for part in str(text or "").replace(",", " ").split():
        try:
            values.append(float(part))
        except Exception:
            pass
    while len(values) < count:
        values.append(default)
    return values[:count]


def _robot_model_payload(path_text: str) -> dict[str, object]:
    path = Path(str(path_text or "")).expanduser()
    if not path.is_file():
        raise RuntimeError(f"URDF/Xacro file not found: {path}")
    root = ET.fromstring(_load_urdf_or_xacro(path))
    visuals: list[dict[str, object]] = []
    for link in root.findall("link"):
        link_name = str(link.attrib.get("name") or "")
        if not link_name:
            continue
        for visual in link.findall("visual"):
            origin = visual.find("origin")
            xyz = _float_list(origin.attrib.get("xyz", "") if origin is not None else "", 3, 0.0)
            rpy = _float_list(origin.attrib.get("rpy", "") if origin is not None else "", 3, 0.0)
            geometry = visual.find("geometry")
            if geometry is None:
                continue
            item: dict[str, object] = {"link": link_name, "xyz": xyz, "rpy": rpy}
            box = geometry.find("box")
            cylinder = geometry.find("cylinder")
            sphere = geometry.find("sphere")
            mesh = geometry.find("mesh")
            if box is not None:
                item.update({"type": "box", "size": _float_list(box.attrib.get("size", ""), 3, 1.0)})
            elif cylinder is not None:
                item.update({"type": "cylinder", "radius": float(cylinder.attrib.get("radius") or 0.5), "length": float(cylinder.attrib.get("length") or 1.0)})
            elif sphere is not None:
                item.update({"type": "sphere", "radius": float(sphere.attrib.get("radius") or 0.5)})
            elif mesh is not None:
                filename = str(mesh.attrib.get("filename") or "")
                mesh_path = _resolve_robot_mesh_path(path.parent, filename)
                item.update({
                    "type": "mesh",
                    "filename": filename,
                    "url": f"/api/robot-mesh?path={quote(str(mesh_path))}" if mesh_path and mesh_path.is_file() else "",
                    "extension": mesh_path.suffix.lower() if mesh_path else Path(filename).suffix.lower(),
                    "scale": _float_list(mesh.attrib.get("scale", ""), 3, 1.0),
                })
            else:
                continue
            visuals.append(item)
    return {"ok": True, "path": str(path), "fileName": path.name, "visuals": visuals}


def _resolve_robot_mesh_path(urdf_dir: Path, filename: str) -> Path | None:
    if not filename:
        return None
    text = filename
    if text.startswith("file://"):
        return Path(text[7:]).expanduser().resolve()
    if text.startswith("package://"):
        rest = text[len("package://"):]
        parts = rest.split("/", 1)
        rel = parts[1] if len(parts) == 2 else ""
        candidates = [
            PROJECT_DIR / rest,
            PROJECT_DIR / "src" / rest,
            PROJECT_DIR / "install" / rest,
        ]
        if len(parts) == 2:
            candidates.extend([
                PROJECT_DIR / parts[0] / rel,
                PROJECT_DIR / "src" / parts[0] / rel,
                PROJECT_DIR / "install" / parts[0] / "share" / parts[0] / rel,
            ])
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (urdf_dir / path).resolve()


def _select_mcap_file() -> dict[str, object]:
    if sys.platform == "darwin":
        script = (
            'set modeChoice to button returned of (display dialog "Select MCAP file or ROS 2 bag directory" buttons {"Cancel", "ROS 2 Bag", "MCAP File"} default button "MCAP File" cancel button "Cancel")\n'
            'if modeChoice is "ROS 2 Bag" then\n'
            '  set f to choose folder with prompt "Select ROS 2 bag directory"\n'
            'else\n'
            '  set f to choose file with prompt "Select MCAP file"\n'
            'end if\n'
            "POSIX path of f"
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            if "User canceled" in text or result.returncode == 1:
                return {"ok": True, "canceled": True}
            raise RuntimeError(text or "MCAP file selection failed")
        path = result.stdout.strip()
    else:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(
                title="Select MCAP file",
                filetypes=[
                    ("MCAP files", "*.mcap"),
                    ("All files", "*.*"),
                ],
            )
        finally:
            root.destroy()
        if not path:
            return {"ok": True, "canceled": True}
    selected = Path(path).expanduser()
    if not selected.exists():
        raise RuntimeError(f"selected MCAP path does not exist: {selected}")
    try:
        return {"ok": True, **_probe_mcap_path(selected)}
    except Exception as exc:
        return {"ok": True, "path": str(selected), "fileName": selected.name, "channels": [], "channelCount": 0, "probeError": str(exc)}


def _select_mcap_record_file() -> dict[str, object]:
    if sys.platform == "darwin":
        script = (
            'set f to choose file name with prompt "Save ROS 2 bag recording as" default name "recording"\n'
            "POSIX path of f"
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            if "User canceled" in text or result.returncode == 1:
                return {"ok": True, "canceled": True}
            raise RuntimeError(text or "MCAP output selection failed")
        path = result.stdout.strip()
    else:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.asksaveasfilename(
                title="Save ROS 2 bag recording as",
                filetypes=[
                    ("ROS 2 bag directories", "*"),
                    ("All files", "*.*"),
                ],
            )
        finally:
            root.destroy()
        if not path:
            return {"ok": True, "canceled": True}
    selected = Path(path).expanduser()
    selected.parent.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(selected), "fileName": selected.name}


def _open_mcap_file(path: object) -> dict[str, object]:
    selected = Path(str(path or "")).expanduser()
    if not selected.exists():
        raise RuntimeError(f"selected MCAP path does not exist: {selected}")
    if selected.is_file() and selected.suffix.lower() not in {".mcap", ".yaml", ".yml", ".json"}:
        raise RuntimeError(f"selected file is not an MCAP or ROS 2 bag metadata file: {selected}")
    try:
        return {"ok": True, **_probe_mcap_path(selected)}
    except Exception as exc:
        return {"ok": True, "path": str(selected), "fileName": selected.name, "channels": [], "channelCount": 0, "probeError": str(exc)}


def _natural_sort_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _strip_yaml_scalar(value: str) -> str:
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def _yaml_relative_file_paths(metadata_path: Path) -> list[str]:
    try:
        text = metadata_path.read_text(encoding="utf-8")
    except Exception:
        return []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "relative_file_paths:":
            continue
        paths: list[str] = []
        for item in lines[index + 1:]:
            stripped = item.strip()
            if not stripped:
                continue
            if stripped.startswith("- "):
                paths.append(_strip_yaml_scalar(stripped[2:]))
                continue
            if not item.startswith(" ") and stripped.endswith(":"):
                break
        return paths
    return []


def _resolve_mcap_paths(path: Path) -> tuple[Path, list[Path], Path | None]:
    selected = path.expanduser()
    if selected.is_file() and selected.name in {"metadata.yaml", "metadata.yml", "metadata.json"}:
        selected = selected.parent
    if selected.is_file():
        if selected.suffix.lower() != ".mcap":
            raise RuntimeError(f"selected file is not an .mcap file: {selected}")
        return selected, [selected], None
    if not selected.is_dir():
        raise RuntimeError(f"selected MCAP path does not exist: {selected}")
    metadata_path = next((candidate for candidate in [
        selected / "metadata.yaml",
        selected / "metadata.yml",
        selected / "metadata.json",
    ] if candidate.is_file()), None)
    files: list[Path] = []
    if metadata_path is not None and metadata_path.suffix.lower() == ".json":
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            info = data.get("rosbag2_bagfile_information") if isinstance(data, dict) else {}
            rels = info.get("relative_file_paths") if isinstance(info, dict) else []
            if isinstance(rels, list):
                files = [selected / str(rel) for rel in rels]
        except Exception:
            files = []
    elif metadata_path is not None:
        files = [selected / rel for rel in _yaml_relative_file_paths(metadata_path)]
    files = [file for file in files if file.is_file() and file.suffix.lower() == ".mcap"]
    if not files:
        files = sorted(selected.glob("*.mcap"), key=_natural_sort_key)
    if not files:
        raise RuntimeError(f"ROS 2 bag directory has no .mcap files: {selected}")
    return selected, files, metadata_path


def _normalize_mcap_message_type(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text.replace(".", "/")
    return text.replace(".", "/")


def _is_ros2_mcap_channel(channel: dict[str, object]) -> bool:
    message_encoding = str(channel.get("messageEncoding") or "").lower()
    data_type = _normalize_mcap_message_type(channel.get("type") or "")
    return message_encoding == "cdr" and "/msg/" in data_type


def _load_mcap_sidecar_metadata(path: Path) -> dict[str, object]:
    _ensure_mcap_dependencies()
    candidates = [
        path.parent / "metadata.yaml",
        path.parent / "metadata.yml",
        path.parent / "metadata.json",
        path.with_suffix(path.suffix + ".metadata.yaml"),
        path.with_suffix(path.suffix + ".metadata.yml"),
        path.with_suffix(path.suffix + ".metadata.json"),
        path.with_suffix(".metadata.yaml"),
        path.with_suffix(".metadata.yml"),
        path.with_suffix(".metadata.json"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            if candidate.suffix.lower() == ".json":
                data = json.loads(candidate.read_text(encoding="utf-8"))
            else:
                import yaml
                data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"metadataPath": str(candidate), "metadataError": str(exc)}
        return {"metadataPath": str(candidate), "metadata": data if isinstance(data, dict) else {}}
    return {}


def _metadata_topics(metadata: object) -> list[dict[str, object]]:
    if not isinstance(metadata, dict):
        return []
    source = metadata.get("topics") or metadata.get("channels") or metadata.get("rosbag2_bagfile_information")
    if isinstance(source, dict):
        source = source.get("topics") or source.get("topics_with_message_count")
    if not isinstance(source, list):
        return []
    topics: list[dict[str, object]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        topic_metadata = item.get("topic_metadata") if isinstance(item.get("topic_metadata"), dict) else item
        name = topic_metadata.get("name") or topic_metadata.get("topic") or item.get("topic")
        type_name = topic_metadata.get("type") or topic_metadata.get("message_type") or item.get("type")
        if not name or not type_name:
            continue
        topics.append({
            "topic": str(name),
            "type": _normalize_mcap_message_type(type_name),
            "messageCount": int(item.get("message_count") or item.get("messageCount") or 0),
        })
    return topics


def _metadata_time_ns(value: object, key: str) -> int:
    if isinstance(value, dict):
        value = value.get(key)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _metadata_bag_timing(metadata: object) -> tuple[int, int]:
    if not isinstance(metadata, dict):
        return 0, 0
    info = metadata.get("rosbag2_bagfile_information")
    if not isinstance(info, dict):
        return 0, 0
    return (
        _metadata_time_ns(info.get("starting_time"), "nanoseconds_since_epoch"),
        _metadata_time_ns(info.get("duration"), "nanoseconds"),
    )


def _duration_from_times(start_time: int | None, end_time: int | None, metadata: object) -> tuple[int | None, int | None, float]:
    metadata_start_ns, metadata_duration_ns = _metadata_bag_timing(metadata)
    if metadata_start_ns:
        start_time = metadata_start_ns
    if metadata_duration_ns > 0:
        if start_time is not None:
            end_time = int(start_time) + metadata_duration_ns
        return start_time, end_time, metadata_duration_ns / 1e9
    duration_sec = ((end_time - start_time) / 1e9) if start_time is not None and end_time is not None else 0.0
    return start_time, end_time, duration_sec


def _probe_mcap_path(path: Path) -> dict[str, object]:
    display_path, files, metadata_path = _resolve_mcap_paths(path)
    channels: dict[str, dict[str, object]] = {}
    start_time: int | None = None
    end_time: int | None = None
    for file_path in files:
        file_probe = _probe_mcap_file(file_path, include_sidecar=False)
        file_start = int(file_probe.get("startTimeNs") or 0) or None
        file_end = int(file_probe.get("endTimeNs") or 0) or None
        start_time = file_start if start_time is None else (min(start_time, file_start) if file_start is not None else start_time)
        end_time = file_end if end_time is None else (max(end_time, file_end) if file_end is not None else end_time)
        for channel in file_probe.get("channels", []):
            if not isinstance(channel, dict):
                continue
            topic = str(channel.get("topic") or "")
            if not topic:
                continue
            item = channels.setdefault(topic, {
                "topic": topic,
                "type": channel.get("type") or "",
                "messageEncoding": channel.get("messageEncoding") or "",
                "schemaEncoding": channel.get("schemaEncoding") or "",
                "messageCount": 0,
            })
            if channel.get("type") and not item.get("type"):
                item["type"] = channel["type"]
            if channel.get("messageEncoding") and not item.get("messageEncoding"):
                item["messageEncoding"] = channel["messageEncoding"]
            if channel.get("schemaEncoding") and not item.get("schemaEncoding"):
                item["schemaEncoding"] = channel["schemaEncoding"]
            item["messageCount"] = int(item.get("messageCount") or 0) + int(channel.get("messageCount") or 0)
    sidecar = _load_mcap_sidecar_metadata(files[0])
    if metadata_path is not None:
        loaded = _load_mcap_sidecar_metadata(metadata_path if metadata_path.is_file() else files[0])
        if loaded:
            sidecar = loaded
        sidecar.setdefault("metadataPath", str(metadata_path))
    for meta_topic in _metadata_topics(sidecar.get("metadata")):
        topic = str(meta_topic.get("topic") or "")
        if not topic:
            continue
        item = channels.setdefault(topic, {"topic": topic, "messageCount": 0})
        if meta_topic.get("type"):
            item["type"] = meta_topic["type"]
        if meta_topic.get("messageCount"):
            item["messageCount"] = meta_topic["messageCount"]
    sorted_channels = sorted(channels.values(), key=lambda item: str(item.get("topic") or ""))
    for channel in sorted_channels:
        channel["ros2Compatible"] = _is_ros2_mcap_channel(channel)
    start_time, end_time, duration_sec = _duration_from_times(start_time, end_time, sidecar.get("metadata"))
    return {
        "path": str(display_path),
        "fileName": display_path.name,
        "mcapFiles": [str(file) for file in files],
        "fileCount": len(files),
        "channels": sorted_channels,
        "channelCount": len(sorted_channels),
        "ros2ChannelCount": sum(1 for channel in sorted_channels if channel.get("ros2Compatible")),
        "startTimeNs": start_time or 0,
        "endTimeNs": end_time or 0,
        "durationSec": duration_sec,
        **sidecar,
    }


def _probe_mcap_file(path: Path, include_sidecar: bool = True) -> dict[str, object]:
    _ensure_mcap_dependencies()
    from mcap.reader import make_reader

    channels: dict[str, dict[str, object]] = {}
    start_time: int | None = None
    end_time: int | None = None
    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()
        if summary is not None:
            stats = summary.statistics
            start_time = int(stats.message_start_time or 0) or None
            end_time = int(stats.message_end_time or 0) or None
            for channel_id, channel in summary.channels.items():
                schema = summary.schemas.get(channel.schema_id)
                channels[str(channel.topic)] = {
                    "topic": str(channel.topic),
                    "type": _normalize_mcap_message_type(schema.name if schema else ""),
                    "messageEncoding": channel.message_encoding,
                    "schemaEncoding": schema.encoding if schema else "",
                    "messageCount": int(stats.channel_message_counts.get(channel_id, 0)),
                }
        else:
            for schema, channel, message in reader.iter_messages(log_time_order=False):
                topic = str(channel.topic)
                schema_name = _normalize_mcap_message_type(schema.name if schema else "")
                item = channels.setdefault(topic, {
                    "topic": topic,
                    "type": schema_name,
                    "messageEncoding": channel.message_encoding,
                    "schemaEncoding": schema.encoding if schema else "",
                    "messageCount": 0,
                })
                if schema_name and not item.get("type"):
                    item["type"] = schema_name
                item["messageCount"] = int(item.get("messageCount") or 0) + 1
                start_time = message.log_time if start_time is None else min(start_time, message.log_time)
                end_time = message.log_time if end_time is None else max(end_time, message.log_time)
    sidecar = _load_mcap_sidecar_metadata(path) if include_sidecar else {}
    for meta_topic in _metadata_topics(sidecar.get("metadata")):
        topic = str(meta_topic.get("topic") or "")
        if not topic:
            continue
        item = channels.setdefault(topic, {"topic": topic, "messageCount": 0})
        if meta_topic.get("type"):
            item["type"] = meta_topic["type"]
        if meta_topic.get("messageCount"):
            item["messageCount"] = meta_topic["messageCount"]
    sorted_channels = sorted(channels.values(), key=lambda item: str(item.get("topic") or ""))
    for channel in sorted_channels:
        channel["ros2Compatible"] = _is_ros2_mcap_channel(channel)
    start_time, end_time, duration_sec = _duration_from_times(start_time, end_time, sidecar.get("metadata"))
    return {
        "path": str(path),
        "fileName": path.name,
        "channels": sorted_channels,
        "channelCount": len(sorted_channels),
        "ros2ChannelCount": sum(1 for channel in sorted_channels if channel.get("ros2Compatible")),
        "startTimeNs": start_time or 0,
        "endTimeNs": end_time or 0,
        "durationSec": duration_sec,
        **sidecar,
    }


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


def _safe_custom_node_id(value: object) -> str:
    text = str(value or "").strip().replace(" ", "_")
    safe = "".join(ch for ch in text if ch.isalnum() or ch in {"_", "-", "."}).strip("._-")
    return safe[:80] or f"custom_node_{int(time.time() * 1000)}"


def _custom_node_path(node_id: object) -> Path:
    CUSTOM_NODE_DIR.mkdir(parents=True, exist_ok=True)
    return CUSTOM_NODE_DIR / f"{_safe_custom_node_id(node_id)}.json"


def _wheel_runtime_info(name: str) -> dict[str, str] | None:
    match = re.search(r"lwrclpy-([^-]+)-cp(\d)(\d{1,2})-", name)
    if not match:
        return None
    return {"lwrclpyVersion": match.group(1), "pythonVersion": f"{match.group(2)}.{match.group(3)}"}


def _local_lwrclpy_release_options() -> list[dict[str, object]]:
    wheel = local_lwrclpy_wheel()
    if wheel is None:
        return []
    info = _wheel_runtime_info(wheel.name)
    if not info:
        return []
    return [{
        "tag": info["lwrclpyVersion"],
        "version": info["lwrclpyVersion"],
        "pythonVersions": [info["pythonVersion"]],
        "assetNames": [wheel.name],
        "local": True,
    }]


def _lwrclpy_release_options() -> dict[str, object]:
    releases: list[dict[str, object]] = []
    error = ""
    try:
        request = urllib.request.Request(
            LWRCLPY_RELEASES_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "lwrclpy-web-node-editor",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for release in payload if isinstance(payload, list) else []:
            assets = release.get("assets") if isinstance(release, dict) else []
            versions: set[str] = set()
            lwrclpy_versions: set[str] = set()
            asset_names: list[str] = []
            tag = str(release.get("tag_name") or "")
            if tag == "latest":
                continue
            for asset in assets if isinstance(assets, list) else []:
                name = str(asset.get("name") or "")
                info = _wheel_runtime_info(name)
                if not info:
                    continue
                if "latest" in info["lwrclpyVersion"]:
                    continue
                versions.add(info["pythonVersion"])
                lwrclpy_versions.add(info["lwrclpyVersion"])
                asset_names.append(name)
            if not versions:
                continue
            release_version = sorted(lwrclpy_versions, reverse=True)[0] if lwrclpy_versions else (tag.lstrip("v") or tag)
            releases.append({
                "tag": tag,
                "version": release_version,
                "pythonVersions": sorted(versions, key=lambda item: tuple(int(part) for part in item.split("."))),
                "assetNames": sorted(asset_names),
                "local": False,
            })
    except Exception as exc:
        error = str(exc)
    local = _local_lwrclpy_release_options()
    for item in local:
        existing = next((release for release in releases if str(release.get("version")) == str(item.get("version"))), None)
        if existing is not None:
            versions = set(map(str, existing.get("pythonVersions", [])))
            versions.update(map(str, item.get("pythonVersions", [])))
            existing["pythonVersions"] = sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")))
            existing["local"] = True
            existing["assetNames"] = sorted(set(map(str, existing.get("assetNames", []))) | set(map(str, item.get("assetNames", []))))
        else:
            releases.insert(0, item)
    host_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not releases:
        installed = ""
        try:
            installed = importlib.metadata.version("lwrclpy")
        except Exception:
            installed = ""
        releases.append({
            "tag": installed or "host",
            "version": installed or "",
            "pythonVersions": [host_python],
            "assetNames": [],
            "local": False,
            "fallback": True,
        })
    releases.sort(key=_lwrclpy_release_sort_key, reverse=True)
    return {"releases": releases, "hostPythonVersion": host_python, "error": error}


def _lwrclpy_release_sort_key(release: dict[str, object]) -> tuple[int, tuple[int, ...], str]:
    version = str(release.get("version") or "")
    numeric = tuple(int(part) for part in re.findall(r"\d+", version)[:3])
    return 0, numeric, version


def _normalize_custom_node_payload(payload: dict) -> dict:
    node = payload.get("node")
    if not isinstance(node, dict):
        raise ValueError("custom node payload requires a node object")
    if node.get("toolType"):
        raise ValueError("only custom lwrclpy nodes can be saved as custom nodes")
    meta = node.get("customNodeMeta") if isinstance(node.get("customNodeMeta"), dict) else {}
    name = str(payload.get("name") or meta.get("name") or node.get("name") or "custom_node").strip() or "custom_node"
    node_id = _safe_custom_node_id(payload.get("id") or meta.get("id") or name)
    stored_node = dict(node)
    for key in ("id", "x", "y", "toolType", "customNodeMeta"):
        stored_node.pop(key, None)
    stored_node["pythonVersion"] = str(stored_node.get("pythonVersion") or "").strip()
    stored_node["lwrclpyVersion"] = str(stored_node.get("lwrclpyVersion") or "").strip()
    return {
        "format": "lwrclpy-web-node-editor-custom-node",
        "version": int(payload.get("version") or meta.get("version") or 1),
        "id": node_id,
        "name": name,
        "description": str(payload.get("description") or meta.get("description") or "").strip(),
        "node": stored_node,
    }


def _read_custom_nodes() -> list[dict]:
    CUSTOM_NODE_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for path in sorted(CUSTOM_NODE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("node"), dict):
            data.setdefault("id", path.stem)
            items.append(data)
    return items


def _write_custom_node(payload: dict) -> dict:
    item = _normalize_custom_node_payload(payload)
    path = _custom_node_path(item["id"])
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return item


def _delete_custom_node(payload: dict) -> dict:
    node_id = payload.get("id")
    if not node_id:
        raise ValueError("id is required")
    path = _custom_node_path(node_id)
    deleted = path.exists()
    path.unlink(missing_ok=True)
    return {"ok": True, "deleted": deleted, "id": _safe_custom_node_id(node_id)}


def _import_custom_nodes(payload: dict) -> dict:
    raw_items = payload.get("items")
    if raw_items is None:
        if isinstance(payload.get("node"), dict):
            raw_items = [payload]
        elif isinstance(payload.get("customNodes"), list):
            raw_items = payload.get("customNodes")
        elif isinstance(payload.get("nodes"), list):
            raw_items = [{"node": node, "name": node.get("name")} for node in payload.get("nodes") if isinstance(node, dict) and not node.get("toolType")]
        else:
            raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError("items must be a list")
    imported = []
    for raw in raw_items:
        if isinstance(raw, dict):
            imported.append(_write_custom_node(raw))
    return {"ok": True, "imported": imported}


def _sample_project_items() -> list[dict[str, object]]:
    if not SAMPLES_DIR.exists():
        return []
    items: list[dict[str, object]] = []
    for path in sorted(SAMPLES_DIR.glob("**/*.json")):
        if not path.is_file():
            continue
        rel = path.relative_to(SAMPLES_DIR).as_posix()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        items.append({
            "path": rel,
            "name": str(payload.get("name") or Path(rel).stem),
            "category": rel.split("/", 1)[0] if "/" in rel else "",
        })
    return items


def _read_sample_project(path_value: object) -> dict[str, object]:
    rel = str(path_value or "").strip()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise ValueError("invalid sample path")
    path = (SAMPLES_DIR / rel).resolve()
    try:
        path.relative_to(SAMPLES_DIR.resolve())
    except ValueError:
        raise ValueError("invalid sample path") from None
    if not path.is_file() or path.suffix.lower() != ".json":
        raise FileNotFoundError(f"sample not found: {rel}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": True, "path": rel, "project": payload}


def _safe_archive_name(value: object, fallback: str = "lwrclpy_cli_project") -> str:
    name = str(value or fallback).strip().lower()
    name = re.sub(r"[^a-z0-9_.-]+", "_", name).strip("._-")
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"project_{name}"
    return name


def _cli_export_project_name(payload: dict) -> str:
    raw = payload.get("name") or payload.get("projectName") or payload.get("fileName")
    if raw:
        return _safe_archive_name(raw)
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    for node in nodes:
        if isinstance(node, dict) and node.get("name"):
            return _safe_archive_name(f"{node.get('name')}_project")
    return "lwrclpy_cli_project"


EXPORT_EXCLUDED_TOOL_TYPES = {
    "image_view",
    "string_view",
    "graph_view",
    "topic_hz_monitor",
    "topic_input",
    "topic_output",
}


def _valid_ros_type_name(value: object) -> bool:
    parts = str(value or "").split("/")
    return len(parts) == 3 and all(parts)


def _normalize_export_topic(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("/") else f"/{text}"


def _find_port(node: dict[str, Any] | None, direction: str, port_id: object) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    ports = node.get(direction)
    if not isinstance(ports, list):
        return None
    for port in ports:
        if isinstance(port, dict) and port.get("id") == port_id:
            return port
    return None


def _append_export_port_topic(port: dict[str, Any] | None, topic: str) -> None:
    if not port or not topic:
        return
    topics = port.setdefault("topics", [])
    if not isinstance(topics, list):
        topics = []
        port["topics"] = topics
    if topic not in topics:
        topics.append(topic)


def _mark_export_external(node: dict[str, Any] | None) -> None:
    if not isinstance(node, dict):
        return
    params = node.setdefault("params", {})
    if not isinstance(params, dict):
        params = {}
        node["params"] = params
    params["_externalDdsCompatible"] = True


def _infer_export_link_types(src_port: dict[str, Any] | None, dst_port: dict[str, Any] | None) -> None:
    if not src_port or not dst_port:
        return
    src_type = str(src_port.get("dataType") or "")
    dst_type = str(dst_port.get("dataType") or "")
    if not _valid_ros_type_name(src_type) and _valid_ros_type_name(dst_type):
        src_port["dataType"] = dst_type
    elif not _valid_ros_type_name(dst_type) and _valid_ros_type_name(src_type):
        dst_port["dataType"] = src_type


def _node_language(node: dict[str, Any] | None) -> str:
    return str((node or {}).get("language") or "python").strip().lower()


def _is_cpp_node(node: dict[str, Any] | None) -> bool:
    return _node_language(node) in {"cpp", "c++"}


def _is_python_runtime_export_node(node: dict[str, Any]) -> bool:
    return str(node.get("toolType") or "") not in EXPORT_EXCLUDED_TOOL_TYPES and not _is_cpp_node(node)


def _export_runtime_payload(payload: dict) -> dict[str, Any]:
    source_nodes = [
        node for node in (payload.get("nodes") if isinstance(payload.get("nodes"), list) else [])
        if isinstance(node, dict)
    ]
    source_by_id = {str(node.get("id") or ""): node for node in source_nodes}
    nodes = [
        json.loads(json.dumps(node, default=str))
        for node in source_nodes
        if _is_python_runtime_export_node(node)
    ]
    runtime_by_id = {str(node.get("id") or ""): node for node in nodes}
    active_ids = {str(node.get("id") or "") for node in nodes}
    links: list[dict[str, Any]] = []
    for raw_link in (payload.get("links") if isinstance(payload.get("links"), list) else []):
        if not isinstance(raw_link, dict):
            continue
        link = json.loads(json.dumps(raw_link, default=str))
        src_id = str(link.get("fromNode") or "")
        dst_id = str(link.get("toNode") or "")
        src_source = source_by_id.get(src_id)
        dst_source = source_by_id.get(dst_id)
        src_runtime = runtime_by_id.get(src_id)
        dst_runtime = runtime_by_id.get(dst_id)
        topic = _normalize_export_topic(
            link.get("name")
            or f"/{link.get('fromNode')}_{link.get('fromPort')}_to_{link.get('toNode')}_{link.get('toPort')}"
        )
        if src_id in active_ids and dst_id in active_ids:
            src_port = _find_port(src_runtime, "outputs", link.get("fromPort"))
            dst_port = _find_port(dst_runtime, "inputs", link.get("toPort"))
            _infer_export_link_types(src_port, dst_port)
            links.append(link)
            continue
        if str(src_source.get("toolType") if src_source else "") == "topic_input" and dst_runtime is not None:
            src_port = _find_port(src_source, "outputs", link.get("fromPort"))
            dst_port = _find_port(dst_runtime, "inputs", link.get("toPort"))
            _infer_export_link_types(src_port, dst_port)
            _append_export_port_topic(dst_port, topic)
            _mark_export_external(dst_runtime)
            continue
        if _is_cpp_node(src_source) and dst_runtime is not None:
            src_port = _find_port(src_source, "outputs", link.get("fromPort"))
            dst_port = _find_port(dst_runtime, "inputs", link.get("toPort"))
            _infer_export_link_types(src_port, dst_port)
            _append_export_port_topic(dst_port, topic)
            _mark_export_external(dst_runtime)
            continue
        if src_runtime is not None and str(dst_source.get("toolType") if dst_source else "") == "topic_output":
            src_port = _find_port(src_runtime, "outputs", link.get("fromPort"))
            dst_port = _find_port(dst_source, "inputs", link.get("toPort"))
            _infer_export_link_types(src_port, dst_port)
            _append_export_port_topic(src_port, topic)
            _mark_export_external(src_runtime)
            continue
        if src_runtime is not None and _is_cpp_node(dst_source):
            src_port = _find_port(src_runtime, "outputs", link.get("fromPort"))
            dst_port = _find_port(dst_source, "inputs", link.get("toPort"))
            _infer_export_link_types(src_port, dst_port)
            _append_export_port_topic(src_port, topic)
            _mark_export_external(src_runtime)
            continue
    return {
        "format": str(payload.get("format") or "lwrclpy-web-node-editor-project"),
        "version": int(payload.get("version") or 1),
        "nodes": nodes,
        "links": links,
        "view": payload.get("view") if isinstance(payload.get("view"), dict) else {"x": 0, "y": 0, "scale": 1},
        "nextId": payload.get("nextId") or 1,
    }


def _build_cli_export_zip(payload: dict) -> tuple[str, bytes]:
    project_name = _cli_export_project_name(payload)
    project_payload = _export_runtime_payload(payload)
    cpp_nodes = _cpp_export_nodes(payload)
    root = f"{project_name}_cli"
    buffer = io.BytesIO()
    wheel = local_lwrclpy_wheel()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{root}/project.json", json.dumps(project_payload, ensure_ascii=False, indent=2, default=str) + "\n")
        archive.writestr(f"{root}/README.md", _render_cli_export_readme(project_name, wheel, cpp_nodes))
        archive.writestr(f"{root}/requirements.txt", _render_cli_export_requirements(wheel))
        archive.writestr(f"{root}/run_project.py", _render_cli_package_run_project(wheel))
        if cpp_nodes:
            archive.writestr(f"{root}/cpp_nodes/CMakeLists.txt", _render_cpp_workspace_cmake(cpp_nodes))
            archive.writestr(f"{root}/build_cpp_nodes.sh", _render_cpp_build_script())
            for cpp_node in cpp_nodes:
                node_dir = f"{root}/cpp_nodes/{cpp_node['package_name']}"
                archive.writestr(f"{node_dir}/CMakeLists.txt", _render_cpp_node_cmake(cpp_node))
                archive.writestr(f"{node_dir}/src/{cpp_node['executable_name']}.cpp", _render_cpp_node_source(cpp_node))
        for source in _cli_package_runtime_files():
            archive.write(source, f"{root}/lwrclpy_web_node_editor/{source.name}")
        if wheel is not None and wheel.is_file():
            archive.write(wheel, f"{root}/wheels/{wheel.name}")
        archive.writestr(f"{root}/.gitignore", "venv/\n.venv/\n.node_envs/\n.node_workers/\n__pycache__/\n*.pyc\n")
    return f"{project_name}_cli_package.zip", buffer.getvalue()


def _cli_package_runtime_files() -> list[Path]:
    package_dir = PROJECT_DIR / "lwrclpy_web_node_editor"
    names = [
        "__init__.py",
        "cli_run.py",
        "runtime_exec.py",
        "graph.py",
        "node_worker.py",
        "video_dds_worker.py",
        "dds_tap_worker.py",
        "builtin_source_worker.py",
        "mcap_record_worker.py",
    ]
    return [package_dir / name for name in names if (package_dir / name).is_file()]


def _safe_cpp_identifier(value: object, fallback: str = "node") -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"n_{text}"
    return text


def _cpp_type(data_type: object) -> str:
    parts = str(data_type or "std_msgs/msg/String").replace(".", "/").split("/")
    if len(parts) != 3:
        parts = ["std_msgs", "msg", "String"]
    package, kind, name = parts
    namespace = "msg" if kind == "msg" else kind
    return f"{package}::{namespace}::{name}"


def _is_cpp_message_type(data_type: object) -> bool:
    parts = str(data_type or "").replace(".", "/").split("/")
    return len(parts) == 3 and parts[1] == "msg" and _valid_ros_type_name(data_type)


def _cpp_include(data_type: object) -> str:
    parts = str(data_type or "std_msgs/msg/String").replace(".", "/").split("/")
    if len(parts) != 3:
        parts = ["std_msgs", "msg", "String"]
    package, kind, name = parts
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return f"{package}/{kind}/{snake}.hpp"


def _cpp_string_literal(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=True)


def _cpp_message_packages_for_node(node: dict[str, Any]) -> list[str]:
    packages: set[str] = set()
    for direction in ("inputs", "outputs"):
        for port in node.get(direction, []) if isinstance(node.get(direction), list) else []:
            if not isinstance(port, dict):
                continue
            parts = str(port.get("dataType") or "").replace(".", "/").split("/")
            if len(parts) == 3 and re.fullmatch(r"[a-z][a-z0-9_]*", parts[0]):
                packages.add(parts[0])
    return sorted(packages)


def _cpp_export_nodes(payload: dict) -> list[dict[str, Any]]:
    source_nodes = [
        node for node in (payload.get("nodes") if isinstance(payload.get("nodes"), list) else [])
        if isinstance(node, dict)
    ]
    source_by_id = {str(node.get("id") or ""): node for node in source_nodes}
    cpp_nodes = [json.loads(json.dumps(node, default=str)) for node in source_nodes if _is_cpp_node(node) and not node.get("toolType")]
    cpp_by_id = {str(node.get("id") or ""): node for node in cpp_nodes}
    used_packages: set[str] = set()
    for index, node in enumerate(cpp_nodes, start=1):
        base = re.sub(r"[^a-z0-9_]", "_", str(node.get("name") or node.get("id") or f"cpp_node_{index}").lower()).strip("_") or f"cpp_node_{index}"
        if base[0].isdigit():
            base = f"node_{base}"
        name = base
        suffix = 2
        while name in used_packages:
            name = f"{base}_{suffix}"
            suffix += 1
        used_packages.add(name)
        node["package_name"] = name
        node["executable_name"] = name
        node["ros_node_name"] = name
        node["class_name"] = _safe_cpp_identifier("".join(part.capitalize() for part in name.split("_")), "GeneratedNode")
        node["message_packages"] = _cpp_message_packages_for_node(node)
        node["importCode"] = str(node.get("importCode") or "")
        node["loopCode"] = str(node.get("loopCode") or "")
        node["requirements"] = str(node.get("requirements") or "")
        node["cppCode"] = str(node.get("cppCode") or "")
        node["timers"] = [timer for timer in (node.get("timers") if isinstance(node.get("timers"), list) else []) if isinstance(timer, dict)]
        for direction in ("inputs", "outputs"):
            for port in node.get(direction, []) if isinstance(node.get(direction), list) else []:
                if isinstance(port, dict):
                    port["topics"] = []
    for raw_link in (payload.get("links") if isinstance(payload.get("links"), list) else []):
        if not isinstance(raw_link, dict):
            continue
        src = source_by_id.get(str(raw_link.get("fromNode") or ""))
        dst = source_by_id.get(str(raw_link.get("toNode") or ""))
        topic = _normalize_export_topic(raw_link.get("name") or f"/{raw_link.get('fromNode')}_{raw_link.get('fromPort')}_to_{raw_link.get('toNode')}_{raw_link.get('toPort')}")
        if str(raw_link.get("fromNode") or "") in cpp_by_id:
            port = _find_port(cpp_by_id[str(raw_link.get("fromNode"))], "outputs", raw_link.get("fromPort"))
            _infer_export_link_types(port, _find_port(dst, "inputs", raw_link.get("toPort")))
            _append_export_port_topic(port, topic)
        if str(raw_link.get("toNode") or "") in cpp_by_id:
            port = _find_port(cpp_by_id[str(raw_link.get("toNode"))], "inputs", raw_link.get("toPort"))
            _infer_export_link_types(_find_port(src, "outputs", raw_link.get("fromPort")), port)
            _append_export_port_topic(port, topic)
    return cpp_nodes


def _render_cpp_workspace_cmake(cpp_nodes: list[dict[str, Any]]) -> str:
    return render_cpp_workspace_cmake([str(node["package_name"]) for node in cpp_nodes])


def _render_cpp_node_cmake(node: dict[str, Any]) -> str:
    return render_cpp_node_cmake(node)


def _render_cpp_node_source(node: dict[str, Any]) -> str:
    return render_cpp_node_source(node, run_hz=GRAPH_RUN_HZ)


def _indent_cpp_user_code(code: str, spaces: int) -> str:
    text = code.strip() or "// Add C++ logic here."
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else "" for line in text.splitlines())


def _render_cpp_build_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${ROOT}/cpp_build"

cmake -S "${ROOT}/cpp_nodes" -B "${BUILD_DIR}" -DCMAKE_BUILD_TYPE=Release
cmake --build "${BUILD_DIR}" --parallel

echo "Built C++ nodes under ${BUILD_DIR}."
"""


def _render_cli_package_run_project(wheel: Path | None) -> str:
    wheel_arg = f"wheels/{wheel.name}" if wheel is not None and wheel.is_file() else ""
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from lwrclpy_web_node_editor.cli_run import main


def _argv() -> list[str]:
    root = Path(__file__).resolve().parent
    args = list(sys.argv[1:])
    if not args or args[0].startswith("-"):
        args.insert(0, str(root / "project.json"))
    if "--cwd" not in args:
        args.extend(["--cwd", str(root)])
    wheel = root / {wheel_arg!r}
    if {bool(wheel_arg)!r} and wheel.exists() and "--lwrclpy-wheel" not in args:
        args.extend(["--lwrclpy-wheel", str(wheel)])
    return args


if __name__ == "__main__":
    raise SystemExit(main(_argv()))
'''


def _build_ros2_export_zip(payload: dict) -> tuple[str, bytes]:
    project_name = _cli_export_project_name(payload)
    # ROS 2 package names may only contain [a-z0-9_] and must start with a letter.
    package_name = re.sub(r"[^a-z0-9_]", "_", _safe_archive_name(f"{project_name}_runner")).strip("_") or "exported_runner"
    if package_name[0].isdigit():
        package_name = f"pkg_{package_name}"
    project_payload = _export_runtime_payload(payload)
    cpp_nodes = _cpp_export_nodes(payload)
    root = f"{project_name}_ros2"
    runner_text = (PROJECT_DIR / "lwrclpy_web_node_editor" / "cli_export_runner.py").read_text(encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        base = f"{root}/{package_name}"
        archive.writestr(f"{root}/README.md", _render_ros2_export_readme(project_name, package_name))
        archive.writestr(f"{base}/package.xml", _render_ros2_runner_package_xml(package_name, _export_message_packages(project_payload)))
        archive.writestr(f"{base}/setup.py", _render_ros2_runner_setup_py(package_name))
        archive.writestr(f"{base}/setup.cfg", _render_ros2_setup_cfg(package_name))
        archive.writestr(f"{base}/resource/{package_name}", "")
        archive.writestr(f"{base}/{package_name}/__init__.py", "")
        archive.writestr(f"{base}/{package_name}/runner.py", runner_text)
        archive.writestr(f"{base}/{package_name}/project.json", json.dumps(project_payload, ensure_ascii=False, indent=2, default=str) + "\n")
        archive.writestr(f"{base}/launch/{project_name}.launch.py", _render_ros2_runner_launch_py(package_name))
        if cpp_nodes:
            archive.writestr(f"{root}/cpp_nodes/CMakeLists.txt", _render_cpp_workspace_cmake(cpp_nodes))
            archive.writestr(f"{root}/build_cpp_nodes.sh", _render_cpp_build_script())
            for cpp_node in cpp_nodes:
                node_dir = f"{root}/cpp_nodes/{cpp_node['package_name']}"
                archive.writestr(f"{node_dir}/CMakeLists.txt", _render_cpp_node_cmake(cpp_node))
                archive.writestr(f"{node_dir}/src/{cpp_node['executable_name']}.cpp", _render_cpp_node_source(cpp_node))
    return f"{project_name}_ros2_package.zip", buffer.getvalue()


def _render_ros2_export_readme(project_name: str, package_name: str) -> str:
    return f"""# {project_name} ROS 2 Export

This archive contains one ROS 2 Python package. It runs the exported project, including supported built-in nodes, in a normal ROS 2 `rclpy` environment.

## Build

Copy `{package_name}` into a ROS 2 workspace `src` directory:

```bash
colcon build --packages-select {package_name}
source install/setup.bash
```

Install Python packages used by built-ins if they are not already available in the ROS 2 Python environment:

```bash
python3 -m pip install numpy opencv-python-headless pillow mcap mcap-ros2-support PyYAML
```

## Run

```bash
ros2 launch {package_name} {project_name}.launch.py
```

External image/video paths stored in `project.json` must exist on the target machine.

## C++ / lwrcl nodes

If the project contains C++ nodes, this archive also includes `cpp_nodes/` and `build_cpp_nodes.sh`.
Install and build `tatsuyai713/lwrcl` with the FastDDS backend first, then run:

```bash
./build_cpp_nodes.sh
```

The C++ executables communicate with the Python runner via the same DDS topic names stored in the exported graph.
"""


def _export_message_packages(project_payload: dict) -> list[str]:
    """Collect ROS message/service packages referenced by exported node ports."""
    packages: set[str] = set()
    for node in (project_payload.get("nodes") if isinstance(project_payload.get("nodes"), list) else []):
        if not isinstance(node, dict):
            continue
        for direction in ("inputs", "outputs"):
            ports = node.get(direction)
            if not isinstance(ports, list):
                continue
            for port in ports:
                if not isinstance(port, dict):
                    continue
                data_type = str(port.get("dataType") or "").replace(".", "/")
                parts = data_type.split("/")
                if len(parts) == 3 and parts[1] in {"msg", "srv"} and re.fullmatch(r"[a-z][a-z0-9_]*", parts[0]):
                    packages.add(parts[0])
    return sorted(packages)


def _render_ros2_runner_package_xml(package_name: str, message_packages: list[str] | None = None) -> str:
    depends = ["rclpy", "std_msgs", "sensor_msgs"]
    for package in message_packages or []:
        if package not in depends:
            depends.append(package)
    depend_lines = "\n".join(f"  <exec_depend>{package}</exec_depend>" for package in depends)
    return f"""<?xml version="1.0"?>
<package format="3">
  <name>{package_name}</name>
  <version>0.0.0</version>
  <description>ROS 2 runner package exported from lwrclpy Web Node Editor.</description>
  <maintainer email="user@example.com">user</maintainer>
  <license>TODO</license>
  <buildtool_depend>ament_python</buildtool_depend>
{depend_lines}
  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
"""


def _render_ros2_runner_setup_py(package_name: str) -> str:
    return f"""from glob import glob
from setuptools import find_packages, setup

package_name = '{package_name}'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    package_data={{package_name: ['project.json']}},
    include_package_data=True,
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@example.com',
    description='ROS 2 runner package exported from lwrclpy Web Node Editor.',
    license='TODO',
    entry_points={{'console_scripts': ['run_project = {package_name}.runner:main']}},
)
"""


def _render_ros2_setup_cfg(package_name: str) -> str:
    return f"""[develop]
script_dir=$base/lib/{package_name}
[install]
install_scripts=$base/lib/{package_name}
"""


def _render_ros2_runner_launch_py(package_name: str) -> str:
    return f"""import os

from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        SetEnvironmentVariable('LWRCLPY_CLI_EXPORT_NO_BOOTSTRAP', '1'),
        Node(
            package='{package_name}',
            executable='run_project',
            name='{package_name}',
            output='screen',
            additional_env={{'LWRCLPY_CLI_EXPORT_NO_BOOTSTRAP': '1'}},
        ),
    ])
"""


def _render_cli_export_requirements(wheel: Path | None, project_payload: dict | None = None) -> str:
    lines = [
        "pillow",
        "opencv-python-headless",
        "numpy",
        "mcap",
        "mcap-ros2-support",
        "PyYAML",
    ]
    for node in (project_payload or {}).get("nodes", []):
        if not isinstance(node, dict):
            continue
        for raw_line in str(node.get("requirements") or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line not in lines:
                lines.append(line)
    if wheel is not None and wheel.is_file():
        lines.append(f"./wheels/{wheel.name}")
    return "\n".join(lines) + "\n"


def _render_cli_export_readme(project_name: str, wheel: Path | None, cpp_nodes: list[dict[str, Any]] | None = None) -> str:
    wheel_text = (
        f"- Bundled lwrclpy wheel: `wheels/{wheel.name}`\n"
        if wheel is not None and wheel.is_file()
        else "- No local lwrclpy wheel was configured when this package was exported. The runner will try to download a matching lwrclpy wheel on first run.\n"
    )
    return f"""# {project_name} CLI Export

This archive runs a saved lwrclpy Web Node Editor project without the web UI.
It contains `run_project.py`, `project.json`, a small CLI runtime, `requirements.txt`, and the lwrclpy wheel used by the editor when available.

## Requirements

- Python 3.13 or compatible Python for the lwrclpy wheel you use.
- `venv` from the Python standard library, or `uv`.
{wheel_text}

No system-wide install script is required. Use any Python environment with `venv` or `uv` available.
Each exported node creates its own environment under `.node_envs/<node_id>` when needed, so node-specific Python versions and requirements stay separate.

Manual install, if desired:

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

Do not run `pip install -r wheels/name.whl`; `-r` is only for requirements text files.

## Run

macOS/Linux:

```bash
python3 run_project.py --duration 10
```

Windows:

```bat
python run_project.py --duration 10
```

Without `--duration`, the project runs until interrupted.

## Notes

- `project.json` is the exported editor project.
- Node environments are created under `.node_envs/` beside this README.
- Worker state and logs are created under `.node_workers/`.
- External image/video paths stored in the project must exist on the target PC, or you must edit `project.json`.
- The package includes only the CLI graph runtime and worker scripts needed to run the project. It does not include the web UI.
- For ROS 2/DDS communication across machines, make sure both PCs use compatible `ROS_DOMAIN_ID`, network interfaces, and QoS settings.
{_render_cli_cpp_readme_section(cpp_nodes or [])}
"""


def _render_cli_cpp_readme_section(cpp_nodes: list[dict[str, Any]]) -> str:
    if not cpp_nodes:
        return ""
    names = "\n".join(f"- `{node['package_name']}`" for node in cpp_nodes)
    return f"""

## C++ / lwrcl nodes

This export contains C++ nodes under `cpp_nodes/`:

{names}

Install and build `tatsuyai713/lwrcl` with the FastDDS backend first:

```bash
git clone --recursive https://github.com/tatsuyai713/lwrcl.git
cd lwrcl
./scripts/install_fast_dds.sh
./build_libraries.sh fastdds install
./build_data_types.sh fastdds install
./build_lwrcl.sh fastdds install
```

Then build the exported C++ nodes:

```bash
./build_cpp_nodes.sh
```

Run the generated executables from `cpp_build/<node>/` alongside `run_project.py`.
They use the same DDS topic names as the Python/lwrclpy runner.
"""


def cleanup_framework_processes(force: bool = True) -> dict[str, list[int]]:
    targets = _framework_worker_pids()
    killed: list[int] = []
    failed: list[int] = []
    sig = signal.SIGKILL if force else signal.SIGTERM
    for pid in sorted(targets):
        if pid == os.getpid():
            continue
        try:
            _signal_process_tree(pid, sig)
            killed.append(pid)
        except ProcessLookupError:
            continue
        except Exception:
            failed.append(pid)
    if not force:
        deadline = time.time() + 1.0
        while time.time() < deadline and any(_pid_alive(pid) for pid in killed):
            time.sleep(0.05)
        for pid in list(killed):
            if pid == os.getpid() or not _pid_alive(pid):
                continue
            try:
                _signal_process_tree(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                if pid not in failed:
                    failed.append(pid)
    deadline = time.time() + 1.0
    while time.time() < deadline and any(_pid_alive(pid) for pid in killed):
        time.sleep(0.05)
    _cleanup_pid_files()
    return {"killed": killed, "failed": failed}


def _signal_process_tree(pid: int, sig: int) -> None:
    if os.name == "nt":
        os.kill(pid, sig)
        return
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        raise
    except Exception:
        os.kill(pid, sig)


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
    worker_dir = str(WORKER_DIR)
    return any(token in command for token in framework_worker_tokens()) and worker_dir in command


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


def _install_local_lwrclpy_for_server() -> None:
    wheel = local_lwrclpy_wheel()
    if wheel is None:
        return
    marker = local_lwrclpy_wheel_marker(wheel)
    if os.environ.get(LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV) == marker:
        graph_module.LWRCLPY_TYPE_TREE = graph_module.discover_lwrclpy_types()
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
    print(f"Installing local lwrclpy wheel for server: {wheel}", flush=True)
    subprocess.run(command, cwd=Path.cwd(), check=True)
    os.environ[LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV] = marker
    importlib.invalidate_caches()
    graph_module.LWRCLPY_TYPE_TREE = graph_module.discover_lwrclpy_types()


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
        self._hz = GRAPH_RUN_HZ
        self._duration_sec: float | None = None
        self._error = ""
        self._pending_param_updates: list[dict] = []
        self._stopping = False
        self._phase = "idle"

    def start(self, payload: dict) -> dict:
        hz = max(1.0, min(float(payload.get("runHz") or GRAPH_RUN_HZ), 120.0))
        graph_payload = {
            "nodes": payload.get("nodes", []),
            "links": payload.get("links", []),
            "runHz": hz,
        }
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
        hz = max(1.0, min(float(payload.get("runHz") or self._hz), 120.0))
        graph_payload = {
            "nodes": payload.get("nodes", []),
            "links": payload.get("links", []),
            "runHz": hz,
        }
        with self._lock:
            if self._running and not self._stopping:
                self._payload = graph_payload
                self._hz = hz
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
            if self._error == "runner stop timed out":
                self._error = ""

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
            next_at += 1.0 / max(1.0, hz)
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
                "pythonVersion": str(node.get("pythonVersion") or ""),
                "lwrclpyVersion": str(node.get("lwrclpyVersion") or ""),
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
        if path in {"/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}:
            self._send_no_content()
            return
        if path == "/api/message-types":
            self._send_json({"types": graph_module.LWRCLPY_TYPE_TREE})
            return
        if path == "/api/lwrclpy-releases":
            self._send_json(_lwrclpy_release_options())
            return
        if path == "/api/custom-nodes":
            self._send_json({"customNodes": _read_custom_nodes()})
            return
        if path == "/api/sample-projects":
            self._send_json({"samples": _sample_project_items()})
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
        if path == "/api/node-frame-stream":
            query = parse_qs(parsed.query)
            self._send_node_frame_stream(str((query.get("nodeId") or [""])[0]))
            return
        if path == "/api/robot-mesh":
            query = parse_qs(parsed.query)
            self._send_robot_mesh(str((query.get("path") or [""])[0]))
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
        if path == "/api/select-urdf-file":
            try:
                self._send_json(_select_urdf_file())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return
        if path == "/api/select-mcap-file":
            try:
                self._send_json(_select_mcap_file())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return
        if path == "/api/select-mcap-record-file":
            try:
                self._send_json(_select_mcap_record_file())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return
        if path not in {
            "/api/run",
            "/api/ready",
            "/api/start",
            "/api/update-run-payload",
            "/api/update-node-params",
            "/api/stop",
            "/api/custom-nodes/save",
            "/api/custom-nodes/delete",
            "/api/custom-nodes/import",
            "/api/open-mcap-file",
            "/api/robot-model",
            "/api/sample-project",
            "/api/export-cli",
            "/api/export-ros2-package",
        }:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/custom-nodes/save":
                result = {"ok": True, "customNode": _write_custom_node(payload), "customNodes": _read_custom_nodes()}
            elif path == "/api/custom-nodes/delete":
                result = _delete_custom_node(payload)
                result["customNodes"] = _read_custom_nodes()
            elif path == "/api/custom-nodes/import":
                result = _import_custom_nodes(payload)
                result["customNodes"] = _read_custom_nodes()
            elif path == "/api/open-mcap-file":
                result = _open_mcap_file(payload.get("path"))
            elif path == "/api/robot-model":
                result = _robot_model_payload(str(payload.get("path") or ""))
            elif path == "/api/sample-project":
                result = _read_sample_project(payload.get("path"))
            elif path == "/api/export-cli":
                filename, data = _build_cli_export_zip(payload)
                self._send_blob(data, "application/zip", filename)
                return
            elif path == "/api/export-ros2-package":
                filename, data = _build_ros2_export_zip(payload)
                self._send_blob(data, "application/zip", filename)
                return
            elif path == "/api/run":
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
                signature = self._payload_signature(payload)
                run_state = self.runner.status().get("run", {})
                if self.__class__._ready_signature == signature and not run_state.get("running"):
                    result = self.runtime.ready_status()
                    result["signature"] = signature
                else:
                    self.runner.stop()
                    self.runtime.stop(force=True, lock_timeout=0.2)
                    cleanup_framework_processes(force=True)
                    shutil.rmtree(WORKER_DIR, ignore_errors=True)
                    WORKER_DIR.mkdir(parents=True, exist_ok=True)
                    result = self.runtime.prepare(payload)
                if result.get("ready"):
                    self.__class__._ready_signature = signature
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
            else:
                requested_force = bool(payload.get("force", True))
                orphan_processes = cleanup_framework_processes(force=True) if requested_force else {"killed": [], "failed": []}
                runner_status = self.runner.stop()
                run_state = runner_status.get("run", {}) if isinstance(runner_status, dict) else {}
                should_force = requested_force or str(run_state.get("error") or "") == "runner stop timed out"
                result = self.runtime.stop(force=should_force, lock_timeout=0.0 if should_force else 1.0)
                result["orphanProcesses"] = orphan_processes
                result["runner"] = run_state
                if should_force and not requested_force:
                    result["escalatedForce"] = True
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

    def _send_blob(self, data: bytes, content_type: str, filename: str, status: int = 200):
        safe_name = str(filename or "download.bin").replace('"', "")
        try:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(data)))
            self.send_header("content-disposition", f'attachment; filename="{safe_name}"')
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

    def _read_stream_frame(self, frame: dict) -> dict | None:
        name = str(frame.get("streamName") or "")
        size = int(frame.get("streamSize") or 0)
        if not name or size <= STREAM_HEADER.size:
            return None
        memory = None
        try:
            memory = shared_memory.SharedMemory(name=name, create=False)
            header = bytes(memory.buf[:STREAM_HEADER.size])
            magic, version, seq, width, height, encoding_code, data_len, source_width, source_height, timestamp = STREAM_HEADER.unpack(header)
            if magic != STREAM_MAGIC or version != 1 or data_len <= 0 or data_len + STREAM_HEADER.size > memory.size:
                return None
            data = bytes(memory.buf[STREAM_HEADER.size:STREAM_HEADER.size + data_len])
            header_after = bytes(memory.buf[:STREAM_HEADER.size])
            if header_after != header:
                return None
            return {
                "seq": int(seq),
                "width": int(width),
                "height": int(height),
                "sourceWidth": int(source_width),
                "sourceHeight": int(source_height),
                "encodingCode": int(encoding_code),
                "encoding": STREAM_ENCODINGS.get(int(encoding_code), "rgb8"),
                "timestamp": float(timestamp),
                "data": data,
            }
        except FileNotFoundError:
            return None
        except Exception:
            return None
        finally:
            if memory is not None:
                try:
                    memory.close()
                except Exception:
                    pass

    def _send_node_frame_stream(self, node_id: str):
        try:
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("cache-control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("connection", "close")
            self.end_headers()
            last_seq = 0
            last_stream_key = ""
            last_write_at = time.time()
            while True:
                if self._client_connection_closed():
                    return
                frame = self.runtime.get_node_frame(node_id)
                stream_key = str((frame or {}).get("streamName") or (frame or {}).get("streamKey") or "")
                if stream_key != last_stream_key:
                    last_seq = 0
                    last_stream_key = stream_key
                stream_frame = self._read_stream_frame(frame or {})
                if stream_frame is not None and int(stream_frame["seq"]) > last_seq:
                    payload = stream_frame["data"]
                    self.wfile.write(STREAM_CHUNK_HEADER.pack(
                        STREAM_CHUNK_MAGIC,
                        int(stream_frame["seq"]),
                        int(stream_frame["width"]),
                        int(stream_frame["height"]),
                        int(stream_frame["encodingCode"]),
                        len(payload),
                        int(stream_frame["sourceWidth"] or stream_frame["width"]),
                        int(stream_frame["sourceHeight"] or stream_frame["height"]),
                    ))
                    self.wfile.write(payload)
                    self.wfile.flush()
                    last_seq = int(stream_frame["seq"])
                    last_write_at = time.time()
                elif time.time() - last_write_at > 2.0:
                    return
                time.sleep(1.0 / max(1.0, GRAPH_RUN_HZ))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _client_connection_closed(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except Exception:
            return True

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

    ROBOT_MESH_EXTENSIONS = {".stl", ".dae", ".obj", ".ply", ".glb", ".gltf"}

    def _send_robot_mesh(self, path: str):
        target = Path(unquote(path or "")).expanduser().resolve()
        # Only serve mesh file types; this endpoint must not become an
        # arbitrary file read.
        if target.suffix.lower() not in self.ROBOT_MESH_EXTENSIONS:
            self.send_error(403)
            return
        if not target.is_file():
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
    parser.add_argument("--lwrclpy-wheel", default="", help="Use this local lwrclpy .whl for the server and every node venv.")
    args = parser.parse_args(argv)
    try:
        if args.lwrclpy_wheel:
            configure_local_lwrclpy_wheel(args.lwrclpy_wheel)
        _install_local_lwrclpy_for_server()
        Handler.runtime.close()
        Handler.runtime = GraphRuntime()
        Handler.runner = ContinuousGraphRunner(Handler.runtime)
    except Exception as exc:
        print(f"Failed to configure local lwrclpy wheel: {exc}", file=sys.stderr)
        return 1
    startup_cleanup = cleanup_framework_processes(force=True)
    killed_count = len(startup_cleanup.get("killed", []))
    if killed_count:
        print(f"Cleaned up {killed_count} stale lwrclpy Web Node Editor worker process(es).")
    lock_path, lock_acquired = _acquire_server_lock(args.host, args.port)
    if not lock_acquired:
        print(
            f"Another lwrclpy Web Node Editor server is already running (lock: {lock_path}). "
            f"Stop it first before starting a new instance.",
            file=sys.stderr,
        )
        return 1
    atexit.register(_cleanup_at_exit)
    atexit.register(_release_server_lock, lock_path)
    try:
        server = ReusableThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
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
        _release_server_lock(lock_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
