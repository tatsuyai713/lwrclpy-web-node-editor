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
from pathlib import Path
from typing import Any

RUNNING = True
EXTERNAL_FASTDDS_TRANSPORTS = "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=false"


def _configure_fastdds_transport(config: dict[str, Any]) -> None:
    if config.get("externalDdsCompatible"):
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = EXTERNAL_FASTDDS_TRANSPORTS
    else:
        os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "LARGE_DATA")


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


def _topic_qos(data_type: str) -> Any:
    if str(data_type).replace(".", "/") not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
        return 10
    try:
        import rclpy.qos as qos

        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=64,
            reliability=qos.ReliabilityPolicy.RELIABLE,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return 64


def _set_field(msg: Any, key: str, value: Any) -> None:
    field = getattr(msg, key, None)
    if callable(field):
        try:
            field(value)
            return
        except TypeError:
            pass
    setattr(msg, key, value)


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
                current = getattr(msg, key, None)
                if isinstance(item, dict) and current is not None and not callable(current) and hasattr(current, "__dict__"):
                    _populate_message(current, item)
                else:
                    _set_field(msg, key, item)
    elif hasattr(msg, "data"):
        _set_field(msg, "data", value)


def _coerce_message(type_name: str, value: Any) -> Any:
    msg = _import_type_class(type_name)()
    _populate_message(msg, _plain_value(value))
    return msg


def _write_status(path: Path, **values: Any) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps({"time": time.time(), **values}, ensure_ascii=False, default=str), encoding="utf-8")
    tmp_path.replace(path)


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
    frame_index = int(elapsed * fps)
    shift = frame_index % max(1, width)
    band = (frame_index * 3) % max(1, width + height)
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
    return {
        "width": width,
        "height": height,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": width * 3,
        "data": bytes(output),
        "dataEncoding": "bytes",
    }, elapsed, ended


def _signal_value(params: dict[str, Any], elapsed: float, rng: random.Random) -> float:
    signal_type = str(params.get("signalType") or "sine")
    amplitude = float(params.get("amplitude") if params.get("amplitude") is not None else 1.0)
    bias = float(params.get("bias") if params.get("bias") is not None else 0.0)
    frequency = float(params.get("frequency") if params.get("frequency") is not None else 1.0)
    phase = float(params.get("phase") if params.get("phase") is not None else 0.0)
    if signal_type == "step":
        step_time = float(params.get("stepTime") if params.get("stepTime") is not None else 1.0)
        return float(params.get("finalValue") if elapsed >= step_time else params.get("initialValue") or 0.0)
    if signal_type == "square":
        duty = max(0.0, min(100.0, float(params.get("dutyCycle") if params.get("dutyCycle") is not None else 50.0))) / 100.0
        pos = ((elapsed * frequency) + phase / 360.0) % 1.0
        return bias + (amplitude if pos < duty else -amplitude)
    if signal_type == "ramp":
        return bias + float(params.get("rampSlope") if params.get("rampSlope") is not None else 1.0) * elapsed
    if signal_type == "chirp":
        start = float(params.get("chirpStartFrequency") if params.get("chirpStartFrequency") is not None else 0.1)
        end = float(params.get("chirpEndFrequency") if params.get("chirpEndFrequency") is not None else 10.0)
        duration = max(0.001, float(params.get("chirpDuration") if params.get("chirpDuration") is not None else 10.0))
        k = (end - start) / duration
        angle = 2.0 * math.pi * (start * elapsed + 0.5 * k * min(elapsed, duration) ** 2) + math.radians(phase)
        return bias + amplitude * math.sin(angle)
    if signal_type == "white_noise":
        mean = float(params.get("noiseMean") if params.get("noiseMean") is not None else 0.0)
        std = float(params.get("noiseStd") if params.get("noiseStd") is not None else 1.0)
        return bias + mean + rng.gauss(0.0, std)
    angle = 2.0 * math.pi * frequency * elapsed + math.radians(phase)
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
            _write_status(status_path, running=True, published=count, status=f"{params.get('signalType', 'sine')} {publish_hz:g}Hz published t={elapsed:.3f}s y={last_value:.5g}")
            next_at += 1.0 / publish_hz if next_at else now + 1.0 / publish_hz
        time.sleep(max(0.0, min(0.002, next_at - time.time())))


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
        _write_status(
            status_path,
            running=not ended,
            published=count,
            ended=ended,
            status=f"{params.get('fileName') or 'embedded video'} {elapsed:.2f}/{duration:.2f}s @ {publish_hz:g}Hz",
        )
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
                    _write_status(
                        status_path,
                        running=True,
                        phase="first_publish" if count == 0 else "",
                        published=count,
                        status=f"starting: publishing first {item_topic}" if count == 0 else f"publishing {item_topic}",
                        currentFile=str(file_path),
                        fileIndex=file_index,
                        fileCount=len(mcap_files),
                    )
                    publishers[port_id].publish(_coerce_message(data_types_by_port[port_id], plain_msg))
                    count += 1
                    if count == 1:
                        _write_status(
                            status_path,
                            running=True,
                            published=count,
                            status=f"publishing {item_topic}",
                            currentFile=str(file_path),
                            fileIndex=file_index,
                            fileCount=len(mcap_files),
                        )
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
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    _configure_fastdds_transport(config)
    import rclpy

    if not rclpy.ok():
        rclpy.init(args=None)
    node = rclpy.create_node(f"ipn_builtin_source_{config.get('nodeId', 'source')}".replace("-", "_")[:80])
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
                    _topic_qos(data_type),
                )
            _run_mcap_input(config, publishers)
        else:
            data_type = str(config["dataType"])
            publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type))
            if config.get("toolType") == "function_generator":
                _run_function_generator(config, publisher)
            elif config.get("toolType") == "image_file_input":
                _run_image_input(config, publisher)
            elif config.get("toolType") == "video_file_input":
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
