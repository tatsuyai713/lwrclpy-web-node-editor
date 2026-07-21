from __future__ import annotations

import argparse
import io
import json
import os
import re
import signal
import struct
import threading
import time
import traceback
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any

RUNNING = True
DEFAULT_PREVIEW_HZ = 60.0
STREAM_HEADER = struct.Struct("<4sI Q I I I I I I d")
STREAM_MAGIC = b"IPNF"
STREAM_VERSION = 1
STREAM_ENCODING_CODES = {"rgb8": 1, "bgr8": 2, "mono8": 3, "8uc1": 3, "jpeg": 10, "jpg": 10, "bmp": 11, "png": 12}
EXTERNAL_FASTDDS_TRANSPORTS = os.environ.get(
    "LWRCLPY_WEB_FASTDDS_TRANSPORTS",
    "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=true",
)


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


def _sanitize_node_name(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip())
    if not text:
        return "lwrclpy_node"
    if text[0].isdigit():
        text = f"node_{text}"
    return text


def _stamp_header(msg: Any, node: Any) -> None:
    """Best-effort: set header.stamp from the node clock (ROS 2 convention)."""
    if node is None:
        return
    try:
        header = getattr(msg, "header", None)
        if callable(header):
            header = header()
        if header is None:
            return
        stamp = node.get_clock().now().to_msg()
        try:
            header.stamp = stamp
        except Exception:
            stamp_field = getattr(header, "stamp", None)
            if callable(stamp_field):
                stamp_field(stamp)
    except Exception:
        pass


def _topic_qos(data_type: str, external: bool = False) -> Any:
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
    if key == "data" and isinstance(value, (bytes, bytearray, memoryview)):
        resize = getattr(msg, "_lwrclpy_data_resize", None)
        buffer_getter = getattr(msg, "_lwrclpy_data_memoryview", None)
        if callable(resize) and callable(buffer_getter):
            try:
                source = memoryview(value)
                resize(len(source))
                memoryview(buffer_getter())[:len(source)] = source
                return
            except Exception:
                pass
    field = getattr(msg, key, None)
    if callable(field):
        try:
            field(value)
            return
        except TypeError:
            pass
    setattr(msg, key, value)


def _populate_image_message(msg: Any, width: int, height: int, data: bytes, encoding: str = "rgb8") -> None:
    encoding = str(encoding or "rgb8").lower()
    channels = 1 if encoding in {"mono8", "8uc1"} else 3
    fields = {
        "width": width,
        "height": height,
        "encoding": encoding,
        "is_bigendian": 0,
        "step": width * channels,
        "data": data,
    }
    for key, value in fields.items():
        if hasattr(msg, key):
            _set_field(msg, key, value)


def _populate_compressed_image_message(msg: Any, width: int, height: int, jpeg: bytes) -> None:
    if hasattr(msg, "format"):
        _set_field(msg, "format", f"jpeg; width={width}; height={height}")
    if hasattr(msg, "data"):
        _set_field(msg, "data", jpeg)


def _coerce_image(type_name: str, width: int, height: int, frame: bytes, output_encoding: str, frame_encoding: str = "rgb8") -> Any:
    msg = _import_type_class(type_name)()
    if str(type_name).endswith("/CompressedImage") or output_encoding == "jpeg":
        _populate_compressed_image_message(msg, width, height, frame)
    else:
        _populate_image_message(msg, width, height, frame, frame_encoding)
    return msg


def _publish_frame(publisher: Any, type_name: str, width: int, height: int, frame: bytes, output_encoding: str, frame_encoding: str = "rgb8", node: Any = None) -> None:
    msg = _coerce_image(type_name, width, height, frame, output_encoding, frame_encoding)
    _stamp_header(msg, node)
    publisher.publish(msg)


def _matched_subscriptions(publisher: Any) -> int:
    if publisher is None:
        return 0
    try:
        return int(publisher.get_subscription_count())
    except Exception:
        return 0


