from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import signal
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


def _populate_message(msg: Any, value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if hasattr(msg, key):
                _set_field(msg, key, item)
    elif hasattr(msg, "data"):
        _set_field(msg, "data", value)


def _coerce_message(type_name: str, value: Any) -> Any:
    msg = _import_type_class(type_name)()
    _populate_message(msg, value)
    return msg


def _write_status(path: Path, **values: Any) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps({"time": time.time(), **values}, ensure_ascii=False, default=str), encoding="utf-8")
    tmp_path.replace(path)


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
    time.sleep(0.25)
    while RUNNING:
        now = time.time()
        if now >= next_at:
            elapsed = now - started
            if sample_time <= 0 or now - last_sample_at >= sample_time:
                last_value = _signal_value(params, elapsed, rng)
                last_sample_at = now
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
    data_type = str(config["dataType"])
    publisher = node.create_publisher(_import_type_class(data_type), str(config["topic"]), _topic_qos(data_type))
    _write_status(Path(config["statusPath"]), running=True, status="starting")
    try:
        if config.get("toolType") == "function_generator":
            _run_function_generator(config, publisher)
        elif config.get("toolType") == "image_file_input":
            _run_image_input(config, publisher)
        elif config.get("toolType") == "video_file_input":
            _run_video_input(config, publisher)
    finally:
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
