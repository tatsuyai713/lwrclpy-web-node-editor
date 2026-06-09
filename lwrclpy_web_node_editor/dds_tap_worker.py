from __future__ import annotations

import argparse
import base64
import io
import json
import os
import signal
import threading
import time
import traceback
from pathlib import Path
from typing import Any

RUNNING = True
GUI_DISPLAY_HZ = 30.0
PREVIEW_JPEG_QUALITY = 60
PREVIEW_JPEG_SUBSAMPLING = 2
PREVIEW_MAX_SIDE = 640
RAW_PREVIEW_ENCODINGS = {"rgb8", "bgr8", "mono8", "8uc1"}
EXTERNAL_FASTDDS_TRANSPORTS = "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=false"


def _configure_fastdds_transport(config: dict[str, Any]) -> None:
    if config.get("externalDdsCompatible"):
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = EXTERNAL_FASTDDS_TRANSPORTS
    else:
        os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "LARGE_DATA")


def _stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def _spin_executor(executor: Any) -> None:
    while RUNNING:
        try:
            executor.spin_once(timeout_sec=0.01)
        except Exception:
            traceback.print_exc()
            time.sleep(0.01)


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


def _dds_format_label(data_type: str, encoding: str) -> str:
    normalized = str(data_type or "").replace(".", "/")
    source_encoding = str(encoding or "").lower()
    if normalized == "sensor_msgs/msg/CompressedImage":
        return f"CompressedImage/{source_encoding or 'compressed'}"
    if normalized == "sensor_msgs/msg/Image":
        return f"Image/{source_encoding or 'raw'}"
    if source_encoding:
        return f"{normalized or 'topic'}/{source_encoding}"
    return normalized or "topic"


def _field(value: Any, key: str) -> Any:
    field = getattr(value, key, None)
    if callable(field):
        try:
            return field()
        except TypeError:
            return field
    return field


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("status write returned 0 bytes")
            view = view[written:]
    finally:
        os.close(fd)
    os.replace(tmp_path, path)


def _decimate_series(series: list[dict[str, float]], limit: int) -> list[dict[str, float]]:
    if len(series) <= limit:
        return series
    step = len(series) / max(1, limit)
    sampled = [series[min(len(series) - 1, int(index * step))] for index in range(limit)]
    if sampled[-1] is not series[-1]:
        sampled.append(series[-1])
    return sampled


def _write_bytes(path: Path, payload: Any) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    tmp_path.write_bytes(payload)
    tmp_path.replace(path)