def _write_status(path: Path, **values: Any) -> None:
    payload = {"time": time.time(), **values}
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _create_shared_memory(name: str, size: int) -> shared_memory.SharedMemory | None:
    try:
        try:
            stale = shared_memory.SharedMemory(name=name, create=False)
            stale.close()
            stale.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return shared_memory.SharedMemory(name=name, create=True, size=size)
    except Exception:
        traceback.print_exc()
        return None


def _write_stream_frame(memory: shared_memory.SharedMemory, seq: int, data: bytes, width: int, height: int, encoding: str, source_width: int, source_height: int) -> bool:
    payload = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if len(payload) + STREAM_HEADER.size > memory.size:
        return False
    encoding_code = STREAM_ENCODING_CODES.get(str(encoding or "").lower(), 1)
    view = memory.buf
    view[STREAM_HEADER.size:STREAM_HEADER.size + len(payload)] = payload
    STREAM_HEADER.pack_into(
        view,
        0,
        STREAM_MAGIC,
        STREAM_VERSION,
        max(0, int(seq)),
        max(0, int(width)),
        max(0, int(height)),
        max(0, int(encoding_code)),
        len(payload),
        max(0, int(source_width)),
        max(0, int(source_height)),
        time.time(),
    )
    return True


class PreviewFrameWriter:
    def __init__(self, frame_path: Path, status_path: Path, width: int, height: int, source_encoding: str, preview_encoding: str = "jpeg", preview_max_side: int = 640, stream_name: str = "", stream_size: int = 0) -> None:
        self.frame_path = frame_path
        self.status_path = status_path
        self.width = width
        self.height = height
        self.source_encoding = source_encoding
        self.preview_encoding = preview_encoding
        self.preview_max_side = max(0, int(preview_max_side or 0))
        self.stream_name = stream_name
        self.stream_size = max(0, int(stream_size or 0))
        self._stream_memory = _create_shared_memory(self.stream_name, self.stream_size) if self.stream_name and self.stream_size > STREAM_HEADER.size else None
        self._condition = threading.Condition()
        self._item: tuple[int, bytes, dict[str, Any]] | None = None
        self._latest_status: dict[str, Any] = {}
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="video-preview-writer", daemon=True)
        self._thread.start()

    def submit(self, seq: int, frame: bytes, status_values: dict[str, Any]) -> None:
        with self._condition:
            self._item = (seq, frame, dict(status_values))
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify()
        self._thread.join(timeout=1.0)
        if self._stream_memory is not None:
            try:
                self._stream_memory.close()
            except Exception:
                pass
            try:
                self._stream_memory.unlink()
            except Exception:
                pass
            self._stream_memory = None

    def status_snapshot(self) -> dict[str, Any]:
        with self._condition:
            return dict(self._latest_status)

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._item is None and not self._stopped:
                    self._condition.wait()
                if self._stopped and self._item is None:
                    return
                item = self._item
                self._item = None
            if item is None:
                continue
            seq, frame, status_values = item
            try:
                data, frame_encoding, preview_width, preview_height = self._preview_payload(frame)
                stream_written = False
                if self._stream_memory is not None:
                    stream_written = _write_stream_frame(self._stream_memory, seq, data, preview_width, preview_height, frame_encoding, self.width, self.height)
                if not stream_written:
                    tmp_frame_path = self.frame_path.with_name(f"{self.frame_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
                    tmp_frame_path.write_bytes(data)
                    tmp_frame_path.replace(self.frame_path)
                frame_status = {
                    "frameSeq": seq,
                    "framePath": "" if stream_written else str(self.frame_path),
                    "frameEncoding": frame_encoding,
                    "previewWidth": preview_width,
                    "previewHeight": preview_height,
                    "streamName": self.stream_name if stream_written else "",
                    "streamSize": self.stream_size if stream_written else 0,
                }
                with self._condition:
                    self._latest_status = frame_status
                _write_status(
                    self.status_path,
                    **status_values,
                    **frame_status,
                )
            except Exception:
                pass

    def _preview_payload(self, frame: bytes) -> tuple[bytes, str, int, int]:
        if self.source_encoding == "jpeg":
            return frame, "jpeg", self.width, self.height
        if self.preview_encoding == "raw":
            preview_width, preview_height = _scaled_preview_size(self.width, self.height, self.preview_max_side)
            if preview_width == self.width and preview_height == self.height:
                return frame, self.source_encoding, self.width, self.height
            resized = _resize_color_bytes(self.width, self.height, frame, preview_width, preview_height, self.source_encoding)
            if resized is None:
                return frame, self.source_encoding, self.width, self.height
            return resized, self.source_encoding, preview_width, preview_height
        if self.preview_encoding == "jpeg":
            try:
                from PIL import Image

                image = Image.frombytes("RGB", (self.width, self.height), _rgb_preview_bytes(frame, self.source_encoding))
                preview_width, preview_height = _scaled_preview_size(self.width, self.height, self.preview_max_side)
                if preview_width != self.width or preview_height != self.height:
                    image = image.resize((preview_width, preview_height), Image.Resampling.BILINEAR)
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=75, subsampling=1)
                return out.getvalue(), "jpeg", preview_width, preview_height
            except Exception:
                pass
        preview_width, preview_height = _scaled_preview_size(self.width, self.height, self.preview_max_side)
        rgb = _rgb_preview_bytes(frame, self.source_encoding)
        if preview_width != self.width or preview_height != self.height:
            resized = _resize_color_bytes(self.width, self.height, rgb, preview_width, preview_height, "rgb8")
            if resized is not None:
                return _bmp_bytes(preview_width, preview_height, resized), "bmp", preview_width, preview_height
        return _bmp_bytes(self.width, self.height, rgb), "bmp", self.width, self.height


