from __future__ import annotations

import importlib
import json
import math
import pkgutil
import base64
import hashlib
import os
import random
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def discover_lwrclpy_types() -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for module_info in pkgutil.iter_modules():
        package = module_info.name
        if not (package.endswith("_msgs") or package.endswith("_srvs")):
            continue
        for kind in ("msg", "srv"):
            try:
                module = importlib.import_module(f"{package}.{kind}")
            except Exception:
                continue
            names = sorted(
                name for name in dir(module)
                if name[:1].isupper() and not name.endswith(("_Request", "_Response"))
            )
            if names:
                result.setdefault(package, {})[kind] = names
    return dict(sorted(result.items()))


LWRCLPY_TYPE_TREE = discover_lwrclpy_types()


@dataclass
class PortConfig:
    id: str
    name: str
    data_type: str
    topic: str = ""
    topics: tuple[str, ...] = ()
    receive_mode: str = "callback"
    callback_code: str = ""


@dataclass
class TimerConfig:
    id: str
    name: str
    period_sec: float = 1.0
    callback_code: str = ""


@dataclass
class CustomLwrclNodeConfig:
    id: str
    name: str
    x: int = 0
    y: int = 0
    inputs: list[PortConfig] = field(default_factory=list)
    outputs: list[PortConfig] = field(default_factory=list)
    loop_code: str = ""
    timers: list[TimerConfig] = field(default_factory=list)
    timer_enabled: bool = False
    timer_period_sec: float = 1.0
    timer_code: str = ""
    import_code: str = ""
    requirements: str = ""
    tool_type: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def normalize_type(type_name: str) -> str:
    return type_name.replace(".", "/")


def split_type(type_name: str) -> tuple[str, str, str]:
    parts = normalize_type(type_name).split("/")
    if len(parts) != 3 or parts[1] not in {"msg", "srv"}:
        raise ValueError(f"Unsupported lwrclpy type: {type_name}")
    return parts[0], parts[1], parts[2]


def import_type_class(type_name: str):
    package, kind, name = split_type(type_name)
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


class LwrclpyRuntime:
    def __init__(self) -> None:
        self.available = False
        self.initialized = False
        self.error = ""
        self.rclpy = None
        self.executor = None
        try:
            self.rclpy = importlib.import_module("rclpy")
            self.available = True
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)

    def ensure_initialized(self) -> bool:
        if not self.available or self.rclpy is None:
            return False
        if self.initialized:
            return True
        try:
            executors = importlib.import_module("rclpy.executors")
            if not self.rclpy.ok():
                self.rclpy.init(args=None)
            self.executor = executors.MultiThreadedExecutor()
            self.initialized = True
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def spin_once(self) -> None:
        if self.executor is None:
            return
        try:
            self.executor.spin_once(timeout_sec=0.0)
        except Exception as exc:
            self.error = str(exc)

    def spin_some(self, count: int = 8) -> None:
        for _ in range(max(1, count)):
            self.spin_once()


class PreviewLogger:
    def __init__(self, log_func) -> None:
        self._log = log_func

    def info(self, value: Any) -> None:
        self._log(value)

    def warning(self, value: Any) -> None:
        self._log("warning:", value)

    def error(self, value: Any) -> None:
        self._log("error:", value)


class PreviewNode:
    def __init__(self, name: str, log_func) -> None:
        self._name = name
        self._logger = PreviewLogger(log_func)

    def get_name(self) -> str:
        return self._name

    def get_logger(self) -> PreviewLogger:
        return self._logger