def _bytes_payload(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return value
    try:
        return memoryview(value)
    except TypeError:
        return bytes(value)


def _bytes_field(msg: Any, key: str) -> Any:
    helper = getattr(msg, f"_lwrclpy_{key}_memoryview", None)
    if callable(helper):
        try:
            return memoryview(helper())
        except Exception:
            pass
    helper = getattr(msg, f"_lwrclpy_{key}_bytes", None)
    if callable(helper):
        try:
            return helper()
        except Exception:
            pass
    return _field(msg, key)


class DdsTap:
    def __init__(self, config: dict[str, Any]) -> None:
        self.mode = str(config.get("mode") or "hz")
        self.data_type = str(config["dataType"])
        self.topic = str(config["topic"])
        self.status_path = Path(config["statusPath"])
        self.frame_path = Path(config.get("framePath") or (str(self.status_path) + ".frame"))
        self.window_sec = max(0.5, float(config.get("windowSec") or 5.0))
        self.display_hz = max(0.1, min(float(config.get("displayHz") or GUI_DISPLAY_HZ), 60.0))
        self.preview_encoding = str(config.get("previewEncoding") or "raw").lower()
        self.preview_max_side = max(0, int(config.get("previewMaxSide") or PREVIEW_MAX_SIDE))
        self.field_path = str(config.get("fieldPath") or "data")
        self.sample_limit = max(8, min(int(config.get("sampleLimit") or 10000), 100000))
        self.graph_window_sec = max(0.1, float(config.get("graphWindowSec") or 10.0))
        self.graph_display_limit = max(64, min(int(config.get("graphDisplayLimit") or 600), 2000))
        self.output_dir = Path(config.get("outputDir") or "saved_images")
        self._lock = threading.Lock()
        self._times: list[float] = []
        self._series: list[dict[str, float]] = []
        self._graph_recent_points: list[dict[str, float]] = []
        self._graph_reset_key = f"{os.getpid()}:{time.time_ns()}"
        self._graph_transfer_limit = max(256, min(self.sample_limit, 2048))
        self._latest_msg: Any = None
        self._latest_seq = 0
        self._written_seq = 0
        self._last_saved_seq = 0
        self._latest_frame_status: dict[str, Any] = {}
        self._frame_condition = threading.Condition()
        self._frame_item: tuple[int, Any] | None = None
        self._frame_writer_thread: threading.Thread | None = None
        self._frame_writer_stop = False
        self.subscription: Any = None
        self._matched_publishers_count = 0
        configured_transport = str(config.get("transport") or "").lower()
        if self.mode == "graph":
            self.transport = "polling"
        elif configured_transport in {"callback", "polling"}:
            self.transport = configured_transport
        else:
            self.transport = "callback"
        self._last_error = ""

    def poll_batch_size(self) -> int:
        if self.data_type.replace(".", "/") in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
            return 4
        return 64

    def callback(self, msg: Any) -> None:
        try:
            now = time.time() if self.mode == "graph" else time.perf_counter()
            if self.mode == "graph":
                value = _extract_number(msg, self.field_path)
                if value is not None:
                    self._record_graph_value(now, value)
                return
            with self._lock:
                self._times.append(now)
                del self._times[:-10000]
                if self.mode in {"image", "save"}:
                    self._latest_msg = msg
                    self._latest_seq += 1
        except Exception as exc:
            self._record_error("callback", exc)

    def poll(self, max_count: int = 128) -> int:
        subscription = self.subscription
        if subscription is None:
            return 0
        try:
            samples = subscription.take(max_count)
        except Exception:
            return 0
        received = 0
        for item in samples:
            try:
                msg = item[0] if isinstance(item, tuple) else item
            except Exception:
                msg = item
            if msg is None:
                continue
            self.callback(msg)
            received += 1
        return received

    def run_status_loop(self) -> None:
        next_at = 0.0
        next_frame_at = 0.0
        status_hz = self.display_hz
        status_period = (1.0 / status_hz) if self.mode in {"graph", "hz"} else 0.25
        while RUNNING:
            try:
                now = time.time() if self.mode == "graph" else time.perf_counter()
                if now >= next_at:
                    self._write_status(now)
                    next_at = now + status_period
                if self.mode == "image" and now >= next_frame_at:
                    self._write_latest_frame()
                    next_frame_at = now + (1.0 / self.display_hz)
                if self.mode == "save":
                    self._save_latest_image()
                time.sleep(0.002)
            except Exception as exc:
                self._record_error("status_loop", exc)
                time.sleep(0.05)
        try:
            self._write_status(time.time() if self.mode == "graph" else time.perf_counter(), running=False)
        except Exception as exc:
            self._record_error("status_stop", exc)

    def _write_status(self, now: float, running: bool = True) -> None:
        times, seq = self._current_times(now)
        count = len(times)
        hz = 0.0
        if count >= 2:
            duration = max(0.001, times[-1] - times[0])
            hz = (count - 1) / duration
        payload = {
            "time": time.time(),
            "running": running,
            "mode": self.mode,
            "topic": self.topic,
            "dataType": self.data_type,
            "subscribed": True,
            "count": count,
            "hz": hz,
            "windowSec": self.window_sec,
            "frameSeq": seq,
            "framePath": str(self.frame_path),
            "matchedPublishers": self._matched_publishers_cached(),
            "polling": self.transport == "polling",
            "transport": self.transport,
        }
        if self._last_error:
            payload["lastError"] = self._last_error
        with self._lock:
            frame_status = dict(self._latest_frame_status)
        if frame_status:
            payload.update(frame_status)
        if self.mode == "graph":
            with self._lock:
                points = list(self._graph_recent_points)
            payload["fieldPath"] = self.field_path
            payload["resetKey"] = self._graph_reset_key
            payload["points"] = points
            if points:
                payload["lastSampleAgeSec"] = max(0.0, time.time() - points[-1]["t"])
        _write_json(self.status_path, payload)

    def _record_graph_value(self, timestamp: float, value: float) -> None:
        with self._lock:
            self._latest_seq += 1
            self._times.append(timestamp)
            del self._times[:-10000]
            self._graph_recent_points.append({"seq": self._latest_seq, "t": timestamp, "y": value})
            if len(self._graph_recent_points) > self._graph_transfer_limit:
                self._graph_recent_points = self._graph_recent_points[-self._graph_transfer_limit:]

    def _record_error(self, stage: str, exc: BaseException) -> None:
        self._last_error = f"{stage}: {exc}"
        traceback.print_exc()
        try:
            _write_json(
                self.status_path,
                {
                    "time": time.time(),
                    "running": RUNNING,
                    "mode": self.mode,
                    "topic": self.topic,
                    "dataType": self.data_type,
                    "subscribed": self.subscription is not None,
                    "error": self._last_error,
                    "traceback": traceback.format_exc()[-4000:],
                    "matchedPublishers": self._matched_publishers_cached(),
                    "polling": self.transport == "polling",
                    "transport": self.transport,
                },
            )
        except Exception:
            traceback.print_exc()

    def _matched_publishers_cached(self) -> int:
        return int(self._matched_publishers_count)

    def _current_times(self, now: float) -> tuple[list[float], int]:
        cutoff = now - self.window_sec
        with self._lock:
            self._times = [t for t in self._times if t >= cutoff]
            return list(self._times), self._latest_seq

    def _write_latest_frame(self) -> None:
        self._ensure_frame_writer()
        with self._lock:
            if self._latest_msg is None or self._latest_seq == self._written_seq:
                return
            msg = self._latest_msg
            seq = self._latest_seq
            self._written_seq = seq
        with self._frame_condition:
            self._frame_item = (seq, msg)
            self._frame_condition.notify()

    def _ensure_frame_writer(self) -> None:
        if self._frame_writer_thread is not None:
            return
        with self._frame_condition:
            if self._frame_writer_thread is not None:
                return
            self._frame_writer_thread = threading.Thread(target=self._frame_writer_loop, name="dds-tap-frame-writer", daemon=True)
            self._frame_writer_thread.start()

    def stop_frame_writer(self) -> None:
        with self._frame_condition:
            self._frame_writer_stop = True
            self._frame_condition.notify()
        thread = self._frame_writer_thread
        if thread is not None:
            thread.join(timeout=1.0)

    def _frame_writer_loop(self) -> None:
        while True:
            with self._frame_condition:
                while self._frame_item is None and not self._frame_writer_stop:
                    self._frame_condition.wait()
                if self._frame_writer_stop and self._frame_item is None:
                    return
                item = self._frame_item
                self._frame_item = None
            if item is None:
                continue
            seq, msg = item
            self._write_frame_payload(seq, msg)

    def _write_frame_payload(self, seq: int, msg: Any) -> None:
        frame = self._frame_payload(msg)
        if frame is None:
            return
        data, status = frame
        dds_encoding = str(status.get("encoding") or "rgb8").lower()
        frame_encoding = dds_encoding
        source_width = int(status.get("width") or 0)
        source_height = int(status.get("height") or 0)
        preview_width = source_width
        preview_height = source_height
        if dds_encoding in RAW_PREVIEW_ENCODINGS:
            if self.preview_encoding == "raw":
                frame_encoding = dds_encoding
                if source_width > 0 and source_height > 0 and self.preview_max_side > 0 and max(source_width, source_height) > self.preview_max_side:
                    data, frame_encoding, preview_width, preview_height = _raw_preview_image_bytes(
                        source_width,
                        source_height,
                        _rgb_preview_bytes(data, dds_encoding),
                        self.preview_max_side,
                    )
            elif self.preview_encoding == "bmp" and source_width > 0 and source_height > 0:
                data, frame_encoding, preview_width, preview_height = _bmp_preview_image_bytes(
                    source_width,
                    source_height,
                    _rgb_preview_bytes(data, dds_encoding),
                    self.preview_max_side,
                )
            elif source_width > 0 and source_height > 0:
                data, frame_encoding, preview_width, preview_height = _preview_image_bytes(source_width, source_height, _rgb_preview_bytes(data, dds_encoding))
        dds_format = _dds_format_label(str(status.get("dataType") or self.data_type), dds_encoding)
        _write_bytes(self.frame_path, data)
        with self._lock:
            self._latest_frame_status = {
                "encoding": dds_encoding,
                "ddsEncoding": dds_encoding,
                "frameEncoding": frame_encoding,
                "dataType": status.get("dataType"),
                "ddsFormat": dds_format,
                "width": source_width,
                "height": source_height,
                "previewWidth": preview_width,
                "previewHeight": preview_height,
                "frameSeq": seq,
                "framePath": str(self.frame_path),
            }
        now = time.time()
        now_counter = time.perf_counter()
        times, _ = self._current_times(now_counter)
        count = len(times)
        hz = 0.0
        if count >= 2:
            duration = max(0.001, times[-1] - times[0])
            hz = (count - 1) / duration
        status.update({
            "encoding": dds_encoding,
            "ddsEncoding": dds_encoding,
            "frameEncoding": frame_encoding,
            "ddsFormat": dds_format,
            "previewWidth": preview_width,
            "previewHeight": preview_height,
            "frameSeq": seq,
            "framePath": str(self.frame_path),
            "time": now,
            "running": True,
            "subscribed": True,
            "count": count,
            "hz": hz,
            "windowSec": self.window_sec,
            "matchedPublishers": self._matched_publishers_cached(),
            "polling": self.transport == "polling",
            "transport": self.transport,
        })
        _write_json(self.status_path, status)

    def _save_latest_image(self) -> None:
        with self._lock:
            if self._latest_msg is None or self._latest_seq == self._last_saved_seq:
                return
            msg = self._latest_msg
            seq = self._latest_seq
        frame = self._frame_payload(msg)
        if frame is None:
            return
        data, status = frame
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"image_save_{int(time.time() * 1000)}.bmp"
        if status.get("encoding") in {"jpeg", "jpg"}:
            path = path.with_suffix(".jpg")
            path.write_bytes(data)
        else:
            width = int(status.get("width") or 0)
            height = int(status.get("height") or 0)
            if width <= 0 or height <= 0:
                return
            path.write_bytes(_bmp_bytes(width, height, data))
        self._last_saved_seq = seq
        _write_json(
            self.status_path,
            {
                "time": time.time(),
                "running": True,
                "mode": self.mode,
                "savedPath": str(path),
                "frameSeq": seq,
                "matchedPublishers": self._matched_publishers_cached(),
                "polling": self.transport == "polling",
                "transport": self.transport,
            },
        )

    def _frame_payload(self, msg: Any) -> tuple[bytes, dict[str, Any]] | None:
        if self.data_type.replace(".", "/") == "sensor_msgs/msg/CompressedImage":
            data = _bytes_field(msg, "data")
            if data is None:
                return None
            return _bytes_payload(data), {
                "mode": self.mode,
                "topic": self.topic,
                "dataType": self.data_type,
                "encoding": "jpeg",
                "width": _format_int(str(_field(msg, "format") or ""), "width") or 0,
                "height": _format_int(str(_field(msg, "format") or ""), "height") or 0,
            }
        width = int(_field(msg, "width") or 0)
        height = int(_field(msg, "height") or 0)
        encoding = str(_field(msg, "encoding") or "rgb8").lower()
        data = _bytes_field(msg, "data")
        if width <= 0 or height <= 0 or data is None:
            return None
        return _bytes_payload(data), {
            "mode": self.mode,
            "topic": self.topic,
            "dataType": self.data_type,
            "encoding": encoding,
            "width": width,
            "height": height,
        }


def _format_int(format_text: str, key: str) -> int | None:
    marker = f"{key}="
    for part in format_text.replace(",", ";").split(";"):
        part = part.strip()
        if not part.startswith(marker):
            continue
        try:
            return int(float(part[len(marker):].strip()))
        except Exception:
            return None
    return None


def _extract_number(value: Any, path: str) -> float | None:
    current = value
    for part in [item for item in path.replace("/", ".").split(".") if item]:
        current = _field(current, part)
    try:
        return float(current)
    except Exception:
        return None


def _bmp_bytes(width: int, height: int, rgb: Any) -> bytes:
    if not isinstance(rgb, bytes):
        rgb = bytes(rgb)
    row_stride = ((width * 3 + 3) // 4) * 4
    row_size = width * 3
    bgr_flat = bytearray(len(rgb))
    bgr_flat[0::3] = rgb[2::3]
    bgr_flat[1::3] = rgb[1::3]
    bgr_flat[2::3] = rgb[0::3]
    if row_stride == row_size:
        pixel_bytes = bytes(bgr_flat)
    else:
        padding_size = row_stride - row_size
        pixel_buffer = bytearray(row_stride * height)
        for y in range(height):
            src_start = y * row_size
            dst_start = y * row_stride
            pixel_buffer[dst_start:dst_start + row_size] = bgr_flat[src_start:src_start + row_size]
            if padding_size:
                pixel_buffer[dst_start + row_size:dst_start + row_stride] = b"\x00" * padding_size
        pixel_bytes = bytes(pixel_buffer)
    file_size = 14 + 40 + len(pixel_bytes)
    return b"".join([
        b"BM",
        file_size.to_bytes(4, "little"),
        (0).to_bytes(4, "little"),
        (54).to_bytes(4, "little"),
        (40).to_bytes(4, "little"),
        width.to_bytes(4, "little", signed=True),
        (-height).to_bytes(4, "little", signed=True),
        (1).to_bytes(2, "little"),
        (24).to_bytes(2, "little"),
        (0).to_bytes(4, "little"),
        len(pixel_bytes).to_bytes(4, "little"),
        (2835).to_bytes(4, "little", signed=True),
        (2835).to_bytes(4, "little", signed=True),
        (0).to_bytes(4, "little"),
        (0).to_bytes(4, "little"),
        pixel_bytes,
    ])


def _rgb_preview_bytes(data: Any, encoding: str) -> bytes:
    raw = data if isinstance(data, bytes) else bytes(data)
    encoding = encoding.lower()
    if encoding == "rgb8":
        return raw
    if encoding == "bgr8":
        rgb = bytearray(len(raw))
        rgb[0::3] = raw[2::3]
        rgb[1::3] = raw[1::3]
        rgb[2::3] = raw[0::3]
        return bytes(rgb)
    if encoding in {"mono8", "8uc1"}:
        rgb = bytearray(len(raw) * 3)
        rgb[0::3] = raw
        rgb[1::3] = raw
        rgb[2::3] = raw
        return bytes(rgb)
    return raw


def _preview_image_bytes(width: int, height: int, rgb: bytes) -> tuple[bytes, str, int, int]:
    try:
        from PIL import Image

        image = Image.frombytes("RGB", (width, height), rgb)
        if PREVIEW_MAX_SIDE > 0 and max(width, height) > PREVIEW_MAX_SIDE:
            scale = PREVIEW_MAX_SIDE / max(width, height)
            preview_width = max(1, int(round(width * scale)))
            preview_height = max(1, int(round(height * scale)))
            image = image.resize((preview_width, preview_height), Image.Resampling.BILINEAR)
        else:
            preview_width = width
            preview_height = height
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=PREVIEW_JPEG_QUALITY, subsampling=PREVIEW_JPEG_SUBSAMPLING)
        return out.getvalue(), "jpeg", preview_width, preview_height
    except Exception:
        return _bmp_bytes(width, height, rgb), "bmp", width, height


def _raw_preview_image_bytes(width: int, height: int, rgb: bytes, max_side: int) -> tuple[bytes, str, int, int]:
    preview_width, preview_height = _scaled_preview_size(width, height, max_side)
    if preview_width == width and preview_height == height:
        return rgb, "rgb8", width, height
    resized = _resize_rgb_bytes(width, height, rgb, preview_width, preview_height)
    if resized is None:
        return rgb, "rgb8", width, height
    return resized, "rgb8", preview_width, preview_height


def _bmp_preview_image_bytes(width: int, height: int, rgb: bytes, max_side: int) -> tuple[bytes, str, int, int]:
    preview_width, preview_height = _scaled_preview_size(width, height, max_side)
    if preview_width != width or preview_height != height:
        resized = _resize_rgb_bytes(width, height, rgb, preview_width, preview_height)
        if resized is not None:
            rgb = resized
        else:
            preview_width, preview_height = width, height
    return _bmp_bytes(preview_width, preview_height, rgb), "bmp", preview_width, preview_height


def _resize_rgb_bytes(width: int, height: int, rgb: bytes, preview_width: int, preview_height: int) -> bytes | None:
    try:
        from PIL import Image

        image = Image.frombytes("RGB", (width, height), rgb)
        image = image.resize((preview_width, preview_height), Image.Resampling.BILINEAR)
        return image.tobytes()
    except Exception:
        pass
    try:
        import cv2
        import numpy as np

        array = np.frombuffer(rgb, dtype=np.uint8).reshape((height, width, 3))
        resized = cv2.resize(array, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
        return resized.tobytes()
    except Exception:
        pass
    try:
        source = memoryview(rgb)
        output = bytearray(preview_width * preview_height * 3)
        for y in range(preview_height):
            src_y = min(height - 1, int(y * height / preview_height))
            src_row = src_y * width * 3
            dst_row = y * preview_width * 3
            for x in range(preview_width):
                src = src_row + min(width - 1, int(x * width / preview_width)) * 3
                dst = dst_row + x * 3
                output[dst:dst + 3] = source[src:src + 3]
        return bytes(output)
    except Exception:
        return None


def _scaled_preview_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    if max_side <= 0 or max(width, height) <= max_side:
        return width, height
    scale = max_side / max(width, height)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _configure_fastdds_transport(config)
    tap = DdsTap(config)
    _write_json(tap.status_path, {"running": True, "mode": tap.mode, "topic": tap.topic, "dataType": tap.data_type, "hz": 0.0, "count": 0})

    import rclpy

    if not rclpy.ok():
        rclpy.init(args=None)
    node = rclpy.create_node(f"ipn_dds_tap_{config.get('nodeId', 'tap')}".replace("-", "_")[:80])
    callback = None if tap.transport == "polling" else tap.callback
    subscription = node.create_subscription(_import_type_class(tap.data_type), tap.topic, callback, _topic_qos(tap.data_type))
    tap.subscription = subscription
    if subscription is None:
        _write_json(tap.status_path, {"running": False, "error": "failed to create DDS subscription", "mode": tap.mode, "topic": tap.topic, "dataType": tap.data_type})
        return 2
    executor = None
    executor_thread = None
    if tap.transport != "polling":
        try:
            from rclpy.executors import MultiThreadedExecutor

            executor = MultiThreadedExecutor(num_threads=1)
            executor.add_node(node)
            executor_thread = threading.Thread(target=_spin_executor, args=(executor,), name="dds-tap-executor", daemon=True)
            executor_thread.start()
        except Exception:
            executor = None
            executor_thread = None
    _write_json(
        tap.status_path,
        {
            "running": True,
            "mode": tap.mode,
            "topic": tap.topic,
            "dataType": tap.data_type,
            "hz": 0.0,
            "count": 0,
            "subscribed": True,
            "polling": tap.transport == "polling",
            "transport": tap.transport,
        },
    )
    status_thread = threading.Thread(target=tap.run_status_loop, name="dds-tap-status", daemon=True)
    status_thread.start()
    try:
        while RUNNING:
            if tap.transport == "polling" or executor is None:
                tap.poll(tap.poll_batch_size())
            time.sleep(0.001)
    finally:
        tap.stop_frame_writer()
        status_thread.join(timeout=1.0)
        if executor is not None:
            try:
                executor.remove_node(node)
            except Exception:
                pass
            try:
                executor.shutdown(timeout_sec=0.2)
            except Exception:
                pass
        if executor_thread is not None and executor_thread.is_alive():
            executor_thread.join(timeout=0.2)
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