def _resize_color_bytes(width: int, height: int, data: bytes, preview_width: int, preview_height: int, encoding: str = "rgb8") -> bytes | None:
    try:
        import cv2
        import numpy as np

        channels = 1 if str(encoding).lower() in {"mono8", "8uc1"} else 3
        array = np.frombuffer(data, dtype=np.uint8).reshape((height, width, channels))
        interpolation = cv2.INTER_AREA if preview_width < width or preview_height < height else cv2.INTER_LINEAR
        resized = cv2.resize(array, (preview_width, preview_height), interpolation=interpolation)
        return resized.tobytes()
    except Exception:
        pass
    try:
        from PIL import Image

        image = Image.frombytes("RGB", (width, height), _rgb_preview_bytes(data, encoding))
        image = image.resize((preview_width, preview_height), Image.Resampling.BILINEAR)
        return image.tobytes()
    except Exception:
        pass
    try:
        source = memoryview(data)
        channels = 1 if str(encoding).lower() in {"mono8", "8uc1"} else 3
        output = bytearray(preview_width * preview_height * channels)
        for y in range(preview_height):
            src_y = min(height - 1, int(y * height / preview_height))
            src_row = src_y * width * channels
            dst_row = y * preview_width * channels
            for x in range(preview_width):
                src = src_row + min(width - 1, int(x * width / preview_width)) * channels
                dst = dst_row + x * channels
                output[dst:dst + channels] = source[src:src + channels]
        return bytes(output)
    except Exception:
        return None


def _rgb_preview_bytes(data: Any, encoding: str) -> bytes:
    raw = data if isinstance(data, (bytes, bytearray, memoryview)) else bytes(data)
    encoding = str(encoding or "rgb8").lower()
    if encoding == "rgb8":
        return bytes(raw) if isinstance(raw, memoryview) else raw
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
    return bytes(raw) if isinstance(raw, memoryview) else raw


def _bmp_bytes(width: int, height: int, rgb: bytes) -> bytes:
    row_stride = ((width * 3 + 3) // 4) * 4
    row_size = width * 3
    bgr_flat = bytearray(len(rgb))
    bgr_flat[0::3] = rgb[2::3]
    bgr_flat[1::3] = rgb[1::3]
    bgr_flat[2::3] = rgb[0::3]
    if row_stride == row_size:
        pixel_bytes = bytes(bgr_flat)
    else:
        pixel_buffer = bytearray(row_stride * height)
        for y in range(height):
            src_start = y * row_size
            dst_start = y * row_stride
            pixel_buffer[dst_start:dst_start + row_size] = bgr_flat[src_start:src_start + row_size]
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


def _open_capture(path: Path) -> Any:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"OpenCV could not open video: {path}")
    return capture


