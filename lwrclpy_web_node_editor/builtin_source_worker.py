from __future__ import annotations

import argparse
import base64
import traceback
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

RUNNING = True
EXTERNAL_FASTDDS_TRANSPORTS = os.environ.get(
    "LWRCLPY_WEB_FASTDDS_TRANSPORTS",
    "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=true",
)


def _configure_fastdds_transport(config: dict[str, Any]) -> None:
    if config.get("externalDdsCompatible"):
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = EXTERNAL_FASTDDS_TRANSPORTS
    else:
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = os.environ.get(
            "LWRCLPY_WEB_INTERNAL_FASTDDS_TRANSPORTS",
            "UDPv4",
        )


def _disable_lwrclpy_side_channels() -> None:
    if os.environ.get("LWRCLPY_WEB_ENABLE_LWRCLPY_SIDE_CHANNELS") == "1":
        return
    try:
        import lwrclpy.node as lwrclpy_node

        node_cls = getattr(lwrclpy_node, "Node", None)
        if node_cls is None:
            return
        for name in (
            "_configure_cuda_ipc_publisher",
            "_configure_cuda_ipc_subscription",
            "_configure_shared_memory_publisher",
            "_configure_shared_memory_subscription",
        ):
            if hasattr(node_cls, name):
                setattr(node_cls, name, lambda self, *args, **kwargs: None)
    except Exception:
        pass


def _dependency_site_dir() -> Path:
    root = Path.cwd() / ".app_settings"
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
        import mcap_ros2  # noqa: F401
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
    import mcap_ros2  # noqa: F401
    import yaml  # noqa: F401


def _stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def _import_type_class(type_name: str):
    package, kind, name = [part for part in str(type_name).split("/") if part]
    module = __import__(f"{package}.{kind}", fromlist=[name])
    return getattr(module, name)


def _sanitize_node_name(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip())
    if not text:
        return "lwrclpy_node"
    if text[0].isdigit():
        text = f"node_{text}"
    return text


def _topic_qos(data_type: str, external: bool = False, topic: str = "") -> Any:
    normalized = str(data_type).replace(".", "/")
    topic_name = str(topic or "")
    if normalized == "tf2_msgs/msg/TFMessage" and topic_name.rstrip("/") == "/tf_static":
        try:
            import rclpy.qos as qos

            return qos.QoSProfile(
                history=qos.HistoryPolicy.KEEP_LAST,
                depth=1,
                reliability=qos.ReliabilityPolicy.RELIABLE,
                durability=qos.DurabilityPolicy.TRANSIENT_LOCAL,
            )
        except Exception:
            return 1
    if str(data_type).replace(".", "/") not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
        return 10
    try:
        import rclpy.qos as qos

        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=qos.ReliabilityPolicy.BEST_EFFORT,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return 5


def _set_field(msg: Any, key: str, value: Any) -> None:
    field = getattr(msg, key, None)
    if callable(field):
        try:
            field(value)
            return
        except TypeError:
            pass
    setattr(msg, key, value)


def _get_field(msg: Any, key: str) -> Any:
    field = getattr(msg, key, None)
    if callable(field):
        try:
            return field()
        except TypeError:
            return field
    return field


def _plain_value(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray, int, float, bool)) or value is None:
        return bytes(value) if isinstance(value, bytearray) else value
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    slots = getattr(value, "__slots__", None)
    if slots:
        return {
            str(name).lstrip("_"): _plain_value(getattr(value, name))
            for name in slots
            if hasattr(value, name)
        }
    fields = getattr(value, "_fields_and_field_types", None)
    if isinstance(fields, dict):
        return {
            str(name): _plain_value(getattr(value, name))
            for name in fields
            if hasattr(value, name)
        }
    if hasattr(value, "__dict__"):
        return {
            str(key).lstrip("_"): _plain_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("__")
        }
    return value