class CustomLwrclNodeInstance:
    def __init__(self, config: CustomLwrclNodeConfig, runtime: LwrclpyRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.state: dict[str, Any] = {}
        self.last_inputs: dict[str, Any] = {}
        self.input_queues: dict[str, list[Any]] = {}
        self.last_outputs: dict[str, Any] = {}
        self.logs: list[str] = []
        self.view: dict[str, Any] = {}
        self._series: list[dict[str, float]] = []
        self._image_input_signatures: dict[str, tuple[Any, ...]] = {}
        self._last_saved_signature = ""
        self._next_timer_at = 0.0
        self._next_timer_by_id: dict[str, float] = {}
        self._exec_globals: dict[str, Any] | None = None
        self._import_signature = ""
        self.env_path: Path | None = None
        self.env_python_bin: Path | None = None
        self.env_site_packages: Path | None = None
        self.env_status = "pending"
        self.publishers: dict[str, list[Any]] = {}
        self.clients: dict[str, list[Any]] = {}
        self.subscriptions: list[Any] = []
        self.services: list[Any] = []
        self.lwrcl_node = None
        self.preview_node = PreviewNode(config.name, self.log)
        self.worker_process: subprocess.Popen | None = None
        self.worker_signature = None
        self.worker_config_path: Path | None = None
        self.worker_log_path: Path | None = None
        self.worker_pid_path: Path | None = None
        self.env_signature = None
        self.signature = None
        self._setup_transport()

    def update_config(self, config: CustomLwrclNodeConfig) -> None:
        if self._signature(config) != self.signature:
            self.close()
            self.config = config
            self._exec_globals = None
            self._import_signature = ""
            self._setup_transport()
        else:
            self.config = config

    def close(self) -> None:
        if self.lwrcl_node is not None and self.runtime.available:
            try:
                if self.runtime.executor is not None:
                    self.runtime.executor.remove_node(self.lwrcl_node)
                self.lwrcl_node.destroy_node()
            except Exception:
                pass
        self.lwrcl_node = None
        self.publishers = {}
        self.clients = {}
        self.subscriptions = []
        self.services = []
        self.stop_worker()

    def stop_worker(self, force: bool = False) -> bool:
        process = self.worker_process
        self.worker_process = None
        if process is None or process.poll() is not None:
            if self.worker_pid_path:
                try:
                    self.worker_pid_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return False
        self._signal_worker(process, force)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._signal_worker(process, True)
            process.wait(timeout=2)
        if self.worker_pid_path:
            try:
                self.worker_pid_path.unlink(missing_ok=True)
            except Exception:
                pass
        self.env_status = "worker stopped"
        return True

    def _signal_worker(self, process: subprocess.Popen, force: bool) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            if os.name != "nt":
                os.killpg(process.pid, sig)
            elif force:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    def tick(self, linked_inputs: dict[str, Any]) -> dict[str, Any]:
        self.runtime.spin_once()
        if self._execute_tool(linked_inputs):
            self.runtime.spin_once()
            return {
                "inputs": self._repr_values(self.last_inputs),
                "outputs": self._repr_values(self.last_outputs),
                "logs": self.logs[-20:],
                "lwrclpy": self.runtime.available,
                "environment": self.env_status,
            }
        if self.worker_process is not None:
            self.runtime.spin_once()
            if self.worker_process.poll() is not None:
                self.env_status = f"worker exited: {self.worker_process.returncode}"
            return {
                "inputs": self._repr_values(self.last_inputs),
                "outputs": self._repr_values(self.last_outputs),
                "logs": self._combined_logs(),
                "lwrclpy": self.runtime.available,
                "environment": self.env_status,
                "worker": "running" if self.worker_process.poll() is None else "stopped",
            }
        merged_inputs = dict(self.last_inputs)
        self._reset_state_on_image_shape_change(merged_inputs)
        local_outputs: dict[str, Any] = {}
        self._execute_timer_if_due(merged_inputs, local_outputs)
        self._execute_loop(merged_inputs, local_outputs)
        for key, value in local_outputs.items():
            self.last_outputs[key] = value
            self.publish(key, value)
        return {
            "inputs": self._repr_values(merged_inputs),
            "outputs": self._repr_values(self.last_outputs),
            "logs": self._combined_logs(),
            "lwrclpy": self.runtime.available,
            "environment": self.env_status,
            "worker": "running" if self.worker_process and self.worker_process.poll() is None else "stopped",
        }

    def publish(self, output_id: str, value: Any) -> None:
        self.last_outputs[output_id] = value
        if output_id in self.publishers:
            msg = self._coerce_message(self._output_type(output_id), value)
            for publisher in self.publishers[output_id]:
                publisher.publish(msg)
        elif output_id in self.clients:
            request = self._coerce_service_request(self._output_type(output_id), value)
            for client in self.clients[output_id]:
                client.call_async(request)

    def latest(self, input_id: str, default: Any = None) -> Any:
        return self.last_inputs.get(input_id, default)

    def take(self, input_id: str, default: Any = None) -> Any:
        queue = self.input_queues.get(input_id) or []
        if not queue:
            return default
        return queue.pop(0)

    def has_input(self, input_id: str) -> bool:
        return bool(self.input_queues.get(input_id))

    def log(self, *values: Any) -> None:
        self.logs.append(" ".join(str(v) for v in values))
        self.logs = self.logs[-100:]

    def _combined_logs(self) -> list[str]:
        logs = list(self.logs[-10:])
        if self.worker_log_path and self.worker_log_path.exists():
            try:
                lines = self.worker_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                logs.extend(lines[-10:])
            except Exception:
                pass
        return logs[-20:]

    def _setup_transport(self) -> None:
        self.signature = self._signature(self.config)
        if self.config.tool_type in {"topic_input", "topic_output"}:
            return
        needs_transport = any(self._port_topics(port) for port in [*self.config.inputs, *self.config.outputs])
        if not needs_transport:
            return
        if not self.runtime.ensure_initialized():
            return
        self.lwrcl_node = self.runtime.rclpy.create_node(f"ipn_user_{self.config.id}".replace("-", "_")[:80])
        if self.config.tool_type:
            self._setup_tool_transport()
        else:
            self._setup_worker_bridge_transport()
        if self.runtime.executor is not None:
            self.runtime.executor.add_node(self.lwrcl_node)

    def _execute_tool(self, linked_inputs: dict[str, Any]) -> bool:
        tool = self.config.tool_type
        if tool == "image_file_input":
            image = self.config.params.get("imageMessage")
            if isinstance(image, dict):
                image = self._normalize_image_message(image)
                if self._should_publish_image_input(image):
                    self.last_outputs["out1"] = image
                    self.publish("out1", image)
                else:
                    self.last_outputs.pop("out1", None)
                self.view = {"kind": "image", "dataUrl": self.config.params.get("dataUrl", ""), "status": self._image_input_status(image)}
            else:
                self.last_outputs.pop("out1", None)
                self.view = {"kind": "empty", "status": "No media selected"}
            return True
        if tool == "video_file_input":
            image = self.config.params.get("imageMessage") or self.config.params.get("frameMessage")
            if isinstance(image, dict):
                image = self._normalize_image_message(image)
                self.last_outputs["out1"] = image
                self.publish("out1", image)
                self.view = {"kind": "image", "dataUrl": self.config.params.get("dataUrl", ""), "status": self.config.params.get("fileName", "")}
            else:
                self.view = {"kind": "empty", "status": "No media selected"}
            return True
        if tool == "function_generator":
            value, status, should_publish = self._function_generator_value()
            if should_publish:
                output = {"data": float(value)}
                self.last_outputs["out1"] = output
                self.publish("out1", output)
            else:
                self.last_outputs.pop("out1", None)
            self.view = {"kind": "text", "status": status}
            return True
        if tool == "image_view":
            image = self.take("in1", None) or self.last_inputs.get("in1")
            data_url = self._image_data_url(image)
            self.view = {"kind": "image", "dataUrl": data_url, "status": self._image_status(image) if data_url else "No image"}
            return True
        if tool == "image_file_save":
            image = self.take("in1", None) or self.last_inputs.get("in1")
            path = self._save_image(image)
            self.view = {"kind": "text", "status": path or "No image to save"}
            return True
        if tool == "graph_view":
            queued_values = []
            while self.has_input("in1"):
                queued_values.append(self.take("in1"))
            now = time.time()
            if "graph_view_start_at" not in self.state:
                self.state["graph_view_start_at"] = now
            elapsed = max(0.0, now - float(self.state["graph_view_start_at"]))
            limit = max(8, min(int(self.config.params.get("sampleLimit") or 10000), 100000))
            for value in queued_values:
                y = self._extract_number(value, str(self.config.params.get("fieldPath") or "data"))
                if y is None:
                    continue
                self._series.append({"t": elapsed, "y": y})
                del self._series[:-limit]
                window = max(0.1, float(self.config.params.get("xAxisSeconds") or 10.0))
                cutoff = elapsed - window
                self._series = [point for point in self._series if point.get("t", 0.0) >= cutoff]
            y_mode = str(self.config.params.get("yAxisMode") or "auto")
            self.view = {
                "kind": "plot",
                "series": self._series[-limit:],
                "status": str(self.config.params.get("fieldPath") or "data"),
                "xAxisSeconds": max(0.1, float(self.config.params.get("xAxisSeconds") or 10.0)),
                "yAxis": {
                    "mode": "fixed" if y_mode == "fixed" else "auto",
                    "min": float(self.config.params.get("yMin") if self.config.params.get("yMin") is not None else -1.0),
                    "max": float(self.config.params.get("yMax") if self.config.params.get("yMax") is not None else 1.0),
                },
            }
            return True
        return False

    def _should_publish_image_input(self, image: dict[str, Any]) -> bool:
        signature = self._image_signature(image)
        mode = str(self.config.params.get("publishMode") or "oneshot")
        if mode == "rate":
            hz = max(0.01, float(self.config.params.get("publishHz") or 1.0))
            now = time.time()
            next_at = float(self.state.get("image_file_next_publish_at") or 0.0)
            if self.state.get("image_file_rate_signature") != signature:
                next_at = 0.0
                self.state["image_file_rate_signature"] = signature
            if now < next_at:
                return False
            self.state["image_file_next_publish_at"] = now + (1.0 / hz)
            return True
        if self.state.get("image_file_sent_signature") != signature:
            self.state["image_file_sent_signature"] = signature
            self.state["image_file_sent_count"] = 0
        sent_count = int(self.state.get("image_file_sent_count") or 0)
        if sent_count >= 5:
            return False
        self.state["image_file_sent_count"] = sent_count + 1
        return True

    def _function_generator_value(self) -> tuple[float, str, bool]:
        p = self.config.params
        signature = json.dumps(p, sort_keys=True, default=str)
        if self.state.get("function_generator_signature") != signature:
            for key in list(self.state.keys()):
                if key.startswith("function_generator_"):
                    self.state.pop(key, None)
            self.state["function_generator_signature"] = signature
        now = time.time()
        if "function_generator_start_at" not in self.state:
            self.state["function_generator_start_at"] = now
        elapsed = max(0.0, now - float(self.state["function_generator_start_at"]))
        publish_hz = max(0.01, float(p.get("publishHz") or 10.0))
        publish_period = 1.0 / publish_hz
        next_publish_at = float(self.state.get("function_generator_next_publish_at") or 0.0)
        should_publish = now >= next_publish_at
        sample_time = max(0.0, float(p.get("sampleTime") or 0.0))
        if sample_time > 0:
            last_sample_at = self.state.get("function_generator_last_sample_at")
            if last_sample_at is not None and now - float(last_sample_at) < sample_time:
                value = float(self.state.get("function_generator_last_value") or 0.0)
                if should_publish:
                    self.state["function_generator_next_publish_at"] = self._next_periodic_time(next_publish_at, publish_period, now)
                return value, self._function_generator_status(value, elapsed, held=True, should_publish=should_publish, publish_hz=publish_hz), should_publish
            self.state["function_generator_last_sample_at"] = now

        signal_type = str(p.get("signalType") or "sine")
        value = self._function_generator_raw_value(signal_type, elapsed)
        self.state["function_generator_last_value"] = float(value)
        if should_publish:
            self.state["function_generator_next_publish_at"] = self._next_periodic_time(next_publish_at, publish_period, now)
        return float(value), self._function_generator_status(float(value), elapsed, held=False, should_publish=should_publish, publish_hz=publish_hz), should_publish

    def _next_periodic_time(self, previous_next: float, period: float, now: float) -> float:
        next_at = (previous_next if previous_next > 0 else now) + period
        while next_at <= now:
            next_at += period
        return next_at

    def _function_generator_raw_value(self, signal_type: str, elapsed: float) -> float:
        p = self.config.params
        amplitude = float(p.get("amplitude") or 0.0)
        bias = float(p.get("bias") or 0.0)
        frequency = max(0.0, float(p.get("frequency") or 0.0))
        phase = float(p.get("phase") or 0.0)
        if signal_type == "step":
            step_time = max(0.0, float(p.get("stepTime") or 0.0))
            initial = float(p.get("initialValue") or 0.0)
            final = float(p.get("finalValue") or 0.0)
            return final if elapsed >= step_time else initial
        if signal_type == "square":
            duty = max(0.0, min(100.0, float(p.get("dutyCycle") or 50.0))) / 100.0
            if frequency <= 0:
                return bias + amplitude
            cycle = (elapsed * frequency + phase / (2.0 * math.pi)) % 1.0
            return bias + (amplitude if cycle < duty else -amplitude)
        if signal_type == "ramp":
            slope = float(p.get("rampSlope") or 0.0)
            return bias + slope * elapsed
        if signal_type == "chirp":
            duration = max(0.001, float(p.get("chirpDuration") or 1.0))
            f0 = max(0.0, float(p.get("chirpStartFrequency") or 0.0))
            f1 = max(0.0, float(p.get("chirpEndFrequency") or f0))
            t = min(elapsed, duration)
            k = (f1 - f0) / duration
            angle = 2.0 * math.pi * (f0 * t + 0.5 * k * t * t) + phase
            return bias + amplitude * math.sin(angle)
        if signal_type == "white_noise":
            seed = int(float(p.get("noiseSeed") or 0.0))
            if self.state.get("function_generator_noise_seed") != seed:
                self.state["function_generator_noise_rng"] = random.Random(seed)
                self.state["function_generator_noise_seed"] = seed
            rng = self.state.get("function_generator_noise_rng")
            if not isinstance(rng, random.Random):
                rng = random.Random(seed)
                self.state["function_generator_noise_rng"] = rng
            mean = float(p.get("noiseMean") or 0.0)
            std = max(0.0, float(p.get("noiseStd") or 0.0))
            return rng.gauss(mean, std)
        return bias + amplitude * math.sin((2.0 * math.pi * frequency * elapsed) + phase)

    def _function_generator_status(self, value: float, elapsed: float, held: bool, should_publish: bool, publish_hz: float) -> str:
        signal_type = str(self.config.params.get("signalType") or "sine").replace("_", " ")
        state = "published" if should_publish else "waiting"
        suffix = " held" if held else ""
        return f"{signal_type} {publish_hz:.3g}Hz {state} t={elapsed:.3f}s y={value:.5g}{suffix}"

    def _image_signature(self, image: dict[str, Any]) -> str:
        data = image.get("data")
        return "|".join([
            str(self.config.params.get("fileName") or ""),
            str(image.get("width") or ""),
            str(image.get("height") or ""),
            str(image.get("encoding") or ""),
            str(len(data) if hasattr(data, "__len__") else ""),
            str(self.config.params.get("dataUrl") or "")[-80:],
        ])

    def _image_input_status(self, image: dict[str, Any]) -> str:
        mode = str(self.config.params.get("publishMode") or "oneshot")
        base = self.config.params.get("fileName", "") or self._image_status(image)
        if mode == "rate":
            hz = max(0.01, float(self.config.params.get("publishHz") or 1.0))
            return f"{base} / {hz:g} Hz"
        sent = self.state.get("image_file_sent_signature") == self._image_signature(image)
        return f"{base} / one shot {'sent' if sent else 'ready'}"

    def _image_status(self, image: Any) -> str:
        width = self._field(image, "width")
        height = self._field(image, "height")
        encoding = self._field(image, "encoding") or "rgb8"
        return f"{width} x {height} {encoding}" if width and height else str(encoding)

    def _image_data_url(self, image: Any) -> str:
        if not image:
            return ""
        if isinstance(image, dict) and isinstance(image.get("dataUrl"), str):
            return image["dataUrl"]
        width = int(self._field(image, "width") or 0)
        height = int(self._field(image, "height") or 0)
        rgb = self._image_rgb_bytes(image, width, height)
        if not width or not height or rgb is None:
            return ""
        return "data:image/bmp;base64," + base64.b64encode(self._bmp_bytes(width, height, rgb)).decode("ascii")

    def _image_rgb_bytes(self, image: Any, width: int, height: int) -> bytes | None:
        encoding = str(self._field(image, "encoding") or "rgb8").lower()
        data = self._field(image, "data")
        if not width or not height or data is None:
            return None
        try:
            raw = bytes(data)
        except Exception:
            raw = bytes(max(0, min(255, int(value))) for value in data)
        pixel_count = width * height
        if encoding in {"rgb8", "bgr8"}:
            if encoding == "bgr8":
                raw = bytes(v for i in range(0, len(raw), 3) for v in raw[i:i + 3][::-1])
            return raw[:pixel_count * 3].ljust(pixel_count * 3, b"\x00")
        elif encoding in {"rgba8", "bgra8"}:
            rgb = bytearray()
            for i in range(0, min(len(raw), pixel_count * 4), 4):
                px = raw[i:i + 4]
                rgb.extend((px[2], px[1], px[0]) if encoding == "bgra8" else px[:3])
            return bytes(rgb).ljust(pixel_count * 3, b"\x00")
        elif encoding in {"mono8", "8uc1"}:
            gray = raw[:pixel_count]
            return bytes(value for value in gray for _ in range(3)).ljust(pixel_count * 3, b"\x00")
        return None

    def _normalize_image_message(self, image: dict[str, Any]) -> dict[str, Any]:
        if image.get("dataEncoding") != "base64" or not isinstance(image.get("data"), str):
            return image
        normalized = dict(image)
        try:
            normalized["data"] = base64.b64decode(image["data"])
            normalized.pop("dataEncoding", None)
        except Exception as exc:
            self.log(f"image decode error: {exc}")
        return normalized

    def _bmp_bytes(self, width: int, height: int, rgb: bytes) -> bytes:
        row_stride = ((width * 3 + 3) // 4) * 4
        pixel_bytes = bytearray()
        for y in range(height - 1, -1, -1):
            row = rgb[y * width * 3:(y + 1) * width * 3]
            bgr = bytearray()
            for i in range(0, len(row), 3):
                bgr.extend(row[i:i + 3][::-1])
            bgr.extend(b"\x00" * (row_stride - len(bgr)))
            pixel_bytes.extend(bgr)
        file_size = 14 + 40 + len(pixel_bytes)
        return b"".join([
            b"BM",
            file_size.to_bytes(4, "little"),
            (0).to_bytes(4, "little"),
            (54).to_bytes(4, "little"),
            (40).to_bytes(4, "little"),
            width.to_bytes(4, "little", signed=True),
            height.to_bytes(4, "little", signed=True),
            (1).to_bytes(2, "little"),
            (24).to_bytes(2, "little"),
            (0).to_bytes(4, "little"),
            len(pixel_bytes).to_bytes(4, "little"),
            (2835).to_bytes(4, "little", signed=True),
            (2835).to_bytes(4, "little", signed=True),
            (0).to_bytes(4, "little"),
            (0).to_bytes(4, "little"),
            bytes(pixel_bytes),
        ])

    def _save_image(self, image: Any) -> str:
        data_url = self._image_data_url(image)
        if not data_url:
            return ""
        signature = data_url[-80:]
        if signature == self._last_saved_signature:
            return str(self.config.params.get("lastSavedPath") or "")
        payload = data_url.split(",", 1)[1]
        out_dir = Path.cwd() / "saved_images"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.config.name}_{int(time.time() * 1000)}.bmp"
        path.write_bytes(base64.b64decode(payload))
        self._last_saved_signature = signature
        self.config.params["lastSavedPath"] = str(path)
        return str(path)

    def _extract_number(self, value: Any, path: str) -> float | None:
        current = value
        for part in [item for item in path.replace("/", ".").split(".") if item]:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)) and part.isdigit():
                current = current[int(part)]
            else:
                current = getattr(current, part, None)
                if callable(current):
                    try:
                        current = current()
                    except TypeError:
                        pass
        if isinstance(current, (int, float)):
            return float(current)
        try:
            return float(current)
        except Exception:
            return None

    def _field(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        field = getattr(value, key, None)
        if callable(field):
            try:
                return field()
            except TypeError:
                return field
        return field

    def _set_field(self, target: Any, key: str, value: Any) -> None:
        field = getattr(target, key, None)
        if callable(field):
            try:
                field(value)
                return
            except TypeError:
                pass
        setattr(target, key, value)

    def _setup_tool_transport(self) -> None:
        for output in self.config.outputs:
            topics = self._port_topics(output)
            type_cls = import_type_class(output.data_type)
            _, kind, _ = split_type(output.data_type)
            for topic in topics:
                if kind == "msg":
                    self.publishers.setdefault(output.id, []).append(self.lwrcl_node.create_publisher(type_cls, topic, 10))
                else:
                    self.clients.setdefault(output.id, []).append(self.lwrcl_node.create_client(type_cls, topic))
        for input_port in self.config.inputs:
            topics = self._port_topics(input_port)
            type_cls = import_type_class(input_port.data_type)
            _, kind, _ = split_type(input_port.data_type)
            for topic in topics:
                if kind == "msg":
                    sub = self.lwrcl_node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port.id), 10)
                    self.subscriptions.append(sub)
                else:
                    srv = self.lwrcl_node.create_service(type_cls, topic, self._make_service_callback(input_port.id))
                    self.services.append(srv)

    def _setup_worker_bridge_transport(self) -> None:
        for output in self.config.outputs:
            type_cls = import_type_class(output.data_type)
            _, kind, _ = split_type(output.data_type)
            if kind != "msg":
                continue
            for topic in self._port_topics(output):
                sub = self.lwrcl_node.create_subscription(type_cls, topic, self._make_output_subscription_callback(output.id), 10)
                self.subscriptions.append(sub)

    def _port_topics(self, port: PortConfig) -> tuple[str, ...]:
        if port.topics:
            return port.topics
        return (port.topic,) if port.topic else ()

    def _make_subscription_callback(self, input_id: str):
        def callback(msg):
            self._store_input(input_id, msg)
            port = self._input_port(input_id)
            if port is None or port.receive_mode != "callback":
                return
            outputs: dict[str, Any] = {}
            self._execute_callback(port, msg, None, outputs)
            for key, value in outputs.items():
                self.last_outputs[key] = value
                self.publish(key, value)

        return callback

    def _make_output_subscription_callback(self, output_id: str):
        def callback(msg):
            self.last_outputs[output_id] = msg
            self.log("received", output_id)

        return callback

    def _make_service_callback(self, input_id: str):
        def callback(request, response):
            self._store_input(input_id, request)
            port = self._input_port(input_id)
            outputs: dict[str, Any] = {}
            if port is not None and port.receive_mode == "callback":
                self._execute_callback(port, request, response, outputs)
            for key, value in outputs.items():
                self.last_outputs[key] = value
                self.publish(key, value)
            return response

        return callback

    def _execute_callback(self, port: PortConfig, msg: Any, response: Any, outputs: dict[str, Any]) -> None:
        code = port.callback_code.strip()
        if not code:
            return
        local = self._locals({
            "input_id": port.id,
            "msg": msg,
            "request": msg,
            "response": response,
            "outputs": outputs,
        })
        try:
            exec(code, self._globals(), local)
        except Exception as exc:
            self.log(f"{port.id} callback error: {exc}")

    def _execute_loop(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        code = self.config.loop_code.strip()
        if not code:
            return
        local = self._locals({
            "inputs": inputs,
            "outputs": outputs,
            "now": time.time(),
            "latest": self.latest,
            "take": self.take,
            "has_input": self.has_input,
        })
        try:
            exec(code, self._globals(), local)
        except Exception as exc:
            self.log(f"loop error: {exc}")

    def _execute_timer_if_due(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        now = time.time()
        for timer in self.config.timers:
            code = timer.callback_code.strip()
            if not code:
                continue
            period = max(0.001, float(timer.period_sec or 1.0))
            next_at = self._next_timer_by_id.get(timer.id, 0.0)
            if next_at <= 0:
                next_at = now
            if now < next_at:
                self._next_timer_by_id[timer.id] = next_at
                continue
            self._next_timer_by_id[timer.id] = self._next_periodic_time(next_at, period, now)
            local = self._locals({
                "timer_id": timer.id,
                "timer_name": timer.name,
                "inputs": inputs,
                "outputs": outputs,
                "now": now,
                "period": period,
                "latest": self.latest,
                "take": self.take,
                "has_input": self.has_input,
            })
            try:
                exec(code, self._globals(), local)
            except Exception as exc:
                self.log(f"{timer.id} timer callback error: {exc}")

    def _globals(self) -> dict[str, Any]:
        signature = f"{self.env_site_packages}|{self.config.import_code}"
        if self._exec_globals is not None and self._import_signature == signature:
            return self._exec_globals
        self._exec_globals = {
            "__builtins__": {
                "__import__": __import__,
                "abs": abs,
                "bool": bool,
                "dict": dict,
                "enumerate": enumerate,
                "float": float,
                "getattr": getattr,
                "hasattr": hasattr,
                "int": int,
                "len": len,
                "list": list,
                "max": max,
                "min": min,
                "print": self.log,
                "range": range,
                "round": round,
                "setattr": setattr,
                "str": str,
                "sum": sum,
            },
        }
        code = self.config.import_code.strip()
        if code:
            original_path = list(sys.path)
            try:
                if self.env_site_packages:
                    sys.path.insert(0, str(self.env_site_packages))
                exec(code, self._exec_globals, self._exec_globals)
            except Exception as exc:
                self.log(f"import setup error: {exc}")
            finally:
                sys.path[:] = original_path
        self._import_signature = signature
        return self._exec_globals

    def _locals(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "node": self.lwrcl_node or self.preview_node,
            "params": self.config.params,
            "state": self.state,
            "publish": self.publish,
            "log": self.log,
            **extra,
        }

    def _store_input(self, input_id: str, value: Any) -> None:
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-100]

    def _reset_state_on_image_shape_change(self, inputs: dict[str, Any]) -> None:
        for input_port in self.config.inputs:
            if normalize_type(input_port.data_type) != "sensor_msgs/msg/Image":
                continue
            image = inputs.get(input_port.id)
            if image is None:
                continue
            data = self._field(image, "data")
            signature = (
                self._field(image, "width"),
                self._field(image, "height"),
                self._field(image, "encoding"),
                len(data) if hasattr(data, "__len__") else None,
            )
            previous = self._image_input_signatures.get(input_port.id)
            self._image_input_signatures[input_port.id] = signature
            if previous is not None and previous != signature:
                self.state.clear()
                self.log(f"reset state: {input_port.id} image shape changed {previous} -> {signature}")

    def _input_port(self, input_id: str) -> PortConfig | None:
        for port in self.config.inputs:
            if port.id == input_id:
                return port
        return None

    def _input_type(self, input_id: str) -> str:
        for input_port in self.config.inputs:
            if input_port.id == input_id:
                return input_port.data_type
        return ""

    def _output_type(self, output_id: str) -> str:
        for output in self.config.outputs:
            if output.id == output_id:
                return output.data_type
        return ""

    def _coerce_message(self, data_type: str, value: Any) -> Any:
        msg_cls = import_type_class(data_type)
        if hasattr(value, "_fields_and_field_types"):
            return value
        msg = msg_cls()
        if isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    if key == "data" and isinstance(item, str) and value.get("dataEncoding") == "base64":
                        item = base64.b64decode(item)
                    self._set_field(msg, key, item)
        elif hasattr(msg, "data"):
            self._set_field(msg, "data", value)
        return msg

    def _coerce_service_request(self, data_type: str, value: Any) -> Any:
        srv_cls = import_type_class(data_type)
        request = srv_cls.Request()
        if hasattr(value, "_fields_and_field_types"):
            return value
        if hasattr(request, "data"):
            self._set_field(request, "data", value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(request, key):
                    self._set_field(request, key, item)
        return request

    def _repr_values(self, values: dict[str, Any]) -> dict[str, str]:
        return {key: self._repr_value(value) for key, value in values.items()}

    def _repr_value(self, value: Any) -> str:
        if value is None:
            return "None"
        if hasattr(value, "data"):
            return repr(value.data)[:240]
        return repr(value)[:240]

    def _signature(self, config: CustomLwrclNodeConfig) -> tuple[Any, ...]:
        return (
            config.tool_type,
            tuple((p.id, p.data_type, p.topic, p.topics) for p in config.inputs),
            tuple((p.id, p.data_type, p.topic, p.topics) for p in config.outputs),
        )


class GraphRuntime:
    def __init__(self) -> None:
        self.runtime = LwrclpyRuntime()
        self.instances: dict[str, CustomLwrclNodeInstance] = {}
        self._lock = threading.RLock()

    @property
    def ros(self) -> LwrclpyRuntime:
        return self.runtime

    def close(self) -> None:
        with self._lock:
            for node in self.instances.values():
                node.close()
            self.instances.clear()

    def stop(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            stopped: dict[str, str] = {}
            for node_id, node in list(self.instances.items()):
                if node.stop_worker(force=force):
                    stopped[node_id] = "killed" if force else "terminated"
            return {"stopped": stopped, "force": force}

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._run_locked(payload)

    def _run_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        configs = [self._parse_node(node) for node in payload.get("nodes", [])]
        links = [link for link in payload.get("links", []) if self._valid_link(link, configs)]
        self._apply_link_topics(configs, links)
        self._apply_builtin_param_topics(configs)
        needs_lwrclpy = bool(links) or any(not node.tool_type for node in configs) or any(
            any(port.topics for port in [*node.inputs, *node.outputs])
            for node in configs
            if node.tool_type
        )
        if needs_lwrclpy and not self.runtime.ensure_initialized():
            return {
                "nodes": {},
                "lwrclpy": {"available": self.runtime.available, "error": self.runtime.error or "lwrclpy is required for web preview topic transport"},
                "setup": {"complete": False},
            }
        active_ids = {node.id for node in configs}
        for node_id in list(self.instances.keys()):
            if node_id not in active_ids:
                self.instances.pop(node_id).close()

        response_nodes: dict[str, Any] = {}
        for config in configs:
            instance = self._instance_for(config)
            setup_ok = self._ensure_node_environment(config, instance)
            response_nodes[config.id] = {"meta": {"environment": instance.env_status, "logs": instance.logs[-20:]}, "values": {}, "view": instance.view}
            if not setup_ok:
                return {
                    "nodes": response_nodes,
                    "lwrclpy": {"available": self.runtime.available, "error": self.runtime.error or instance.env_status},
                    "setup": {"complete": False},
                }
            if not config.tool_type:
                if instance.env_python_bin is None:
                    instance.env_status = "python venv missing"
                    return {
                        "nodes": response_nodes,
                        "lwrclpy": {"available": self.runtime.available, "error": instance.env_status},
                        "setup": {"complete": False},
                    }
                if not self._ensure_worker_process(config, instance, instance.env_python_bin):
                    return {
                        "nodes": response_nodes,
                        "lwrclpy": {"available": self.runtime.available, "error": instance.env_status},
                        "setup": {"complete": False},
                    }

        if needs_lwrclpy:
            self.runtime.spin_some(1)
        for config in self._sort(configs, links):
            instance = self._instance_for(config)
            meta = instance.tick({})
            if needs_lwrclpy:
                self.runtime.spin_some(1)
            response_nodes[config.id] = {"meta": meta, "values": meta.get("outputs", {}), "view": instance.view}
        if needs_lwrclpy:
            self.runtime.spin_some(2)
        return {
            "nodes": response_nodes,
            "lwrclpy": {"available": self.runtime.available, "error": self.runtime.error},
            "setup": {"complete": True},
        }

    def _instance_for(self, config: CustomLwrclNodeConfig) -> CustomLwrclNodeInstance:
        instance = self.instances.get(config.id)
        if instance is None:
            instance = CustomLwrclNodeInstance(config, self.runtime)
            self.instances[config.id] = instance
        else:
            instance.update_config(config)
        return instance

    def _ensure_node_environment(self, config: CustomLwrclNodeConfig, instance: CustomLwrclNodeInstance) -> bool:
        if config.tool_type:
            instance.env_status = "built-in node"
            return True
        uv = self._uv_command()
        if not uv:
            instance.env_status = "uv command not found"
            instance.log(instance.env_status)
            return False
        env_root = Path.cwd() / ".node_envs" / config.id
        req_text = config.requirements.strip() + "\n"
        req_hash = hashlib.sha256(req_text.encode("utf-8")).hexdigest()
        desired_env_signature = (str(env_root), req_hash)
        hash_file = env_root / ".requirements.sha256"
        req_file = env_root / "requirements.txt"
        python_bin = env_root / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
        try:
            if instance.env_signature == desired_env_signature and python_bin.exists():
                instance.env_status = "ready"
                return True
            env_root.mkdir(parents=True, exist_ok=True)
            if not python_bin.exists():
                instance.env_status = "creating venv"
                subprocess.run([uv, "venv", str(env_root)], cwd=Path.cwd(), check=True, capture_output=True, text=True)
            req_file.write_text(req_text, encoding="utf-8")
            current_hash = hash_file.read_text(encoding="utf-8") if hash_file.exists() else ""
            if current_hash != req_hash:
                instance.env_status = "installing requirements"
                if req_text.strip():
                    subprocess.run([uv, "pip", "install", "--python", str(python_bin), "-r", str(req_file)], cwd=Path.cwd(), check=True, capture_output=True, text=True)
                hash_file.write_text(req_hash, encoding="utf-8")
            instance.env_path = env_root
            instance.env_python_bin = python_bin
            instance.env_site_packages = self._site_packages_for(env_root)
            if not self._ensure_lwrclpy_in_env(python_bin, instance):
                return False
            instance.env_signature = desired_env_signature
            instance.env_status = "ready"
            return True
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1:]
            instance.env_status = "setup failed: " + (detail[0] if detail else str(exc))
            instance.log(instance.env_status)
            return False
        except Exception as exc:
            instance.env_status = f"setup failed: {exc}"
            instance.log(instance.env_status)
            return False

    def _site_packages_for(self, env_root: Path) -> Path | None:
        if sys.platform.startswith("win"):
            path = env_root / "Lib" / "site-packages"
            return path if path.exists() else None
        lib_dir = env_root / "lib"
        if not lib_dir.exists():
            return None
        for item in lib_dir.iterdir():
            candidate = item / "site-packages"
            if candidate.exists():
                return candidate
        return None

    def _ensure_lwrclpy_in_env(self, python_bin: Path, instance: CustomLwrclNodeInstance) -> bool:
        installer = Path(__file__).resolve().parents[1] / "scripts" / "install_lwrclpy.py"
        if not installer.exists():
            instance.env_status = "lwrclpy installer not found"
            instance.log(instance.env_status)
            return False
        instance.env_status = "installing lwrclpy"
        try:
            subprocess.run([str(python_bin), str(installer)], cwd=Path.cwd(), check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1:]
            instance.env_status = "lwrclpy install failed: " + (detail[0] if detail else str(exc))
            instance.log(instance.env_status)
            return False

    def _ensure_worker_process(self, config: CustomLwrclNodeConfig, instance: CustomLwrclNodeInstance, python_bin: Path) -> bool:
        signature = self._worker_signature(config)
        if instance.worker_process is not None and instance.worker_process.poll() is None and instance.worker_signature == signature:
            return True
        instance.stop_worker()
        worker_dir = Path.cwd() / ".node_workers"
        worker_dir.mkdir(parents=True, exist_ok=True)
        config_path = worker_dir / f"{config.id}.json"
        log_path = worker_dir / f"{config.id}.log"
        pid_path = worker_dir / f"{config.id}.pid"
        config_path.write_text(json.dumps(self._worker_config(config), ensure_ascii=False, default=str), encoding="utf-8")
        worker_script = Path(__file__).resolve().parent / "node_worker.py"
        try:
            log_file = log_path.open("a", encoding="utf-8")
            instance.worker_process = subprocess.Popen(
                [str(python_bin), str(worker_script), str(config_path)],
                cwd=Path.cwd(),
                stdout=log_file,
                stderr=log_file,
                text=True,
                start_new_session=(os.name != "nt"),
            )
            instance.worker_signature = signature
            instance.worker_config_path = config_path
            instance.worker_log_path = log_path
            instance.worker_pid_path = pid_path
            pid_path.write_text(str(instance.worker_process.pid), encoding="utf-8")
            instance.env_status = "worker running"
            return True
        except Exception as exc:
            instance.env_status = f"worker start failed: {exc}"
            instance.log(instance.env_status)
            return False

    def _worker_signature(self, config: CustomLwrclNodeConfig) -> tuple[Any, ...]:
        return (
            config.id,
            config.name,
            config.loop_code,
            tuple((timer.id, timer.name, timer.period_sec, timer.callback_code) for timer in config.timers),
            config.import_code,
            json.dumps(config.params, sort_keys=True, default=str),
            tuple((p.id, p.name, p.data_type, p.topics, p.receive_mode, p.callback_code) for p in config.inputs),
            tuple((p.id, p.name, p.data_type, p.topics) for p in config.outputs),
        )

    def _worker_config(self, config: CustomLwrclNodeConfig) -> dict[str, Any]:
        return {
            "node": {
                "id": config.id,
                "name": config.name,
                "inputs": [self._port_dict(port, include_callback=True) for port in config.inputs],
                "outputs": [self._port_dict(port, include_callback=False) for port in config.outputs],
                "loopCode": config.loop_code,
                "timers": [self._timer_dict(timer) for timer in config.timers],
                "timerEnabled": config.timer_enabled,
                "timerPeriodSec": config.timer_period_sec,
                "timerCode": config.timer_code,
                "importCode": config.import_code,
                "params": config.params,
            },
            "portTopics": {
                "inputs": {port.id: list(port.topics) for port in config.inputs if port.topics},
                "outputs": {port.id: list(port.topics) for port in config.outputs if port.topics},
            },
        }

    def _timer_dict(self, timer: TimerConfig) -> dict[str, Any]:
        return {"id": timer.id, "name": timer.name, "periodSec": timer.period_sec, "callbackCode": timer.callback_code}

    def _port_dict(self, port: PortConfig, include_callback: bool) -> dict[str, Any]:
        data = {"id": port.id, "name": port.name, "dataType": port.data_type, "receiveMode": port.receive_mode}
        if include_callback:
            data["callbackCode"] = port.callback_code
        return data

    def _uv_command(self) -> str | None:
        direct = shutil.which("uv")
        if direct:
            return direct
        sibling = Path(sys.executable).parent / ("uv.exe" if sys.platform.startswith("win") else "uv")
        return str(sibling) if sibling.exists() else None

    def _parse_node(self, node: dict[str, Any]) -> CustomLwrclNodeConfig:
        tool_type = str(node.get("toolType", ""))
        return CustomLwrclNodeConfig(
            id=str(node.get("id")),
            name=str(node.get("name") or node.get("id")),
            x=int(node.get("x", 0)),
            y=int(node.get("y", 0)),
            inputs=[self._parse_port(port, tool_type=tool_type) for port in node.get("inputs", [])],
            outputs=[self._parse_port(port) for port in node.get("outputs", [])],
            loop_code=str(node.get("loopCode", "")),
            timers=self._parse_timers(node),
            timer_enabled=bool(node.get("timerEnabled", False)),
            timer_period_sec=float(node.get("timerPeriodSec", 1.0) or 1.0),
            timer_code=str(node.get("timerCode", "")),
            import_code=str(node.get("importCode", "")),
            requirements=str(node.get("requirements", "")),
            tool_type=tool_type,
            params=dict(node.get("params", {}) if isinstance(node.get("params", {}), dict) else {}),
        )

    def _parse_port(self, port: dict[str, Any], tool_type: str = "") -> PortConfig:
        data_type = str(port.get("dataType", "std_msgs/msg/String"))
        if tool_type == "graph_view" and not data_type:
            data_type = "std_msgs/msg/Float32"
        return PortConfig(
            id=str(port.get("id")),
            name=str(port.get("name") or port.get("id")),
            data_type=normalize_type(data_type),
            topic=str(port.get("topic", "")),
            receive_mode=str(port.get("receiveMode", "callback")),
            callback_code=str(port.get("callbackCode", "")),
        )

    def _parse_timers(self, node: dict[str, Any]) -> list[TimerConfig]:
        timers = node.get("timers")
        if isinstance(timers, list):
            result = []
            for index, timer in enumerate(timers):
                if not isinstance(timer, dict):
                    continue
                timer_id = str(timer.get("id") or f"timer{index + 1}")
                result.append(TimerConfig(
                    id=timer_id,
                    name=str(timer.get("name") or timer_id),
                    period_sec=max(0.001, float(timer.get("periodSec", timer.get("period", 1.0)) or 1.0)),
                    callback_code=str(timer.get("callbackCode", timer.get("timerCode", ""))),
                ))
            return result
        if node.get("timerEnabled", False):
            return [TimerConfig(
                id="timer1",
                name="timer1",
                period_sec=max(0.001, float(node.get("timerPeriodSec", 1.0) or 1.0)),
                callback_code=str(node.get("timerCode", "")),
            )]
        return []

    def _apply_link_topics(self, nodes: list[CustomLwrclNodeConfig], links: list[dict[str, Any]]) -> None:
        by_id = {node.id: node for node in nodes}
        source_topics: dict[tuple[str, str], str] = {}
        for link in links:
            key = (str(link.get("fromNode")), str(link.get("fromPort")))
            if key not in source_topics:
                source_topics[key] = self._link_topic(link, by_id)
            link["name"] = source_topics[key]
        input_topics: dict[tuple[str, str], set[str]] = {}
        output_topics: dict[tuple[str, str], set[str]] = {}
        for link in links:
            src = by_id.get(str(link.get("fromNode")))
            dst = by_id.get(str(link.get("toNode")))
            src_port = next((port for port in (src.outputs if src else []) if port.id == str(link.get("fromPort"))), None)
            dst_port = next((port for port in (dst.inputs if dst else []) if port.id == str(link.get("toPort"))), None)
            if src and dst and src_port and dst_port:
                if not src_port.data_type:
                    src_port.data_type = dst_port.data_type
                if not dst_port.data_type:
                    dst_port.data_type = src_port.data_type
            topic = source_topics.get((str(link.get("fromNode")), str(link.get("fromPort"))), "")
            if not topic:
                continue
            output_topics.setdefault((str(link.get("fromNode")), str(link.get("fromPort"))), set()).add(topic)
            input_topics.setdefault((str(link.get("toNode")), str(link.get("toPort"))), set()).add(topic)
        for node in nodes:
            for port in node.inputs:
                port.topic = ""
                port.topics = tuple(sorted(input_topics.get((node.id, port.id), set())))
            for port in node.outputs:
                port.topic = ""
                port.topics = tuple(sorted(output_topics.get((node.id, port.id), set())))

    def _apply_builtin_param_topics(self, nodes: list[CustomLwrclNodeConfig]) -> None:
        for node in nodes:
            if node.tool_type != "function_generator":
                continue
            topic = str(node.params.get("ddsTopic") or "").strip()
            if not topic:
                continue
            if not topic.startswith("/"):
                topic = f"/{topic}"
            output = next((port for port in node.outputs if port.id == "out1"), None)
            if output is None:
                continue
            topics = set(output.topics)
            topics.add(topic)
            output.topics = tuple(sorted(topics))

    def _link_topic(self, link: dict[str, Any], nodes: dict[str, CustomLwrclNodeConfig]) -> str:
        name = str(link.get("name") or "").strip()
        if not name:
            src = nodes.get(str(link.get("fromNode")))
            dst = nodes.get(str(link.get("toNode")))
            src_port = next((port for port in (src.outputs if src else []) if port.id == str(link.get("fromPort"))), None)
            name = f"{src_port.name if src_port else link.get('fromPort') or 'topic'}"
        return name if name.startswith("/") else f"/{name}"

    def _sort(self, nodes: list[CustomLwrclNodeConfig], links: list[dict[str, Any]]) -> list[CustomLwrclNodeConfig]:
        by_id = {node.id: node for node in nodes}
        indegree = {node.id: 0 for node in nodes}
        outgoing = {node.id: [] for node in nodes}
        for link in links:
            src = str(link.get("fromNode"))
            dst = str(link.get("toNode"))
            if src in by_id and dst in by_id:
                indegree[dst] += 1
                outgoing[src].append(dst)
        queue = [node.id for node in nodes if indegree[node.id] == 0]
        ordered = []
        while queue:
            node_id = queue.pop(0)
            ordered.append(by_id[node_id])
            for dst in outgoing[node_id]:
                indegree[dst] -= 1
                if indegree[dst] == 0:
                    queue.append(dst)
        if len(ordered) != len(nodes):
            seen = {node.id for node in ordered}
            ordered.extend(node for node in nodes if node.id not in seen)
        return ordered

    def _valid_link(self, link: dict[str, Any], nodes: list[CustomLwrclNodeConfig]) -> bool:
        by_id = {node.id: node for node in nodes}
        src = by_id.get(str(link.get("fromNode")))
        dst = by_id.get(str(link.get("toNode")))
        if src is None or dst is None or src.id == dst.id:
            return False
        src_port = next((port for port in src.outputs if port.id == str(link.get("fromPort"))), None)
        dst_port = next((port for port in dst.inputs if port.id == str(link.get("toPort"))), None)
        if not src_port or not dst_port:
            return False
        src_interface = src.tool_type == "topic_input"
        dst_interface = dst.tool_type == "topic_output"
        if src.tool_type and src.tool_type != "topic_input" and not src_port.data_type:
            return True
        if dst.tool_type and dst.tool_type != "topic_output" and not dst_port.data_type:
            return True
        if src_interface and dst_interface:
            return False
        if src_interface:
            return bool(dst_port.data_type)
        if dst_interface:
            return bool(src_port.data_type)
        return src_port.data_type == dst_port.data_type
