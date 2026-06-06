from __future__ import annotations

import argparse
import io
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


os.environ.setdefault("LWRCLPY_NO_DATASHARING", "1")

RUNNING = True


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
            depth=4,
            reliability=qos.ReliabilityPolicy.RELIABLE,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return 1


def _set_field(msg: Any, key: str, value: Any) -> None:
    field = getattr(msg, key, None)
    if callable(field):
        try:
            field(value)
            return
        except TypeError:
            pass
    setattr(msg, key, value)


def _populate_image_message(msg: Any, width: int, height: int, rgb: bytes) -> None:
    fields = {
        "width": width,
        "height": height,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": width * 3,
        "data": rgb,
    }
    for key, value in fields.items():
        if hasattr(msg, key):
            _set_field(msg, key, value)


def _populate_compressed_image_message(msg: Any, width: int, height: int, jpeg: bytes) -> None:
    if hasattr(msg, "format"):
        _set_field(msg, "format", f"jpeg; width={width}; height={height}")
    if hasattr(msg, "data"):
        _set_field(msg, "data", jpeg)


def _coerce_image(type_name: str, width: int, height: int, frame: bytes, output_encoding: str) -> Any:
    msg = _import_type_class(type_name)()
    if str(type_name).endswith("/CompressedImage") or output_encoding == "jpeg":
        _populate_compressed_image_message(msg, width, height, frame)
    else:
        _populate_image_message(msg, width, height, frame)
    return msg


def _publish_frame(publisher: Any, type_name: str, width: int, height: int, frame: bytes, output_encoding: str) -> None:
    publisher.publish(_coerce_image(type_name, width, height, frame, output_encoding))


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


class PreviewFrameWriter:
    def __init__(self, frame_path: Path, status_path: Path, width: int, height: int, source_encoding: str, preview_encoding: str = "jpeg") -> None:
        self.frame_path = frame_path
        self.status_path = status_path
        self.width = width
        self.height = height
        self.source_encoding = source_encoding
        self.preview_encoding = preview_encoding
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
                data = self._preview_payload(frame)
                tmp_frame_path = self.frame_path.with_name(f"{self.frame_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
                tmp_frame_path.write_bytes(data)
                tmp_frame_path.replace(self.frame_path)
                frame_status = {
                    "frameSeq": seq,
                    "framePath": str(self.frame_path),
                    "frameEncoding": self.preview_encoding if self.source_encoding != "jpeg" else "jpeg",
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

    def _preview_payload(self, frame: bytes) -> bytes:
        if self.source_encoding == "jpeg":
            return frame
        if self.preview_encoding == "jpeg":
            try:
                from PIL import Image

                image = Image.frombytes("RGB", (self.width, self.height), frame)
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=75, subsampling=1)
                return out.getvalue()
            except Exception:
                pass
        return _bmp_bytes(self.width, self.height, frame)


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


def _ffmpeg_executable() -> str:
    env_path = os.environ.get("FFMPEG_BINARY")
    if env_path:
        return env_path
    direct = shutil.which("ffmpeg")
    if direct:
        return direct
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg not found. Install requirements.txt to provide imageio-ffmpeg.") from exc


def _probe(path: Path, ffmpeg: str) -> tuple[int, int, float]:
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    text = f"{result.stderr}\n{result.stdout}"
    stream_line = next((line for line in text.splitlines() if " Video:" in line), "")
    size_match = re.search(r"(\d{2,5})x(\d{2,5})", stream_line)
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stream_line)
    width = int(size_match.group(1)) if size_match else 0
    height = int(size_match.group(2)) if size_match else 0
    fps = float(fps_match.group(1)) if fps_match else 30.0
    if width <= 0 or height <= 0:
        raise RuntimeError("ffmpeg could not read video dimensions")
    return width, height, fps


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