def _populate_message(msg: Any, value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if hasattr(msg, key):
                current = _get_field(msg, key)
                if isinstance(item, dict) and current is not None and not callable(current):
                    _populate_message(current, item)
                else:
                    _set_field(msg, key, item)
    elif hasattr(msg, "data"):
        _set_field(msg, "data", value)


def _coerce_message(type_name: str, value: Any) -> Any:
    msg = _import_type_class(type_name)()
    _populate_message(msg, _plain_value(value))
    return msg


def _set_time_stamp(stamp: Any, value: float) -> None:
    sec = int(value)
    nanosec = int((value - sec) * 1_000_000_000)
    if stamp is not None:
        _set_field(stamp, "sec", sec)
        _set_field(stamp, "nanosec", nanosec)


def _quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _float_triplet(value: str | None, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if not value:
        return default
    parts = str(value).replace(",", " ").split()
    if len(parts) != 3:
        return default
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return default


def _load_urdf_or_xacro(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xacro":
        try:
            import xacro  # type: ignore

            return xacro.process_file(str(path)).toxml()
        except Exception:
            pass
        result = subprocess.run(["xacro", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(text or "xacro failed")
        return result.stdout
    return path.read_text(encoding="utf-8")


def _urdf_fixed_transforms(path: Path) -> list[dict[str, Any]]:
    root = ET.fromstring(_load_urdf_or_xacro(path))
    transforms: list[dict[str, Any]] = []
    for joint in root.findall("joint"):
        if str(joint.attrib.get("type") or "") != "fixed":
            continue
        parent = joint.find("parent")
        child = joint.find("child")
        parent_link = str(parent.attrib.get("link") or "") if parent is not None else ""
        child_link = str(child.attrib.get("link") or "") if child is not None else ""
        if not parent_link or not child_link:
            continue
        origin = joint.find("origin")
        xyz = _float_triplet(origin.attrib.get("xyz") if origin is not None else None, (0.0, 0.0, 0.0))
        rpy = _float_triplet(origin.attrib.get("rpy") if origin is not None else None, (0.0, 0.0, 0.0))
        qx, qy, qz, qw = _quaternion_from_rpy(*rpy)
        transforms.append({
            "parent": parent_link,
            "child": child_link,
            "translation": xyz,
            "rotation": (qx, qy, qz, qw),
        })
    return transforms


def _transform_stamped_messages(transforms: list[dict[str, Any]]) -> list[Any]:
    transform_cls = _import_type_class("geometry_msgs/msg/TransformStamped")
    stamp_time = time.time()
    messages = []
    for item in transforms:
        msg = transform_cls()
        header = _get_field(msg, "header")
        if header is not None:
            _set_time_stamp(_get_field(header, "stamp"), stamp_time)
            _set_field(header, "frame_id", str(item["parent"]))
        _set_field(msg, "child_frame_id", str(item["child"]))
        transform = _get_field(msg, "transform")
        if transform is not None:
            translation = _get_field(transform, "translation")
            rotation = _get_field(transform, "rotation")
            tx, ty, tz = item["translation"]
            qx, qy, qz, qw = item["rotation"]
            if translation is not None:
                _set_field(translation, "x", float(tx))
                _set_field(translation, "y", float(ty))
                _set_field(translation, "z", float(tz))
            if rotation is not None:
                _set_field(rotation, "x", float(qx))
                _set_field(rotation, "y", float(qy))
                _set_field(rotation, "z", float(qz))
                _set_field(rotation, "w", float(qw))
        messages.append(msg)
    return messages


def _tf_message(transforms: list[dict[str, Any]]) -> Any:
    tf_msg = _import_type_class("tf2_msgs/msg/TFMessage")()
    messages = _transform_stamped_messages(transforms)
    _set_field(tf_msg, "transforms", messages)
    return tf_msg


def _write_status(path: Path, **values: Any) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps({"time": time.time(), **values}, ensure_ascii=False, default=str), encoding="utf-8")
    tmp_path.replace(path)


def _matched_subscriptions(publisher: Any) -> int:
    if publisher is None:
        return 0
    try:
        return int(publisher.get_subscription_count())
    except Exception:
        return 0


def _wait_for_expected_subscriptions(config: dict[str, Any], publishers: dict[str, Any], status_path: Path) -> None:
    expected_by_output = config.get("expectedSubscriptionsByOutput")
    if not isinstance(expected_by_output, dict):
        expected_total = max(0, int(config.get("expectedSubscriptions") or 0))
        if expected_total <= 0:
            return
        expected_by_output = {next(iter(publishers), "out1"): expected_total}
    expected = {
        str(output_id): max(0, int(count or 0))
        for output_id, count in expected_by_output.items()
        if max(0, int(count or 0)) > 0 and str(output_id) in publishers
    }
    if not expected:
        return
    timeout_sec = max(0.0, float(config.get("discoveryTimeoutSec") or 3.0))
    deadline = time.time() + timeout_sec
    last_status = 0.0
    while RUNNING and time.time() < deadline:
        matched = {output_id: _matched_subscriptions(publishers[output_id]) for output_id in expected}
        if all(matched[output_id] >= count for output_id, count in expected.items()):
            return
        now = time.time()
        if now - last_status >= 0.25:
            _write_status(
                status_path,
                running=True,
                phase="waiting_subscribers",
                published=0,
                matchedSubscriptions=sum(matched.values()),
                expectedSubscriptions=sum(expected.values()),
                matchedSubscriptionsByOutput=matched,
                expectedSubscriptionsByOutput=expected,
                status="waiting for DDS subscribers",
            )
            last_status = now
        time.sleep(0.05)
    matched = {output_id: _matched_subscriptions(publishers[output_id]) for output_id in expected}
    if any(matched[output_id] < count for output_id, count in expected.items()):
        _write_status(
            status_path,
            running=True,
            phase="subscriber_wait_timeout",
            warning=f"only {sum(matched.values())}/{sum(expected.values())} subscriptions matched before publishing",
            published=0,
            matchedSubscriptions=sum(matched.values()),
            expectedSubscriptions=sum(expected.values()),
            matchedSubscriptionsByOutput=matched,
            expectedSubscriptionsByOutput=expected,
            status="subscriber wait timeout; publishing anyway",
        )


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


def _resolve_mcap_input_files(path: Path) -> tuple[Path, list[Path]]:
    selected = path.expanduser()
    if selected.is_file() and selected.name in {"metadata.yaml", "metadata.yml", "metadata.json"}:
        selected = selected.parent
    if selected.is_file():
        if selected.suffix.lower() != ".mcap":
            raise RuntimeError(f"selected file is not an .mcap file: {selected}")
        return selected, [selected]
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
    return selected, files


def _normalize_image(image: dict[str, Any]) -> dict[str, Any]:
    data = image.get("data")
    if isinstance(data, str) and image.get("dataEncoding") == "base64":
        image = dict(image)
        image["data"] = base64.b64decode(data)
        image["dataEncoding"] = "bytes"
    return image


def _image_rgb_bytes(image: dict[str, Any], width: int, height: int) -> bytes:
    data = image.get("data")
    if isinstance(data, str) and image.get("dataEncoding") == "base64":
        data = base64.b64decode(data)
    elif isinstance(data, list):
        data = bytes(max(0, min(255, int(item))) for item in data)
    elif data is None:
        data = b""
    else:
        data = bytes(data)
    expected = max(0, width * height * 3)
    if expected <= 0:
        return b""
    if len(data) >= expected:
        return data[:expected]
    return data + (b"\x00" * (expected - len(data)))


def _synthetic_video_frame(base_frame: dict[str, Any], params: dict[str, Any], started: float, now: float) -> tuple[dict[str, Any], float, bool]:
    base = _normalize_image(dict(base_frame))
    width = int(base.get("width") or 0)
    height = int(base.get("height") or 0)
    source = _image_rgb_bytes(base, width, height)
    duration = max(0.1, float(params.get("duration") or 10.0))
    fps = max(1.0, float(params.get("embeddedFps") or params.get("publishHz") or 30.0))
    loop = bool(params.get("loop", True))
    elapsed = max(0.0, now - started)
    ended = False
    if elapsed >= duration:
        if loop:
            elapsed = elapsed % duration
        else:
            elapsed = duration
            ended = True
    if width <= 0 or height <= 0 or not source:
        return base, elapsed, ended
    start_frame = max(0, int(float(params.get("startFrame") or 0)))
    frame_skip = max(0, int(float(params.get("frameSkip") or 0)))
    frame_index = start_frame + int(elapsed * fps) * (frame_skip + 1)
    shift = frame_index % max(1, width)
    band = (frame_index * 3) % max(1, width + height)
    return {
        "width": width,
        "height": height,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": width * 3,
        "data": _render_synthetic_frame(width, height, source, shift, band),
        "dataEncoding": "bytes",
    }, elapsed, ended


def _render_synthetic_frame(width: int, height: int, source: bytes, shift: int, band: int) -> bytes:
    try:
        import numpy as np

        array = np.frombuffer(source, dtype=np.uint8).reshape((height, width, 3)).astype(np.int16)
        array = np.roll(array, -shift, axis=1)
        xs = np.arange(width, dtype=np.int32)
        ys = np.arange(height, dtype=np.int32).reshape(-1, 1)
        highlight = ((np.abs((xs + ys) - band) < 4).astype(np.int16)) * 34
        array[:, :, 0] += highlight
        array[:, :, 1] += highlight // 2
        return np.clip(array, 0, 255).astype(np.uint8).tobytes()
    except Exception:
        pass
    output = bytearray(width * height * 3)
    for y in range(height):
        row = y * width
        for x in range(width):
            src_x = (x + shift) % width
            src = (row + src_x) * 3
            dst = (row + x) * 3
            highlight = 34 if abs((x + y) - band) < 4 else 0
            output[dst] = max(0, min(255, int(source[src]) + highlight))
            output[dst + 1] = max(0, min(255, int(source[src + 1]) + highlight // 2))
            output[dst + 2] = max(0, min(255, int(source[src + 2])))
    return bytes(output)


def _signal_value(params: dict[str, Any], elapsed: float, rng: random.Random) -> float:
    # Keep this in sync with graph.py _function_generator_raw_value: the UI
    # exposes "Phase rad", so phase is interpreted as radians here too.
    signal_type = str(params.get("signalType") or "sine")
    amplitude = float(params.get("amplitude") if params.get("amplitude") is not None else 1.0)
    bias = float(params.get("bias") if params.get("bias") is not None else 0.0)
    frequency = float(params.get("frequency") if params.get("frequency") is not None else 1.0)
    phase = float(params.get("phase") if params.get("phase") is not None else 0.0)
    if signal_type == "step":
        step_time = max(0.0, float(params.get("stepTime") if params.get("stepTime") is not None else 1.0))
        initial = float(params.get("initialValue") if params.get("initialValue") is not None else 0.0)
        final = float(params.get("finalValue") if params.get("finalValue") is not None else 0.0)
        return final if elapsed >= step_time else initial
    if signal_type == "square":
        duty = max(0.0, min(100.0, float(params.get("dutyCycle") if params.get("dutyCycle") is not None else 50.0))) / 100.0
        if frequency <= 0:
            return bias + amplitude
        pos = ((elapsed * frequency) + phase / (2.0 * math.pi)) % 1.0
        return bias + (amplitude if pos < duty else -amplitude)
    if signal_type == "ramp":
        return bias + float(params.get("rampSlope") if params.get("rampSlope") is not None else 1.0) * elapsed
    if signal_type == "chirp":
        start = float(params.get("chirpStartFrequency") if params.get("chirpStartFrequency") is not None else 0.1)
        end = float(params.get("chirpEndFrequency") if params.get("chirpEndFrequency") is not None else 10.0)
        duration = max(0.001, float(params.get("chirpDuration") if params.get("chirpDuration") is not None else 10.0))
        k = (end - start) / duration
        t = min(elapsed, duration)
        angle = 2.0 * math.pi * (start * t + 0.5 * k * t * t) + phase
        return bias + amplitude * math.sin(angle)
    if signal_type == "white_noise":
        mean = float(params.get("noiseMean") if params.get("noiseMean") is not None else 0.0)
        std = max(0.0, float(params.get("noiseStd") if params.get("noiseStd") is not None else 1.0))
        return bias + rng.gauss(mean, std)
    angle = 2.0 * math.pi * frequency * elapsed + phase
    return bias + amplitude * math.sin(angle)


def _run_function_generator(config: dict[str, Any], publisher: Any) -> None:
    params = config.get("params") or {}
    data_type = str(config.get("dataType") or "std_msgs/msg/Float32")
    status_path = Path(config["statusPath"])
    publish_hz = max(0.01, float(params.get("publishHz") or 10.0))
    sample_time = max(0.0, float(params.get("sampleTime") or 0.0))
    rng = random.Random(int(params.get("noiseSeed") or 1))
    started = time.time()
    next_at = 0.0
    last_sample_at = 0.0
    last_value = 0.0
    count = 0
    next_status_at = 0.0
    _write_status(
        status_path,
        running=True,
        phase="first_publish",
        published=0,
        status=f"starting: waiting for first {params.get('signalType', 'sine')} publish",
    )
    time.sleep(0.25)
    while RUNNING:
        now = time.time()
        if now >= next_at:
            elapsed = now - started
            if sample_time <= 0 or now - last_sample_at >= sample_time:
                last_value = _signal_value(params, elapsed, rng)
                last_sample_at = now
            if count == 0:
                _write_status(
                    status_path,
                    running=True,
                    phase="first_publish",
                    published=0,
                    status=f"starting: publishing first {params.get('signalType', 'sine')} sample",
                )
            publisher.publish(_coerce_message(data_type, {"data": float(last_value)}))
            count += 1
            # Throttle status-file writes: at high publish rates a write per
            # publish becomes disk-bound and drags the publish timing.
            if count == 1 or now >= next_status_at:
                _write_status(status_path, running=True, published=count, status=f"{params.get('signalType', 'sine')} {publish_hz:g}Hz published t={elapsed:.3f}s y={last_value:.5g}")
                next_status_at = now + 0.2
            next_at += 1.0 / publish_hz if next_at else now + 1.0 / publish_hz
        time.sleep(max(0.0, min(0.002, next_at - time.time())))


def _run_interactive_text_input(config_path: Path, config: dict[str, Any], publisher: Any) -> None:
    status_path = Path(config["statusPath"])
    data_type = str(config.get("dataType") or "std_msgs/msg/String")
    last_seq = 0
    count = 0
    last_mtime = 0.0
    cached_config = config
    _write_status(status_path, running=True, published=0, status="waiting for text input")
    while RUNNING:
        try:
            stat = config_path.stat()
            if stat.st_mtime != last_mtime:
                last_mtime = stat.st_mtime
                cached_config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            cached_config = config
        params = cached_config.get("params") if isinstance(cached_config.get("params"), dict) else {}
        messages = params.get("messages") if isinstance(params.get("messages"), list) else []
        for item in messages:
            if not isinstance(item, dict):
                continue
            try:
                seq = int(item.get("seq") or 0)
            except Exception:
                seq = 0
            if seq <= last_seq:
                continue
            text = str(item.get("text") or "")
            last_seq = max(last_seq, seq)
            if not text:
                continue
            publisher.publish(_coerce_message(data_type, {"data": text}))
            count += 1
            _write_status(status_path, running=True, published=count, lastSeq=last_seq, status=f"published text message {count}")
        time.sleep(0.03)


def _run_urdf_static_tf(config: dict[str, Any], node: Any) -> None:
    params = config.get("params") or {}
    status_path = Path(config["statusPath"])
    urdf_path = Path(str(params.get("urdfPath") or "")).expanduser()
    if not urdf_path.is_file():
        _write_status(status_path, running=False, error=f"URDF/Xacro file not found: {urdf_path}", status="No URDF/Xacro selected")
        return
    transforms = _urdf_fixed_transforms(urdf_path)
    transform_messages = _transform_stamped_messages(transforms)
    from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

    broadcaster = StaticTransformBroadcaster(node)
    count = 0
    while RUNNING:
        broadcaster.sendTransform(transform_messages)
        count += 1
        _write_status(
            status_path,
            running=True,
            published=count,
            transformCount=len(transforms),
            status=f"broadcast {len(transforms)} static transforms from {urdf_path.name}",
        )
        if count >= 3:
            break
        time.sleep(0.25)
    while RUNNING:
        time.sleep(0.2)


def _run_image_input(config: dict[str, Any], publisher: Any) -> None:
    params = config.get("params") or {}
    data_type = str(config.get("dataType") or "sensor_msgs/msg/Image")
    status_path = Path(config["statusPath"])
    image = params.get("imageMessage")
    if not isinstance(image, dict):
        _write_status(status_path, running=False, status="No image selected")
        return
    image = _normalize_image(image)
    mode = str(params.get("publishMode") or "oneshot")
    publish_hz = max(0.01, float(params.get("publishHz") or 1.0))
    count = 0
    time.sleep(0.25)
    while RUNNING:
        publisher.publish(_coerce_message(data_type, image))
        count += 1
        _write_status(status_path, running=True, published=count, status=f"{image.get('width', '?')} x {image.get('height', '?')} / {mode}")
        if mode != "rate":
            for _ in range(9):
                if not RUNNING:
                    break
                time.sleep(0.1)
                publisher.publish(_coerce_message(data_type, image))
                count += 1
                _write_status(status_path, running=True, published=count, status=f"{image.get('width', '?')} x {image.get('height', '?')} / {mode}")
            while RUNNING:
                time.sleep(0.1)
            break
        time.sleep(1.0 / publish_hz)


def _run_video_input(config: dict[str, Any], publisher: Any) -> None:
    params = config.get("params") or {}
    data_type = str(config.get("dataType") or "sensor_msgs/msg/Image")
    status_path = Path(config["statusPath"])
    base = params.get("baseFrameMessage") or params.get("frameMessage") or params.get("imageMessage")
    if not isinstance(base, dict):
        _write_status(status_path, running=False, status="No video frame selected")
        return
    publish_hz = max(0.01, float(params.get("publishHz") or params.get("embeddedFps") or 30.0))
    duration = max(0.1, float(params.get("duration") or 10.0))
    loop = bool(params.get("loop", True))
    started = time.time()
    next_at = 0.0
    count = 0
    next_status_at = 0.0
    time.sleep(0.25)
    ended = False
    while RUNNING:
        now = time.time()
        if now < next_at:
            time.sleep(max(0.0, min(0.002, next_at - now)))
            continue
        frame, elapsed, ended = _synthetic_video_frame(base, params, started, now)
        publisher.publish(_coerce_message(data_type, frame))
        count += 1
        if count == 1 or ended or now >= next_status_at:
            start_frame = max(0, int(float(params.get("startFrame") or 0)))
            frame_skip = max(0, int(float(params.get("frameSkip") or 0)))
            current_frame = start_frame + max(0, count - 1) * (frame_skip + 1)
            frame_count = max(0, int(float(params.get("frameCount") or 0)))
            frame_text = f" / frame {current_frame + 1}/{frame_count}" if frame_count > 0 else f" / frame {current_frame + 1}"
            _write_status(
                status_path,
                running=not ended,
                published=count,
                ended=ended,
                currentFrame=current_frame,
                totalFrames=frame_count,
                startFrame=start_frame,
                status=f"{params.get('fileName') or 'embedded video'} {elapsed:.2f}/{duration:.2f}s{frame_text} @ {publish_hz:g}Hz",
            )
            next_status_at = now + 0.2
        if ended and not loop:
            break
        next_at = next_at + (1.0 / publish_hz) if next_at else now + (1.0 / publish_hz)


def _run_mcap_input(config: dict[str, Any], publishers: dict[str, Any]) -> None:
    params = config.get("params") or {}
    status_path = Path(config["statusPath"])
    mcap_path = Path(str(params.get("mcapPath") or "")).expanduser()
    try:
        display_path, mcap_files = _resolve_mcap_input_files(mcap_path)
    except Exception as exc:
        _write_status(status_path, running=False, status=str(exc))
        return
    outputs = [item for item in config.get("outputs", []) if isinstance(item, dict)]
    topics_by_port = {
        str(item.get("id")): str(item.get("mcapTopic") or item.get("name") or "")
        for item in outputs
        if str(item.get("id") or "") in publishers
    }
    data_types_by_port = {
        str(item.get("id")): str(item.get("dataType") or "")
        for item in outputs
        if str(item.get("id") or "") in publishers
    }
    ports_by_topic: dict[str, list[str]] = {}
    for port_id, topic in topics_by_port.items():
        if topic:
            ports_by_topic.setdefault(topic, []).append(port_id)
    if not ports_by_topic:
        _write_status(status_path, running=False, status="MCAP node has no connected outputs")
        return

    _write_status(status_path, running=True, phase="dependencies", published=0, status="starting: loading MCAP dependencies")
    _ensure_mcap_dependencies()
    from mcap_ros2.reader import read_ros2_messages

    _write_status(status_path, running=True, phase="open", published=0, status=f"starting: opening {display_path.name} ({len(mcap_files)} file{'s' if len(mcap_files) != 1 else ''})")
    playback_rate = max(0.001, float(params.get("playbackRate") or 1.0))
    loop = bool(params.get("loop", False))
    selected_topics = sorted(ports_by_topic)
    count = 0
    time.sleep(0.25)
    while RUNNING:
        first_log_time: int | None = None
        wall_start = time.monotonic()
        played_any = False
        _write_status(
            status_path,
            running=True,
            phase="read" if count == 0 else "",
            published=count,
            status=(
                f"starting: reading {display_path.name} topics={', '.join(selected_topics)}"
                if count == 0
                else f"reading {display_path.name} topics={', '.join(selected_topics)}"
            ),
            fileCount=len(mcap_files),
        )
        for file_index, file_path in enumerate(mcap_files, start=1):
            if not RUNNING:
                break
            _write_status(
                status_path,
                running=True,
                phase="read" if count == 0 else "",
                published=count,
                status=f"reading {file_path.name} ({file_index}/{len(mcap_files)})",
                currentFile=str(file_path),
                fileIndex=file_index,
                fileCount=len(mcap_files),
            )
            for item in read_ros2_messages(str(file_path), topics=selected_topics, log_time_order=True):
                if not RUNNING:
                    break
                log_time = int(getattr(item, "log_time_ns", 0) or getattr(getattr(item, "message", None), "log_time", 0) or 0)
                if first_log_time is None:
                    first_log_time = log_time
                    wall_start = time.monotonic()
                wait_sec = ((log_time - first_log_time) / 1e9) / playback_rate
                target = wall_start + max(0.0, wait_sec)
                while RUNNING:
                    remaining = target - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.01, remaining))
                if not RUNNING:
                    break
                plain_msg = _plain_value(item.ros_msg)
                item_topic = str(getattr(item.channel, "topic", ""))
                for port_id in ports_by_topic.get(item_topic, []):
                    # Do not write the status file per published message: that
                    # disk I/O throttles MCAP playback throughput. Status is
                    # written on the first publish and then periodically below.
                    if count == 0:
                        _write_status(
                            status_path,
                            running=True,
                            phase="first_publish",
                            published=count,
                            status=f"starting: publishing first {item_topic}",
                            currentFile=str(file_path),
                            fileIndex=file_index,
                            fileCount=len(mcap_files),
                        )
                    publishers[port_id].publish(_coerce_message(data_types_by_port[port_id], plain_msg))
                    count += 1
                played_any = True
                if count == 1 or count % 30 == 0:
                    elapsed = ((log_time - first_log_time) / 1e9) if first_log_time is not None else 0.0
                    _write_status(
                        status_path,
                        running=True,
                        published=count,
                        status=f"{display_path.name} {elapsed:.2f}s x{playback_rate:g} / {count} messages ({file_index}/{len(mcap_files)})",
                        currentFile=str(file_path),
                        fileIndex=file_index,
                        fileCount=len(mcap_files),
                    )
        if not RUNNING:
            break
        _write_status(status_path, running=loop and played_any, published=count, ended=not loop, status=f"{display_path.name} playback ended / {count} messages", fileCount=len(mcap_files))
        if not loop or not played_any:
            break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _configure_fastdds_transport(config)
    import rclpy
    _disable_lwrclpy_side_channels()

    if not rclpy.ok():
        rclpy.init(args=None)
    node = rclpy.create_node(_sanitize_node_name(f"ipn_builtin_source_{config.get('nodeId', 'source')}")[:80])
    _write_status(Path(config["statusPath"]), running=True, status="starting")
    had_error = False
    try:
        if config.get("toolType") == "mcap_file_input":
            publishers = {}
            for output in config.get("outputs", []):
                if not isinstance(output, dict):
                    continue
                topics = output.get("topics") if isinstance(output.get("topics"), list) else []
                if not topics:
                    continue
                data_type = str(output.get("dataType") or "")
                if not data_type:
                    continue
                publishers[str(output.get("id"))] = node.create_publisher(
                    _import_type_class(data_type),
                    str(topics[0]),
                    _topic_qos(data_type, bool(config.get("externalDdsCompatible")), str(topics[0])),
                )
            _wait_for_expected_subscriptions(config, publishers, Path(config["statusPath"]))
            _run_mcap_input(config, publishers)
        else:
            data_type = str(config["dataType"])
            if config.get("toolType") == "function_generator":
                _write_status(Path(config["statusPath"]), running=True, status="creating publisher")
                publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type, bool(config.get("externalDdsCompatible")), str(config["topic"])))
                _write_status(Path(config["statusPath"]), running=True, status="publisher ready")
                _wait_for_expected_subscriptions(config, {"out1": publisher}, Path(config["statusPath"]))
                _run_function_generator(config, publisher)
            elif config.get("toolType") == "interactive_text_input":
                _write_status(Path(config["statusPath"]), running=True, status="creating publisher")
                publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type, bool(config.get("externalDdsCompatible")), str(config["topic"])))
                _write_status(Path(config["statusPath"]), running=True, status="publisher ready")
                _wait_for_expected_subscriptions(config, {"out1": publisher}, Path(config["statusPath"]))
                _run_interactive_text_input(config_path, config, publisher)
            elif config.get("toolType") == "urdf_static_tf_publisher":
                _wait_for_expected_subscriptions(config, {}, Path(config["statusPath"]))
                _run_urdf_static_tf(config, node)
            elif config.get("toolType") == "image_file_input":
                _write_status(Path(config["statusPath"]), running=True, status="creating publisher")
                publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type, bool(config.get("externalDdsCompatible")), str(config["topic"])))
                _write_status(Path(config["statusPath"]), running=True, status="publisher ready")
                _wait_for_expected_subscriptions(config, {"out1": publisher}, Path(config["statusPath"]))
                _run_image_input(config, publisher)
            elif config.get("toolType") == "video_file_input":
                _write_status(Path(config["statusPath"]), running=True, status="creating publisher")
                publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type, bool(config.get("externalDdsCompatible")), str(config["topic"])))
                _write_status(Path(config["statusPath"]), running=True, status="publisher ready")
                _wait_for_expected_subscriptions(config, {"out1": publisher}, Path(config["statusPath"]))
                _run_video_input(config, publisher)
    except Exception as exc:
        had_error = True
        traceback.print_exc()
        _write_status(Path(config["statusPath"]), running=False, error=str(exc), status=f"error: {exc}")
        return 1
    finally:
        if not had_error:
            _write_status(Path(config["statusPath"]), running=False, status="stopped")
        try:
            node.destroy_node()
        except Exception:
            pass
        try:
            rclpy.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