def _probe(path: Path) -> tuple[int, int, float, int]:
    import cv2

    capture = _open_capture(path)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise RuntimeError("OpenCV could not read video dimensions")
    if fps <= 0 or fps >= 10000:
        fps = 30.0
    return width, height, fps, frame_count


def _seek_start_frame(capture: Any, start_frame: int) -> int:
    if start_frame <= 0:
        return 0
    try:
        import cv2

        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(start_frame)))
        return int(capture.get(cv2.CAP_PROP_POS_FRAMES) or start_frame)
    except Exception:
        return max(0, int(start_frame))


def _capture_frame_index(capture: Any, fallback: int) -> int:
    try:
        import cv2

        pos = int(capture.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        return max(0, pos - 1)
    except Exception:
        return max(0, int(fallback))


def _scaled_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    if max_side <= 0 or max(width, height) <= max_side:
        return width, height
    scale = max_side / max(width, height)
    out_w = max(2, int(round(width * scale)))
    out_h = max(2, int(round(height * scale)))
    if out_w % 2:
        out_w += 1
    if out_h % 2:
        out_h += 1
    return out_w, out_h


def _scaled_preview_size(width: int, height: int, target_width: int) -> tuple[int, int]:
    if target_width <= 0 or width <= target_width:
        return width, height
    scale = target_width / width
    return target_width, max(1, int(round(height * scale)))


def _read_bgr_frame(capture: Any, width: int, height: int) -> bytes | None:
    import cv2
    import numpy as np

    ok, bgr = capture.read()
    if not ok or bgr is None:
        return None
    if int(bgr.shape[1]) != width or int(bgr.shape[0]) != height:
        bgr = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
    if not bgr.flags["C_CONTIGUOUS"]:
        bgr = np.ascontiguousarray(bgr)
    return bgr.tobytes()


def _jpeg_from_bgr(_width: int, _height: int, bgr: bytes) -> bytes:
    try:
        import cv2
        import numpy as np

        array = np.frombuffer(bgr, dtype=np.uint8).reshape((_height, _width, 3))
        ok, encoded = cv2.imencode(".jpg", array, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ok:
            return encoded.tobytes()
    except Exception:
        pass
    return bgr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _configure_fastdds_transport(config)
    video_path = Path(config["videoPath"])
    topic = str(config["topic"])
    type_name = str(config.get("dataType") or "sensor_msgs/msg/Image")
    enable_dds_publish = bool(config.get("enableDdsPublish", False))
    use_source_fps = bool(config.get("useSourceFps", False))
    publish_hz = max(0.01, float(config.get("publishHz") or 30.0))
    expected_subscriptions = max(0, int(config.get("expectedSubscriptions") or 0))
    discovery_timeout_sec = max(0.0, float(config.get("discoveryTimeoutSec") or 3.0))
    output_encoding = str(config.get("outputEncoding") or "raw").lower()
    if output_encoding not in {"raw", "jpeg"}:
        output_encoding = "raw"
    frame_color_encoding = "jpeg" if output_encoding == "jpeg" else "bgr8"
    loop = bool(config.get("loop", True))
    try:
        frame_skip = max(0, int(float(config.get("frameSkip") or 0)))
    except Exception:
        frame_skip = 0
    try:
        start_frame = max(0, int(float(config.get("startFrame") or 0)))
    except Exception:
        start_frame = 0
    try:
        preview_hz = max(0.0, min(float(config.get("previewHz", DEFAULT_PREVIEW_HZ)), 120.0))
    except Exception:
        preview_hz = DEFAULT_PREVIEW_HZ
    preview_encoding = str(config.get("previewEncoding") or "jpeg").lower()
    if preview_encoding not in {"jpeg", "bmp", "raw"}:
        preview_encoding = "jpeg"
    preview_max_side = max(0, int(config.get("previewMaxSide") or 640))
    stream_name = str(config.get("streamName") or "")
    stream_size = max(0, int(config.get("streamSize") or 0))
    status_path = Path(config.get("statusPath") or (str(config_path) + ".status"))
    frame_path = Path(config.get("framePath") or (str(config_path) + ".frame"))

    try:
        _write_status(status_path, running=True, phase="probe", error="", videoPath=str(video_path))
        if not video_path.is_file():
            raise RuntimeError(f"video file not found: {video_path}")
        src_w, src_h, src_fps, frame_count = _probe(video_path)
        width, height = src_w, src_h
        if frame_count > 0:
            start_frame = min(start_frame, max(0, frame_count - 1))
        if use_source_fps and src_fps > 0:
            publish_hz = max(0.01, src_fps / (frame_skip + 1))
    except Exception as exc:
        _write_status(status_path, running=False, error=f"video probe failed: {exc}")
        return 2

    rclpy = None
    node = None
    publisher = None
    if enable_dds_publish:
        _write_status(status_path, running=True, phase="dds_init", error="", videoPath=str(video_path))
        import rclpy as _rclpy

        rclpy = _rclpy
        if not rclpy.ok():
            rclpy.init(args=None)
        _write_status(status_path, running=True, phase="create_node", error="", videoPath=str(video_path))
        node = rclpy.create_node(_sanitize_node_name(f"ipn_video_dds_{config.get('nodeId', 'video')}")[:80])
        _write_status(status_path, running=True, phase="create_publisher", error="", videoPath=str(video_path))
        publisher = node.create_publisher(_import_type_class(type_name), topic, _topic_qos(type_name, bool(config.get("externalDdsCompatible"))))
    count = 0
    started_perf = time.perf_counter()
    first_publish_perf: float | None = None
    last_publish_perf: float | None = None
    ended = False
    next_preview_at = 0.0
    next_status_at = 0.0
    frame_encoding = "jpeg" if output_encoding == "jpeg" else frame_color_encoding
    preview_writer = PreviewFrameWriter(frame_path, status_path, width, height, frame_encoding, "raw" if preview_encoding == "raw" else preview_encoding, preview_max_side, stream_name, stream_size) if preview_hz > 0 else None
    _write_status(
        status_path,
        running=True,
        error="",
        width=width,
        height=height,
        encoding=frame_encoding,
        sourceFps=src_fps,
        totalFrames=frame_count,
        startFrame=start_frame,
        currentFrame=start_frame,
        frameSkip=frame_skip,
        published=0,
        matchedSubscriptions=_matched_subscriptions(publisher),
        expectedSubscriptions=expected_subscriptions,
    )
    if publisher is not None and expected_subscriptions > 0:
        discovery_deadline = time.time() + discovery_timeout_sec
        last_discovery_status = 0.0
        while RUNNING and time.time() < discovery_deadline and _matched_subscriptions(publisher) < expected_subscriptions:
            now = time.time()
            if now - last_discovery_status >= 0.25:
                _write_status(
                    status_path,
                    running=True,
                    phase="waiting_subscribers",
                    error="",
                    width=width,
                    height=height,
                    encoding=frame_encoding,
                    sourceFps=src_fps,
                    frameSkip=frame_skip,
                    totalFrames=frame_count,
                    startFrame=start_frame,
                    currentFrame=start_frame,
                    published=0,
                    matchedSubscriptions=_matched_subscriptions(publisher),
                    expectedSubscriptions=expected_subscriptions,
                )
                last_discovery_status = now
            time.sleep(0.05)
        if _matched_subscriptions(publisher) < expected_subscriptions:
            _write_status(
                status_path,
                running=True,
                phase="subscriber_wait_timeout",
                warning=f"only {_matched_subscriptions(publisher)}/{expected_subscriptions} subscriptions matched before publishing",
                error="",
                width=width,
                height=height,
                encoding=frame_encoding,
                sourceFps=src_fps,
                frameSkip=frame_skip,
                totalFrames=frame_count,
                startFrame=start_frame,
                currentFrame=start_frame,
                published=0,
                matchedSubscriptions=_matched_subscriptions(publisher),
                expectedSubscriptions=expected_subscriptions,
            )
    started_perf = time.perf_counter()

    try:
        period_sec = 1.0 / publish_hz
        next_publish_at = time.perf_counter()
        while RUNNING:
            capture = _open_capture(video_path)
            current_frame = _seek_start_frame(capture, start_frame)
            try:
                while RUNNING:
                    delay = next_publish_at - time.perf_counter()
                    while RUNNING and delay > 0:
                        time.sleep(min(delay, 0.001))
                        delay = next_publish_at - time.perf_counter()
                    bgr_frame = _read_bgr_frame(capture, width, height)
                    if bgr_frame is None:
                        break
                    current_frame = _capture_frame_index(capture, start_frame + count * (frame_skip + 1))
                    frame = _jpeg_from_bgr(width, height, bgr_frame) if output_encoding == "jpeg" else bgr_frame
                    if publisher is not None:
                        _publish_frame(publisher, type_name, width, height, frame, output_encoding, frame_encoding, node)
                    published_at = time.perf_counter()
                    next_publish_at += period_sec
                    if next_publish_at < published_at:
                        next_publish_at = published_at + period_sec
                    if first_publish_perf is None:
                        first_publish_perf = published_at
                    last_publish_perf = published_at
                    count += 1
                    now = time.time()
                    if preview_writer is not None and now >= next_preview_at:
                        elapsed = max(0.001, (last_publish_perf or time.perf_counter()) - (first_publish_perf or started_perf))
                        actual_hz = ((count - 1) / elapsed) if count >= 2 else 0.0
                        preview_writer.submit(
                            count,
                            frame,
                            {
                                "running": True,
                                "error": "",
                                "width": width,
                                "height": height,
                                "encoding": frame_encoding,
                                "sourceFps": src_fps,
                                "totalFrames": frame_count,
                                "startFrame": start_frame,
                                "currentFrame": current_frame,
                                "frameSkip": frame_skip,
                                "published": count,
                                "actualHz": actual_hz,
                                "matchedSubscriptions": _matched_subscriptions(publisher),
                                "expectedSubscriptions": expected_subscriptions,
                            },
                        )
                        next_preview_at = now + (1.0 / preview_hz)
                    if now >= next_status_at:
                        elapsed = max(0.001, (last_publish_perf or time.perf_counter()) - (first_publish_perf or started_perf))
                        actual_hz = ((count - 1) / elapsed) if count >= 2 else 0.0
                        _write_status(
                            status_path,
                            running=True,
                            error="",
                            width=width,
                            height=height,
                            encoding=frame_encoding,
                            sourceFps=src_fps,
                            totalFrames=frame_count,
                            startFrame=start_frame,
                            currentFrame=current_frame,
                            frameSkip=frame_skip,
                            published=count,
                            actualHz=actual_hz,
                            matchedSubscriptions=_matched_subscriptions(publisher),
                            expectedSubscriptions=expected_subscriptions,
                            **(preview_writer.status_snapshot() if preview_writer is not None else {}),
                        )
                        status_hz = preview_hz if preview_hz > 0 else DEFAULT_PREVIEW_HZ
                        next_status_at = now + (1.0 / status_hz)
                    for _ in range(frame_skip):
                        if not capture.grab():
                            break
            finally:
                capture.release()
            if not loop:
                ended = True
                break
    except Exception as exc:
        _write_status(status_path, running=False, error=str(exc), published=count)
        return 3
    finally:
        _write_status(
            status_path,
            running=False,
            ended=ended,
            error="",
            width=width,
            height=height,
            encoding=frame_encoding,
            sourceFps=src_fps,
            totalFrames=frame_count,
            startFrame=start_frame,
            currentFrame=start_frame + max(0, count - 1) * (frame_skip + 1),
            frameSkip=frame_skip,
            published=count,
            **(preview_writer.status_snapshot() if preview_writer is not None else {}),
        )
        if preview_writer is not None:
            preview_writer.stop()
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass
        if rclpy is not None:
            try:
                rclpy.shutdown()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