def _ffmpeg_cmd(ffmpeg: str, path: Path, width: int, height: int, publish_hz: float, output_encoding: str) -> list[str]:
    base = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-i",
        str(path),
        "-vf",
        f"fps={publish_hz:g},scale={width}:{height}",
        "-an",
    ]
    if output_encoding == "jpeg":
        return [
            *base,
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "5",
            "pipe:1",
        ]
    return [
        *base,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    video_path = Path(config["videoPath"])
    topic = str(config["topic"])
    type_name = str(config.get("dataType") or "sensor_msgs/msg/Image")
    enable_dds_publish = bool(config.get("enableDdsPublish", False))
    use_source_fps = bool(config.get("useSourceFps", False))
    publish_hz = max(0.01, float(config.get("publishHz") or 30.0))
    output_encoding = str(config.get("outputEncoding") or "raw").lower()
    if output_encoding not in {"raw", "jpeg"}:
        output_encoding = "raw"
    loop = bool(config.get("loop", True))
    max_side = int(config.get("maxSide") or 640)
    preview_hz = max(0.0, float(config.get("previewHz") if config.get("previewHz") is not None else 30.0))
    preview_encoding = str(config.get("previewEncoding") or "jpeg").lower()
    if preview_encoding not in {"jpeg", "bmp"}:
        preview_encoding = "jpeg"
    status_path = Path(config.get("statusPath") or (str(config_path) + ".status"))
    frame_path = Path(config.get("framePath") or (str(config_path) + ".frame"))
    data_sharing_disabled = os.environ.get("LWRCLPY_NO_DATASHARING") == "1"

    try:
        _write_status(status_path, running=True, phase="probe", error="", videoPath=str(video_path), dataSharingDisabled=data_sharing_disabled)
        ffmpeg = _ffmpeg_executable()
        src_w, src_h, src_fps = _probe(video_path, ffmpeg)
        width, height = _scaled_size(src_w, src_h, max_side)
        if use_source_fps and src_fps > 0:
            publish_hz = max(0.01, src_fps)
    except Exception as exc:
        _write_status(status_path, running=False, error=f"video probe failed: {exc}")
        return 2

    rclpy = None
    node = None
    publisher = None
    if enable_dds_publish:
        _write_status(status_path, running=True, phase="dds_init", error="", videoPath=str(video_path), dataSharingDisabled=data_sharing_disabled)
        import rclpy as _rclpy

        rclpy = _rclpy
        if not rclpy.ok():
            rclpy.init(args=None)
        _write_status(status_path, running=True, phase="create_node", error="", videoPath=str(video_path), dataSharingDisabled=data_sharing_disabled)
        node = rclpy.create_node(f"ipn_video_dds_{config.get('nodeId', 'video')}".replace("-", "_")[:80])
        _write_status(status_path, running=True, phase="create_publisher", error="", videoPath=str(video_path), dataSharingDisabled=data_sharing_disabled)
        publisher = node.create_publisher(_import_type_class(type_name), topic, _topic_qos(type_name))
    frame_size = width * height * 3
    count = 0
    started = time.time()
    ended = False
    next_preview_at = 0.0
    next_status_at = 0.0
    frame_encoding = "jpeg" if output_encoding == "jpeg" else preview_encoding
    preview_writer = PreviewFrameWriter(frame_path, status_path, width, height, output_encoding, frame_encoding) if preview_hz > 0 else None
    _write_status(
        status_path,
        running=True,
        error="",
        width=width,
        height=height,
        encoding=output_encoding,
        sourceFps=src_fps,
        published=0,
        matchedSubscriptions=_matched_subscriptions(publisher),
        dataSharingDisabled=data_sharing_disabled,
    )
    if publisher is not None:
        discovery_deadline = time.time() + 3.0
        while RUNNING and time.time() < discovery_deadline and _matched_subscriptions(publisher) <= 0:
            time.sleep(0.05)

    try:
        while RUNNING:
            proc = subprocess.Popen(_ffmpeg_cmd(ffmpeg, video_path, width, height, publish_hz, output_encoding), stdout=subprocess.PIPE)
            assert proc.stdout is not None
            jpeg_buffer = bytearray()
            while RUNNING:
                if output_encoding == "jpeg":
                    eoi = jpeg_buffer.find(b"\xff\xd9")
                    if eoi < 0:
                        chunk = proc.stdout.read(65536)
                        if not chunk:
                            break
                        jpeg_buffer.extend(chunk)
                        eoi = jpeg_buffer.find(b"\xff\xd9")
                    if eoi < 0:
                        continue
                    frame = bytes(jpeg_buffer[:eoi + 2])
                    del jpeg_buffer[:eoi + 2]
                else:
                    frame = proc.stdout.read(frame_size)
                    if len(frame) != frame_size:
                        break
                count += 1
                if publisher is not None:
                    _publish_frame(publisher, type_name, width, height, frame, output_encoding)
                now = time.time()
                if preview_writer is not None and now >= next_preview_at:
                    elapsed = max(0.001, now - started)
                    preview_writer.submit(
                        count,
                        frame,
                        {
                            "running": True,
                            "error": "",
                            "width": width,
                            "height": height,
                            "encoding": output_encoding,
                            "sourceFps": src_fps,
                            "published": count,
                            "actualHz": count / elapsed,
                            "matchedSubscriptions": _matched_subscriptions(publisher),
                            "dataSharingDisabled": data_sharing_disabled,
                        },
                    )
                    next_preview_at = now + (1.0 / preview_hz)
                if now >= next_status_at:
                    elapsed = max(0.001, now - started)
                    _write_status(
                        status_path,
                        running=True,
                        error="",
                        width=width,
                        height=height,
                        encoding=output_encoding,
                        sourceFps=src_fps,
                        published=count,
                        actualHz=count / elapsed,
                        matchedSubscriptions=_matched_subscriptions(publisher),
                        dataSharingDisabled=data_sharing_disabled,
                        **(preview_writer.status_snapshot() if preview_writer is not None else {}),
                    )
                    next_status_at = now + 0.25
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()
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
            encoding=output_encoding,
            sourceFps=src_fps,
            published=count,
            dataSharingDisabled=data_sharing_disabled,
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
