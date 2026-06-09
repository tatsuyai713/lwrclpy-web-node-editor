from __future__ import annotations

import importlib
import importlib.metadata
import json
import math
import pkgutil
import base64
import builtins
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

from .runtime_exec import find_lwrclpy_installer, resolve_worker_command


def discover_lwrclpy_types() -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for module_info in pkgutil.iter_modules():
        package = module_info.name
        if not (package.endswith("_msgs") or package.endswith("_srvs")):
            continue
        spec = importlib.util.find_spec(package)
        search_locations = list(spec.submodule_search_locations or []) if spec else []
        if not search_locations:
            continue
        package_dir = Path(search_locations[0])
        for kind in ("msg", "srv"):
            kind_dir = package_dir / kind
            if not kind_dir.is_dir():
                continue
            if kind == "msg":
                names = sorted(_discover_message_names(kind_dir))
            else:
                names = sorted({
                    path.stem.removesuffix("_Request").removesuffix("_Response")
                    for path in kind_dir.glob("*.py")
                    if path.stem[:1].isupper() and path.stem.endswith(("_Request", "_Response"))
                })
            if names:
                result.setdefault(package, {})[kind] = names
    return dict(sorted(result.items()))


def _discover_message_names(kind_dir: Path) -> set[str]:
    names: set[str] = {
        path.stem for path in kind_dir.glob("*.py")
        if path.stem[:1].isupper() and not path.stem.endswith("_Wrapper")
    }
    for path in kind_dir.glob("*.so"):
        stem = path.stem
        if stem.startswith("lib") and len(stem) > 3 and stem[3:4].isupper():
            names.add(stem[3:])
        elif stem.startswith("_") and stem.endswith("Wrapper"):
            candidate = stem[1:-7]
            if candidate[:1].isupper():
                names.add(candidate)
    return names


LWRCLPY_TYPE_TREE = discover_lwrclpy_types()
GUI_DISPLAY_HZ = 30.0
LWRCLPY_INSTALL_MARKER = "github-latest-wheel"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp_path.replace(path)


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


def topic_qos(data_type: str, depth: int = 1) -> Any:
    if normalize_type(data_type) not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
        return 10
    try:
        qos = importlib.import_module("rclpy.qos")
        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=qos.ReliabilityPolicy.BEST_EFFORT,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return depth


def publisher_qos(data_type: str) -> Any:
    return topic_qos(data_type, depth=5)


def subscriber_qos(data_type: str) -> Any:
    return topic_qos(data_type, depth=1)


class LwrclpyRuntime:
    def __init__(self) -> None:
        self.available = False
        self.initialized = False
        self.error = ""
        self.version = ""
        self.rclpy = None
        self.executor = None
        self._spin_thread: threading.Thread | None = None
        self._stop_spin = threading.Event()
        try:
            self.rclpy = importlib.import_module("rclpy")
            self.available = True
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
        try:
            self.version = importlib.metadata.version("lwrclpy")
        except Exception:
            self.version = ""

    def status(self, error: str = "") -> dict[str, Any]:
        return {
            "available": self.available,
            "error": error or self.error,
            "version": self.version,
        }

    def ensure_initialized(self) -> bool:
        if not self.available or self.rclpy is None:
            return False
        if self.initialized:
            return True
        try:
            executors = importlib.import_module("rclpy.executors")
            if not self.rclpy.ok():
                self.rclpy.init(args=None)
            self.executor = executors.MultiThreadedExecutor(num_threads=max(4, min(16, (os.cpu_count() or 4))))
            # Spin the executor in a dedicated background thread so ROS subscription
            # callbacks are not driven by the main graph processing loop.
            self._stop_spin.clear()
            self._spin_thread = threading.Thread(
                target=self._spin_loop, daemon=True, name="lwrclpy-executor-spin"
            )
            self._spin_thread.start()
            self.initialized = True
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def _spin_loop(self) -> None:
        """Background thread: lets lwrclpy's executor drain callbacks with its worker pool."""
        if self.executor is None:
            return
        try:
            self.executor.spin()
        except Exception as exc:
            if not self._stop_spin.is_set():
                self.error = str(exc)

    def spin_once(self) -> None:
        # No-op: executor is now spun by the dedicated background thread.
        pass

    def spin_some(self, count: int = 8) -> None:
        # No-op: executor is now spun by the dedicated background thread.
        pass


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
        self.input_versions: dict[str, int] = {}
        self.input_arrival_times: dict[str, list[float]] = {}
        self._input_lock = threading.Lock()  # protects input_queues, last_inputs, input_arrival_times
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
        self.node_executor = None
        self.node_executor_thread: threading.Thread | None = None
        self.preview_node = PreviewNode(config.name, self.log)
        self.worker_process: subprocess.Popen | None = None
        self.worker_signature = None
        self.worker_config_path: Path | None = None
        self.worker_log_path: Path | None = None
        self.worker_pid_path: Path | None = None
        self._builtin_thread: threading.Thread | None = None
        self._builtin_stop = threading.Event()
        self.env_signature = None
        self.signature = None
        self.signature = self._signature(config)

    def update_config(self, config: CustomLwrclNodeConfig) -> None:
        if self._signature(config) != self.signature:
            self.close()
            self.config = config
            self._exec_globals = None
            self._import_signature = ""
            self.signature = self._signature(config)
        else:
            self.config = config

    def close(self) -> None:
        self._stop_builtin_thread()
        self._stop_node_executor()
        if self.lwrcl_node is not None and self.runtime.available:
            try:
                self.lwrcl_node.destroy_node()
            except Exception:
                pass
        self.lwrcl_node = None
        self.publishers = {}
        self.clients = {}
        self.subscriptions = []
        self.services = []
        self.stop_worker()

    def stop_worker(self, force: bool = False, timeout: float | None = None) -> bool:
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
        wait_timeout = 0.2 if force else (0.5 if timeout is None else max(0.0, float(timeout)))
        try:
            process.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            self._signal_worker(process, True)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
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
        with self._input_lock:
            return self.last_inputs.get(input_id, default)

    def latest_with_version(self, input_id: str, default: Any = None) -> tuple[Any, int]:
        with self._input_lock:
            return self.last_inputs.get(input_id, default), int(self.input_versions.get(input_id, 0))

    def take(self, input_id: str, default: Any = None) -> Any:
        with self._input_lock:
            queue = self.input_queues.get(input_id) or []
            if not queue:
                return default
            return queue.pop(0)

    def has_input(self, input_id: str) -> bool:
        with self._input_lock:
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
        if self.config.tool_type in {"function_generator", "image_file_input", "video_file_input", "image_view", "topic_hz_monitor", "graph_view", "image_file_save"}:
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
        self._start_node_executor()

    def _start_node_executor(self) -> None:
        if self.lwrcl_node is None:
            return
        try:
            executors = importlib.import_module("rclpy.executors")
            self.node_executor = executors.MultiThreadedExecutor(num_threads=1)
            self.node_executor.add_node(self.lwrcl_node)
            self.node_executor_thread = threading.Thread(
                target=self._spin_node_executor,
                daemon=True,
                name=f"dds-node-{self.config.id}",
            )
            self.node_executor_thread.start()
        except Exception as exc:
            self.node_executor = None
            self.node_executor_thread = None
            self.env_status = f"node executor start failed: {exc}"
            self.log(self.env_status)

    def _spin_node_executor(self) -> None:
        executor = self.node_executor
        if executor is None:
            return
        try:
            executor.spin()
        except Exception as exc:
            if self.node_executor is executor:
                name = exc.__class__.__name__
                if name != "ExternalShutdownException":
                    self.log(f"node executor error: {exc}")

    def _stop_node_executor(self) -> None:
        executor = self.node_executor
        thread = self.node_executor_thread
        self.node_executor = None
        self.node_executor_thread = None
        if executor is not None:
            try:
                if self.lwrcl_node is not None:
                    executor.remove_node(self.lwrcl_node)
            except Exception:
                pass
            try:
                executor.shutdown(timeout_sec=0.2)
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def _execute_tool(self, linked_inputs: dict[str, Any]) -> bool:
        tool = self.config.tool_type
        if self._builtin_runs_in_background():
            self._ensure_builtin_workers_once()
            if self._should_update_background_view():
                self._execute_builtin_worker_status_once()
            if not self.view:
                self.view = {"kind": "text", "status": "running in background"}
            return True
        if tool in {"function_generator", "image_file_input", "video_file_input", "image_view", "topic_hz_monitor", "graph_view", "image_file_save"}:
            self.last_outputs.clear()
            self.view = {"kind": "text", "status": "DDS topic is required; node execution is isolated in worker processes"}
            return True
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
            should_publish = self._should_publish_video_input()
            image = self._video_frame_message(force=should_publish)
            if isinstance(image, dict):
                image = self._normalize_image_message(image)
                if should_publish:
                    self.last_outputs["out1"] = image
                    self.publish("out1", image)
                    self.view = self._image_view_payload(image, self._video_input_status())
                else:
                    self.last_outputs.pop("out1", None)
                    if not self.view:
                        self.view = self._image_view_payload(image, self._video_input_status())
            else:
                self.last_outputs.pop("out1", None)
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
        if tool == "topic_hz_monitor":
            self._execute_topic_hz_monitor_once()
            return True
        if tool == "image_view":
            self._execute_image_view_once()
            return True
        if tool == "image_file_save":
            image = self.take("in1", None) or self.last_inputs.get("in1")
            path = self._save_image(image)
            self.view = {"kind": "text", "status": path or "No image to save"}
            return True
        if tool == "graph_view":
            self._execute_graph_view_once()
            return True
        return False

    def _builtin_runs_in_background(self) -> bool:
        tool = self.config.tool_type
        if self._uses_builtin_source_worker():
            return True
        if tool == "video_file_input" and self._uses_video_worker():
            return True
        if tool in {"video_file_input", "function_generator"}:
            return bool(self.publishers)
        if tool in {"image_view", "topic_hz_monitor", "graph_view", "image_file_save"} and self._uses_dds_tap_worker():
            return True
        return False

    def _ensure_builtin_workers_once(self) -> None:
        if self._uses_video_worker():
            self._ensure_video_worker()
        if self._uses_dds_tap_worker():
            self._ensure_dds_tap_worker()
        if self._uses_builtin_source_worker():
            self._ensure_builtin_source_worker()

    def _execute_builtin_worker_status_once(self) -> None:
        tool = self.config.tool_type
        if tool == "video_file_input":
            if self._uses_video_worker():
                self._update_video_worker_view()
            else:
                self._execute_source_worker_status_once()
        elif tool in {"function_generator", "image_file_input"}:
            self._execute_source_worker_status_once()
        elif tool == "image_view":
            self._execute_image_view_worker_once()
        elif tool == "topic_hz_monitor":
            self._execute_topic_hz_monitor_worker_once()
        elif tool == "graph_view":
            self._execute_graph_view_worker_once()
        elif tool == "image_file_save":
            self._execute_image_save_worker_once()

    def _ensure_builtin_thread(self) -> None:
        if self._uses_video_worker():
            self._ensure_video_worker()
        if self._uses_dds_tap_worker():
            self._ensure_dds_tap_worker()
        if self._uses_builtin_source_worker():
            self._ensure_builtin_source_worker()
        if self._builtin_thread is not None and self._builtin_thread.is_alive():
            return
        self._builtin_stop.clear()
        self._builtin_thread = threading.Thread(target=self._builtin_loop, name=f"builtin-{self.config.id}", daemon=True)
        self._builtin_thread.start()
        self.env_status = "built-in background"

    def _stop_builtin_thread(self) -> None:
        self._builtin_stop.set()
        thread = self._builtin_thread
        self._builtin_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._builtin_stop.clear()

    def _uses_video_worker(self) -> bool:
        return (
            self.config.tool_type == "video_file_input"
            and bool(self.config.params.get("serverDecode"))
            and bool(self.config.params.get("videoPath"))
            and any(port.topics for port in self.config.outputs)
        )

    def _uses_dds_tap_worker(self) -> bool:
        return self.config.tool_type in {"image_view", "topic_hz_monitor", "graph_view", "image_file_save"} and any(port.topics for port in self.config.inputs)

    def _uses_builtin_source_worker(self) -> bool:
        if self.config.tool_type == "video_file_input" and self._uses_video_worker():
            return False
        return self.config.tool_type in {"function_generator", "image_file_input", "video_file_input"} and any(port.topics for port in self.config.outputs)

    def _ensure_builtin_source_worker(self) -> None:
        signature = self._builtin_source_worker_signature()
        if self.worker_process is not None and self.worker_process.poll() is None and self.worker_signature == signature:
            self.env_status = "built-in source worker running"
            return
        self.stop_worker(force=True)
        worker_dir = Path.cwd() / ".node_workers"
        worker_dir.mkdir(parents=True, exist_ok=True)
        config_path = worker_dir / f"{self.config.id}.source.json"
        log_path = worker_dir / f"{self.config.id}.source.log"
        pid_path = worker_dir / f"{self.config.id}.source.pid"
        status_path = worker_dir / f"{self.config.id}.source.status.json"
        try:
            status_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
        except Exception:
            pass
        write_json_atomic(config_path, self._builtin_source_worker_config(status_path))
        try:
            log_file = log_path.open("a", encoding="utf-8")
            self.worker_process = subprocess.Popen(
                resolve_worker_command("builtin_source", config_path, self._worker_python()),
                cwd=Path.cwd(),
                env=self._worker_env(),
                stdout=log_file,
                stderr=log_file,
                text=True,
                start_new_session=(os.name != "nt"),
            )
            self.worker_signature = signature
            self.worker_config_path = config_path
            self.worker_log_path = log_path
            self.worker_pid_path = pid_path
            pid_path.write_text(str(self.worker_process.pid), encoding="utf-8")
            self.env_status = "built-in source worker running"
        except Exception as exc:
            self.env_status = f"built-in source worker start failed: {exc}"
            self.view = {"kind": "text", "status": self.env_status}
            self.log(self.env_status)

    def _builtin_source_worker_signature(self) -> tuple[Any, ...]:
        output = next((port for port in self.config.outputs if port.topics), None)
        return (
            self.config.id,
            self.config.tool_type,
            output.data_type if output else "",
            output.topics if output else (),
            json.dumps(self.config.params, sort_keys=True, default=str),
        )

    def _builtin_source_worker_config(self, status_path: Path) -> dict[str, Any]:
        output = next((port for port in self.config.outputs if port.topics), None)
        if output is None:
            raise RuntimeError(f"{self.config.tool_type} has no DDS output topic")
        return {
            "nodeId": self.config.id,
            "toolType": self.config.tool_type,
            "topic": output.topics[0],
            "dataType": output.data_type,
            "params": self.config.params,
            "statusPath": str(status_path),
            "externalDdsCompatible": bool(self.config.params.get("_externalDdsCompatible")),
        }

    def _ensure_dds_tap_worker(self) -> None:
        signature = self._dds_tap_worker_signature()
        if self.worker_process is not None and self.worker_process.poll() is None and self.worker_signature == signature:
            self.env_status = "DDS tap worker running"
            return
        self.stop_worker(force=True)
        worker_dir = Path.cwd() / ".node_workers"
        worker_dir.mkdir(parents=True, exist_ok=True)
        config_path = worker_dir / f"{self.config.id}.tap.json"
        log_path = worker_dir / f"{self.config.id}.tap.log"
        pid_path = worker_dir / f"{self.config.id}.tap.pid"
        status_path = worker_dir / f"{self.config.id}.tap.status.json"
        frame_path = worker_dir / f"{self.config.id}.tap.frame"
        try:
            status_path.unlink(missing_ok=True)
            frame_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
        except Exception:
            pass
        write_json_atomic(config_path, self._dds_tap_worker_config(status_path, frame_path))
        try:
            log_file = log_path.open("a", encoding="utf-8")
            self.worker_process = subprocess.Popen(
                resolve_worker_command("dds_tap", config_path, self._worker_python()),
                cwd=Path.cwd(),
                env=self._worker_env(),
                stdout=log_file,
                stderr=log_file,
                text=True,
                start_new_session=(os.name != "nt"),
            )
            self.worker_signature = signature
            self.worker_config_path = config_path
            self.worker_log_path = log_path
            self.worker_pid_path = pid_path
            pid_path.write_text(str(self.worker_process.pid), encoding="utf-8")
            self.env_status = "DDS tap worker running"
        except Exception as exc:
            self.env_status = f"DDS tap worker start failed: {exc}"
            self.view = {"kind": "text", "status": self.env_status}
            self.log(self.env_status)

    def _dds_tap_worker_signature(self) -> tuple[Any, ...]:
        input_port = next((port for port in self.config.inputs if port.topics), None)
        return (
            self.config.id,
            self.config.tool_type,
            input_port.data_type if input_port else "",
            input_port.topics if input_port else (),
            float(self.config.params.get("windowSec") or 5.0),
            float(self.config.params.get("xAxisSeconds") or 10.0),
            int(self.config.params.get("sampleLimit") or 10000),
            bool(self.config.params.get("_externalDdsCompatible")),
            "preview:bmp:max640" if self.config.tool_type == "image_view" else "preview:jpeg",
        )

    def _dds_tap_worker_config(self, status_path: Path, frame_path: Path) -> dict[str, Any]:
        input_port = next((port for port in self.config.inputs if port.topics), None)
        if input_port is None:
            raise RuntimeError(f"{self.config.tool_type} has no DDS input topic")
        return {
            "nodeId": self.config.id,
            "mode": self._dds_tap_mode(),
            "topic": input_port.topics[0],
            "dataType": input_port.data_type,
            "windowSec": max(0.5, float(self.config.params.get("windowSec") or 5.0)),
            "displayHz": GUI_DISPLAY_HZ,
            "fieldPath": str(self.config.params.get("fieldPath") or "data"),
            "sampleLimit": int(self.config.params.get("sampleLimit") or 10000),
            "graphWindowSec": max(0.1, float(self.config.params.get("xAxisSeconds") or 10.0)),
            "graphDisplayLimit": 600,
            "outputDir": str(Path.cwd() / "saved_images"),
            "statusPath": str(status_path),
            "framePath": str(frame_path),
            "externalDdsCompatible": bool(self.config.params.get("_externalDdsCompatible")),
            "previewEncoding": "bmp" if self.config.tool_type == "image_view" else "jpeg",
            "previewMaxSide": 640,
        }

    def _dds_tap_mode(self) -> str:
        if self.config.tool_type == "image_view":
            return "image"
        if self.config.tool_type == "graph_view":
            return "graph"
        if self.config.tool_type == "image_file_save":
            return "save"
        return "hz"

    def _ensure_video_worker(self) -> None:
        signature = self._video_worker_signature()
        if self.worker_signature == signature and self._video_worker_completed():
            self.env_status = "video DDS worker completed"
            self._update_video_worker_view()
            return
        if self.state.get("video_worker_failed_signature") == signature:
            self.env_status = "video DDS worker failed"
            self._update_video_worker_view()
            return
        if self.worker_process is not None and self.worker_process.poll() is None and self.worker_signature == signature:
            self.env_status = "video DDS worker running"
            self._update_video_worker_view()
            return
        if self.worker_process is not None and self.worker_signature == signature and self.worker_process.poll() is not None:
            self._update_video_worker_view()
            if "failed" in self.env_status or "error" in str(self.view.get("status", "")).lower():
                self.state["video_worker_failed_signature"] = signature
                self.env_status = "video DDS worker failed"
                return
        self.stop_worker(force=True)
        worker_dir = Path.cwd() / ".node_workers"
        worker_dir.mkdir(parents=True, exist_ok=True)
        config_path = worker_dir / f"{self.config.id}.video.json"
        log_path = worker_dir / f"{self.config.id}.video.log"
        pid_path = worker_dir / f"{self.config.id}.video.pid"
        status_path = worker_dir / f"{self.config.id}.video.status.json"
        frame_path = worker_dir / f"{self.config.id}.video.preview"
        try:
            status_path.unlink(missing_ok=True)
            frame_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
        except Exception:
            pass
        write_json_atomic(config_path, self._video_worker_config(status_path, frame_path))
        try:
            log_file = log_path.open("a", encoding="utf-8")
            self.worker_process = subprocess.Popen(
                resolve_worker_command("video", config_path, self._worker_python()),
                cwd=Path.cwd(),
                env=self._worker_env(),
                stdout=log_file,
                stderr=log_file,
                text=True,
                start_new_session=(os.name != "nt"),
            )
            self.worker_signature = signature
            self.worker_config_path = config_path
            self.worker_log_path = log_path
            self.worker_pid_path = pid_path
            pid_path.write_text(str(self.worker_process.pid), encoding="utf-8")
            self.env_status = "video DDS worker running"
            self.state.pop("video_worker_failed_signature", None)
            self.view = {"kind": "text", "status": f"{self.config.params.get('fileName') or 'video'} DDS worker starting"}
        except Exception as exc:
            self.env_status = f"video DDS worker start failed: {exc}"
            self.view = {"kind": "text", "status": self.env_status}
            self.log(self.env_status)

    def _worker_python(self) -> Path:
        if self.env_python_bin is not None:
            return self.env_python_bin
        launcher = os.environ.get("__PYVENV_LAUNCHER__")
        if launcher:
            launcher_path = Path(launcher)
            if launcher_path.exists():
                return launcher_path
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            candidate = Path(venv) / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    def _worker_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("__PYVENV_LAUNCHER__", None)
        if self.env_site_packages:
            env["LWRCLPY_EXTRA_SITE_PACKAGES"] = str(self.env_site_packages)
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{self.env_site_packages}{os.pathsep}{existing}" if existing else str(self.env_site_packages)
            )
        return env

    def _video_worker_completed(self) -> bool:
        if bool(self.config.params.get("loop", True)):
            return False
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.video.status.json"
        if not status_path.exists():
            return False
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(data.get("ended")) and not bool(data.get("running"))

    def _video_worker_signature(self) -> tuple[Any, ...]:
        return (
            self.config.id,
            self.config.params.get("videoPath"),
            float(self.config.params.get("publishHz") or 30.0),
            bool(self.config.params.get("loop", True)),
            int(self.config.params.get("maxSide") or 0),
            self._video_frame_skip(),
            bool(self.config.params.get("_externalDdsCompatible")),
            "preview:bmp:max640",
            tuple((p.id, p.data_type, p.topics) for p in self.config.outputs),
        )

    def _video_worker_config(self, status_path: Path, frame_path: Path) -> dict[str, Any]:
        output = next((port for port in self.config.outputs if port.topics), None)
        if output is None:
            raise RuntimeError("Video Input has no DDS output topic")
        return {
            "nodeId": self.config.id,
            "videoPath": str(self.config.params.get("videoPath")),
            "topic": output.topics[0],
            "dataType": output.data_type or "sensor_msgs/msg/Image",
            "publishHz": max(0.01, float(self.config.params.get("publishHz") or 30.0)),
            "useSourceFps": True,
            "loop": bool(self.config.params.get("loop", True)),
            "maxSide": int(self.config.params.get("maxSide") or 0),
            "frameSkip": self._video_frame_skip(),
            "statusPath": str(status_path),
            "framePath": str(frame_path),
            "enableDdsPublish": True,
            "externalDdsCompatible": bool(self.config.params.get("_externalDdsCompatible")),
            "previewHz": GUI_DISPLAY_HZ,
            "previewEncoding": "bmp",
            "previewMaxSide": 640,
            "outputEncoding": "jpeg" if normalize_type(output.data_type) == "sensor_msgs/msg/CompressedImage" else "raw",
        }

    def _update_video_worker_view(self) -> None:
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.video.status.json"
        status = ""
        data: dict[str, Any] | None = None
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                if data.get("error"):
                    status = str(data.get("error"))
                    self.env_status = "video DDS worker failed"
                elif data.get("ended"):
                    size = f"{data.get('width', '?')} x {data.get('height', '?')}"
                    status = f"{self.config.params.get('fileName') or 'video'} ended {size}"
                elif data.get("phase") and not data.get("published"):
                    phase = str(data.get("phase") or "starting")
                    status = f"{self.config.params.get('fileName') or 'video'} starting: {phase}"
                else:
                    size = f"{data.get('width', '?')} x {data.get('height', '?')}"
                    actual = float(data.get("actualHz") or 0.0)
                    hz = f" / {actual:.1f} Hz actual" if actual > 0 else ""
                    status = f"{self.config.params.get('fileName') or 'video'} DDS worker {size}{hz}"
            except Exception:
                status = "video DDS worker running"
        if not status:
            status = "video DDS worker running"
        frame_path = Path(str(data.get("framePath") or "")) if isinstance(data, dict) else Path("")
        seq = int(data.get("frameSeq") or 0) if isinstance(data, dict) else 0
        width = int(data.get("width") or 0) if isinstance(data, dict) else 0
        height = int(data.get("height") or 0) if isinstance(data, dict) else 0
        preview_width = int(data.get("previewWidth") or width) if isinstance(data, dict) else 0
        preview_height = int(data.get("previewHeight") or height) if isinstance(data, dict) else 0
        encoding = str(data.get("frameEncoding") or data.get("encoding") or "jpeg").lower() if isinstance(data, dict) else "jpeg"
        if seq > 0 and frame_path.is_file():
            self.state["image_view_frame"] = {
                "seq": seq,
                "width": preview_width,
                "height": preview_height,
                "sourceWidth": width,
                "sourceHeight": height,
                "encoding": encoding,
                "path": str(frame_path),
                "updatedAt": time.time(),
            }
            self.view = {
                "kind": "image",
                "frameRef": {
                    "nodeId": self.config.id,
                    "seq": seq,
                    "width": preview_width,
                    "height": preview_height,
                    "sourceWidth": width,
                    "sourceHeight": height,
                    "encoding": self.state["image_view_frame"]["encoding"],
                },
                "status": status,
            }
            return
        self.view = {"kind": "text", "status": status}

    def _builtin_loop(self) -> None:
        next_at = time.time()
        while not self._builtin_stop.is_set():
            tool = self.config.tool_type
            try:
                if tool == "video_file_input":
                    if self._uses_video_worker():
                        period = 1.0 / GUI_DISPLAY_HZ
                        self._ensure_video_worker()
                        if self._should_update_background_view():
                            self._update_video_worker_view()
                    else:
                        period = 1.0 / GUI_DISPLAY_HZ
                        self._execute_source_worker_status_once()
                elif tool == "function_generator":
                    period = 1.0 / GUI_DISPLAY_HZ
                    self._execute_source_worker_status_once()
                elif tool == "image_file_input":
                    period = 1.0 / GUI_DISPLAY_HZ
                    self._execute_source_worker_status_once()
                elif tool == "image_view":
                    period = 1.0 / GUI_DISPLAY_HZ
                    if self._uses_dds_tap_worker():
                        self._execute_image_view_worker_once()
                    else:
                        self._execute_image_view_once()
                elif tool == "topic_hz_monitor":
                    period = 1.0 / GUI_DISPLAY_HZ
                    if self._uses_dds_tap_worker():
                        self._execute_topic_hz_monitor_worker_once()
                    else:
                        self._execute_topic_hz_monitor_once()
                elif tool == "graph_view":
                    period = 1.0 / GUI_DISPLAY_HZ
                    if self._uses_dds_tap_worker():
                        self._execute_graph_view_worker_once()
                    else:
                        self._execute_graph_view_once()
                elif tool == "image_file_save":
                    period = 1.0 / 10.0
                    if self._uses_dds_tap_worker():
                        self._execute_image_save_worker_once()
                    else:
                        image = self.take("in1", None) or self.last_inputs.get("in1")
                        path = self._save_image(image)
                        self.view = {"kind": "text", "status": path or "No image to save"}
                else:
                    break
            except Exception as exc:
                self.log(f"background {tool} error: {exc}")
                period = 0.1
            next_at += period
            sleep_for = next_at - time.time()
            if sleep_for > 0:
                self._builtin_stop.wait(sleep_for)
            else:
                next_at = time.time()

    def _should_update_background_view(self) -> bool:
        hz = GUI_DISPLAY_HZ
        now = time.time()
        next_at = float(self.state.get("background_view_next_update_at") or 0.0)
        if now < next_at:
            return False
        self.state["background_view_next_update_at"] = self._next_periodic_time(next_at, 1.0 / hz, now)
        return True

    def _execute_topic_hz_monitor_once(self) -> None:
        now = time.time()
        window = max(0.5, float(self.config.params.get("windowSec") or 5.0))
        with self._input_lock:
            all_times = list(self.input_arrival_times.get("in1", []))
        cutoff = now - window
        windowed = [t for t in all_times if t >= cutoff]
        with self._input_lock:
            self.input_arrival_times["in1"] = windowed
        count = len(windowed)
        if count >= 2:
            span = max(0.001, min(window, now - windowed[0]))
            hz = count / span
            status = f"{hz:.2f} Hz  ({count} msgs / {window:.1f}s window)"
        elif count == 1:
            status = "1 msg received (waiting for more...)"
        else:
            status = "No messages received"
        self.view = {"kind": "text", "status": status}

    def _execute_topic_hz_monitor_worker_once(self) -> None:
        status = self._read_dds_tap_status()
        if not status:
            self.view = {"kind": "text", "status": "DDS tap worker starting"}
            return
        if status.get("error"):
            self.view = {"kind": "text", "status": str(status.get("error"))}
            return
        hz = float(status.get("hz") or 0.0)
        count = int(status.get("count") or 0)
        window = float(status.get("windowSec") or self.config.params.get("windowSec") or 5.0)
        if count >= 2:
            text = f"{hz:.2f} Hz  ({count} msgs / {window:.1f}s window)"
        elif count == 1:
            text = "1 msg received (waiting for more...)"
        else:
            text = "No messages received"
        self.view = {"kind": "text", "status": text}

    def _execute_source_worker_status_once(self) -> None:
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.source.status.json"
        if not status_path.exists():
            self.view = {"kind": "text", "status": "source worker starting"}
            return
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return
        text = str(status.get("status") or "source worker running")
        if self.config.tool_type == "image_file_input":
            image = self.config.params.get("imageMessage")
            if isinstance(image, dict):
                self.view = {"kind": "image", "dataUrl": self.config.params.get("dataUrl", ""), "status": text}
                return
        self.view = {"kind": "text", "status": text}

    def _execute_image_view_once(self) -> None:
        new_image, version = self.latest_with_version("in1")
        if new_image is not None and version != self.state.get("image_view_last_version"):
            if self._should_update_image_view():
                view = self._image_view_payload(new_image, self._image_status(new_image))
                if view.get("dataUrl") or view.get("raw") or view.get("frameRef"):
                    self.view = view
                    self.state["image_view_last_version"] = version
        elif not self.view:
            self.view = {"kind": "image", "dataUrl": "", "status": "No image"}

    def _execute_image_view_worker_once(self) -> None:
        status = self._read_dds_tap_status()
        if not status:
            self.view = {"kind": "image", "dataUrl": "", "status": "DDS tap worker starting"}
            return
        if status.get("error"):
            self.view = {"kind": "image", "dataUrl": "", "status": str(status.get("error"))}
            return
        frame_path = Path(str(status.get("framePath") or ""))
        seq = int(status.get("frameSeq") or 0)
        if seq <= 0 or not frame_path.exists():
            self.view = {"kind": "image", "dataUrl": "", "status": "No image"}
            return
        width = int(status.get("width") or 0)
        height = int(status.get("height") or 0)
        preview_width = int(status.get("previewWidth") or width)
        preview_height = int(status.get("previewHeight") or height)
        dds_encoding = str(status.get("ddsEncoding") or status.get("encoding") or "rgb8").lower()
        frame_encoding = str(status.get("frameEncoding") or status.get("encoding") or "rgb8").lower()
        dds_format = str(status.get("ddsFormat") or "")
        if not dds_format:
            data_type = normalize_type(str(status.get("dataType") or "sensor_msgs/msg/Image"))
            if data_type == "sensor_msgs/msg/CompressedImage":
                dds_format = f"CompressedImage/{dds_encoding or 'compressed'}"
            elif data_type == "sensor_msgs/msg/Image":
                dds_format = f"Image/{dds_encoding or 'raw'}"
            else:
                dds_format = f"{data_type}/{dds_encoding}" if dds_encoding else data_type
        frame_signature = (seq, width, height, dds_encoding, frame_encoding, str(frame_path))
        if frame_signature == self.state.get("image_view_worker_last_signature"):
            return
        self.state["image_view_worker_last_seq"] = seq
        self.state["image_view_worker_last_signature"] = frame_signature
        self.state["image_view_frame_seq"] = seq
        self.state["image_view_frame"] = {
            "seq": seq,
            "width": preview_width,
            "height": preview_height,
            "sourceWidth": width,
            "sourceHeight": height,
            "encoding": frame_encoding,
            "path": str(frame_path),
            "updatedAt": time.time(),
        }
        frame_ref = {
            "nodeId": self.config.id,
            "seq": seq,
            "width": preview_width,
            "height": preview_height,
            "sourceWidth": width,
            "sourceHeight": height,
            "encoding": self.state["image_view_frame"]["encoding"],
        }
        self.view = {
            "kind": "image",
            "frameRef": frame_ref,
            "status": f"{width} x {height} {dds_format}" if width and height else dds_format,
        }

    def _read_dds_tap_status(self) -> dict[str, Any] | None:
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.tap.status.json"
        if not status_path.exists():
            return None
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _execute_graph_view_once(self) -> None:
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

    def _execute_graph_view_worker_once(self) -> None:
        status = self._read_dds_tap_status()
        if not status:
            self.view = {"kind": "plot", "series": [], "status": "DDS tap worker starting"}
            return
        series = status.get("series")
        if not isinstance(series, list):
            series = []
        y_mode = str(self.config.params.get("yAxisMode") or "auto")
        self.view = {
            "kind": "plot",
            "series": series,
            "status": str(status.get("fieldPath") or self.config.params.get("fieldPath") or "data"),
            "xAxisSeconds": max(0.1, float(self.config.params.get("xAxisSeconds") or 10.0)),
            "yAxis": {
                "mode": "fixed" if y_mode == "fixed" else "auto",
                "min": float(self.config.params.get("yMin") if self.config.params.get("yMin") is not None else -1.0),
                "max": float(self.config.params.get("yMax") if self.config.params.get("yMax") is not None else 1.0),
            },
        }

    def _execute_image_save_worker_once(self) -> None:
        status = self._read_dds_tap_status()
        if not status:
            self.view = {"kind": "text", "status": "DDS tap worker starting"}
            return
        self.view = {"kind": "text", "status": str(status.get("savedPath") or "No image to save")}

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

    def _video_frame_message(self, force: bool = False) -> dict[str, Any] | None:
        if self.config.params.get("serverDecode") and self.config.params.get("videoPath"):
            return self._video_worker_frame_message(force=force)
        if self.config.params.get("embeddedVideo"):
            base = self.config.params.get("baseFrameMessage") or self.config.params.get("frameMessage") or self.config.params.get("imageMessage")
            if isinstance(base, dict):
                cached = self.state.get("video_file_cached_frame")
                if not force and isinstance(cached, dict):
                    return cached
                frame = self._synthetic_video_frame(base)
                self.state["video_file_cached_frame"] = frame
                return frame
        frame = self.config.params.get("frameMessage") or self.config.params.get("imageMessage")
        return frame if isinstance(frame, dict) else None

    def _video_worker_frame_message(self, force: bool = False) -> dict[str, Any] | None:
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.video.status.json"
        if not status_path.exists():
            return None
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if status.get("error"):
            return None
        if status.get("ended") and not bool(self.config.params.get("loop", True)):
            return None
        frame_path = Path(str(status.get("framePath") or ""))
        width = int(status.get("width") or 0)
        height = int(status.get("height") or 0)
        preview_width = int(status.get("previewWidth") or width)
        preview_height = int(status.get("previewHeight") or height)
        frame_encoding = str(status.get("frameEncoding") or status.get("encoding") or "jpeg").lower()
        if width <= 0 or height <= 0 or not frame_path.exists():
            return None
        try:
            stat = frame_path.stat()
        except Exception:
            return None
        status_seq = int(status.get("frameSeq") or status.get("published") or 0)
        seq = status_seq if status_seq > 0 else int(stat.st_mtime_ns)
        if seq <= 0:
            return None
        if not force and seq <= int(self.state.get("video_worker_last_seq") or 0):
            return self.state.get("video_worker_last_frame")
        self.state["video_worker_last_seq"] = seq
        if status_seq > 0:
            self.state["video_file_current_time"] = status_seq / max(0.01, float(self.config.params.get("publishHz") or 30.0))
        self.state["image_view_frame"] = {
            "seq": seq,
            "width": preview_width,
            "height": preview_height,
            "sourceWidth": width,
            "sourceHeight": height,
            "encoding": frame_encoding,
            "path": str(frame_path),
            "updatedAt": time.time(),
        }
        frame = {
            "frameRef": {
                "nodeId": self.config.id,
                "seq": seq,
                "width": preview_width,
                "height": preview_height,
                "sourceWidth": width,
                "sourceHeight": height,
                "encoding": self.state["image_view_frame"]["encoding"],
            }
        }
        self.state["video_worker_last_frame"] = frame
        return frame

    def _should_publish_video_input(self) -> bool:
        hz = self._effective_video_publish_hz()
        now = time.time()
        next_at = float(self.state.get("video_file_next_publish_at") or 0.0)
        if now < next_at:
            return False
        self.state["video_file_next_publish_at"] = self._next_periodic_time(next_at, 1.0 / hz, now)
        return True

    def _video_input_status(self) -> str:
        hz = self._effective_video_publish_hz()
        name = str(self.config.params.get("fileName") or "video")
        current = float(self.state.get("video_file_current_time") or self.config.params.get("currentTime") or 0.0)
        duration = float(self.config.params.get("duration") or 0.0)
        suffix = f"{current:.1f}s"
        if duration > 0:
            suffix += f" / {duration:.1f}s"
        return f"{name} {suffix} / {hz:g} Hz"

    def _effective_video_publish_hz(self) -> float:
        divisor = self._video_frame_skip() + 1
        status_path = Path.cwd() / ".node_workers" / f"{self.config.id}.video.status.json"
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                source_fps = float(status.get("sourceFps") or 0.0)
                if source_fps > 0:
                    return max(0.01, source_fps / divisor)
            except Exception:
                pass
        base_hz = float(
            self.config.params.get("detectedFps")
            or self.config.params.get("nativeFps")
            or self.config.params.get("sourceFps")
            or self.config.params.get("embeddedFps")
            or self.config.params.get("publishHz")
            or 30.0
        )
        return max(0.01, base_hz / divisor)

    def _video_frame_skip(self) -> int:
        try:
            return max(0, int(float(self.config.params.get("frameSkip") or 0)))
        except Exception:
            return 0

    def _synthetic_video_frame(self, base_frame: dict[str, Any]) -> dict[str, Any]:
        base = self._normalize_image_message(base_frame)
        width = int(base.get("width") or 0)
        height = int(base.get("height") or 0)
        source = self._image_rgb_bytes(base, width, height)
        if width <= 0 or height <= 0 or not source:
            return base
        now = time.time()
        if "video_file_started_at" not in self.state:
            self.state["video_file_started_at"] = now
        duration = max(0.1, float(self.config.params.get("duration") or 10.0))
        fps = max(1.0, float(self.config.params.get("embeddedFps") or self.config.params.get("publishHz") or 12.0))
        loop = bool(self.config.params.get("loop", True))
        elapsed = now - float(self.state["video_file_started_at"])
        if elapsed >= duration:
            if loop:
                elapsed = elapsed % duration
                self.state["video_file_started_at"] = now - elapsed
            else:
                elapsed = duration
        self.state["video_file_current_time"] = elapsed
        frame_index = int(elapsed * fps) * (self._video_frame_skip() + 1)
        shift = frame_index % max(1, width)
        band = (frame_index * 3) % max(1, width + height)
        output = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                src_x = (x + shift) % width
                src = (y * width + src_x) * 3
                dst = (y * width + x) * 3
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
        }

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
        fmt = self._field(image, "format")
        if fmt:
            width = self._field(image, "width") or self._compressed_image_format_int(str(fmt), "width")
            height = self._field(image, "height") or self._compressed_image_format_int(str(fmt), "height")
            return f"{width} x {height} {fmt}" if width and height else str(fmt)
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

    def _image_view_payload(self, image: Any, status: str) -> dict[str, Any]:
        if isinstance(image, dict) and isinstance(image.get("dataUrl"), str):
            return {"kind": "image", "dataUrl": image["dataUrl"], "status": status}
        if isinstance(image, dict) and isinstance(image.get("frameRef"), dict):
            return {"kind": "image", "frameRef": image["frameRef"], "status": status}
        frame_ref = self._image_frame_ref_payload(image)
        if frame_ref:
            return {"kind": "image", "frameRef": frame_ref, "status": status}
        return {"kind": "image", "dataUrl": self._image_data_url(image), "status": status}

    def _should_update_image_view(self) -> bool:
        hz = GUI_DISPLAY_HZ
        now = time.time()
        next_at = float(self.state.get("image_view_next_update_at") or 0.0)
        if now < next_at:
            return False
        self.state["image_view_next_update_at"] = self._next_periodic_time(next_at, 1.0 / hz, now)
        return True

    def _image_raw_payload(self, image: Any) -> dict[str, Any] | None:
        width = int(self._field(image, "width") or 0)
        height = int(self._field(image, "height") or 0)
        if not width or not height:
            return None
        encoding = str(self._field(image, "encoding") or "rgb8").lower()
        data = self._field(image, "data")
        if data is None:
            return None
        if isinstance(data, str) and self._field(image, "dataEncoding") == "base64" and encoding in {"rgb8", "bgr8", "mono8", "8uc1"}:
            return {"width": width, "height": height, "encoding": encoding, "data": data}
        rgb = self._image_rgb_bytes(image, width, height)
        if rgb is None:
            return None
        return {"width": width, "height": height, "encoding": "rgb8", "data": base64.b64encode(rgb).decode("ascii")}

    def _image_frame_ref_payload(self, image: Any) -> dict[str, Any] | None:
        format_text = str(self._field(image, "format") or "")
        compressed_format = format_text.lower()
        compressed_data = self._field(image, "data") if compressed_format else None
        if compressed_format.startswith(("jpeg", "jpg")) and compressed_data is not None:
            try:
                data = bytes(compressed_data)
            except Exception:
                data = bytes(max(0, min(255, int(value))) for value in compressed_data)
            seq = int(self.state.get("image_view_frame_seq") or 0) + 1
            width = int(self._field(image, "width") or self._compressed_image_format_int(format_text, "width") or 1)
            height = int(self._field(image, "height") or self._compressed_image_format_int(format_text, "height") or 1)
            self.state["image_view_frame_seq"] = seq
            self.state["image_view_frame"] = {
                "seq": seq,
                "width": width,
                "height": height,
                "encoding": "jpeg",
                "data": data,
                "updatedAt": time.time(),
            }
            return {"nodeId": self.config.id, "seq": seq, "width": width, "height": height, "encoding": "jpeg"}
        width = int(self._field(image, "width") or 0)
        height = int(self._field(image, "height") or 0)
        if not width or not height:
            return None
        encoding = str(self._field(image, "encoding") or "rgb8").lower()
        rgb = self._image_rgb_bytes(image, width, height)
        if rgb is None:
            return None
        seq = int(self.state.get("image_view_frame_seq") or 0) + 1
        self.state["image_view_frame_seq"] = seq
        self.state["image_view_frame"] = {
            "seq": seq,
            "width": width,
            "height": height,
            "encoding": "rgb8",
            "data": rgb,
            "updatedAt": time.time(),
        }
        return {"nodeId": self.config.id, "seq": seq, "width": width, "height": height, "encoding": "rgb8"}

    def _compressed_image_format_int(self, format_text: str, key: str) -> int | None:
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
        row_size = width * 3
        padding = b"\x00" * (row_stride - row_size)
        # Convert RGB -> BGR using C-level bytearray slice assignment (no Python loop)
        bgr_flat = bytearray(len(rgb))
        bgr_flat[0::3] = rgb[2::3]  # B <- R
        bgr_flat[1::3] = rgb[1::3]  # G <- G
        bgr_flat[2::3] = rgb[0::3]  # R <- B
        # Reverse rows (BMP is stored bottom-to-top) and add row padding
        mv = memoryview(bgr_flat)
        rows = [
            bytes(mv[(height - 1 - y) * row_size:(height - y) * row_size]) + padding
            for y in range(height)
        ]
        pixel_bytes = b"".join(rows)
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
            pixel_bytes,
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
            if self.config.tool_type in {"function_generator", "image_file_input", "video_file_input"} and topics:
                continue
            type_cls = import_type_class(output.data_type)
            _, kind, _ = split_type(output.data_type)
            for topic in topics:
                if kind == "msg":
                    self.publishers.setdefault(output.id, []).append(self.lwrcl_node.create_publisher(type_cls, topic, publisher_qos(output.data_type)))
                else:
                    self.clients.setdefault(output.id, []).append(self.lwrcl_node.create_client(type_cls, topic))
        for input_port in self.config.inputs:
            topics = self._port_topics(input_port)
            if self.config.tool_type in {"image_view", "topic_hz_monitor", "graph_view", "image_file_save"} and topics:
                continue
            type_cls = import_type_class(input_port.data_type)
            _, kind, _ = split_type(input_port.data_type)
            for topic in topics:
                if kind == "msg":
                    sub = self.lwrcl_node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port.id), subscriber_qos(input_port.data_type))
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
                sub = self.lwrcl_node.create_subscription(type_cls, topic, self._make_output_subscription_callback(output.id), subscriber_qos(output.data_type))
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
        if not hasattr(builtins, "__orig_import__"):
            builtins.__orig_import__ = builtins.__import__
        self._exec_globals = {
            "__builtins__": builtins,
            "print": self.log,
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
        # May be called from the background spin thread; protected by _input_lock.
        with self._input_lock:
            if self.config.tool_type == "topic_hz_monitor":
                self.input_versions[input_id] = int(self.input_versions.get(input_id, 0)) + 1
                times = self.input_arrival_times.setdefault(input_id, [])
                times.append(time.time())
                del times[:-10000]
                return
            self.last_inputs[input_id] = value
            self.input_versions[input_id] = int(self.input_versions.get(input_id, 0)) + 1
            queue = self.input_queues.setdefault(input_id, [])
            queue.append(value)
            if normalize_type(self._input_type(input_id)) == "sensor_msgs/msg/Image":
                del queue[:-2]
            else:
                del queue[:-100]
            # Record exact arrival time for Hz monitoring
            times = self.input_arrival_times.setdefault(input_id, [])
            times.append(time.time())
            del times[:-2000]

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
        self._populate_message(msg, value)
        return msg

    def _populate_message(self, msg: Any, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    if key == "data" and isinstance(item, str) and value.get("dataEncoding") == "base64":
                        item = base64.b64decode(item)
                    self._set_field(msg, key, item)
        elif hasattr(msg, "data"):
            self._set_field(msg, "data", value)

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
        self._runtime_param_overrides: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def ros(self) -> LwrclpyRuntime:
        return self.runtime

    def close(self) -> None:
        with self._lock:
            for node in self.instances.values():
                node.close()
            self.instances.clear()

    def stop(self, force: bool = False, lock_timeout: float = 1.0) -> dict[str, Any]:
        timeout = max(0.0, float(lock_timeout))
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            return {"stopped": {}, "force": force, "pending": True, "reason": "runtime busy"}
        try:
            stopped: dict[str, str] = {}
            for node_id, node in list(self.instances.items()):
                if node.stop_worker(force=force, timeout=0.5):
                    stopped[node_id] = "killed" if force else "terminated"
            self._runtime_param_overrides.clear()
            return {"stopped": stopped, "force": force}
        finally:
            self._lock.release()

    def update_node_params(self, updates: list[dict[str, Any]]) -> int:
        with self._lock:
            updated = 0
            for item in updates:
                node_id = str(item.get("nodeId") or "")
                params = item.get("params")
                if not node_id or not isinstance(params, dict):
                    continue
                current = self._runtime_param_overrides.setdefault(node_id, {})
                current.update(params)
                instance = self.instances.get(node_id)
                if instance is not None:
                    instance.config.params.update(params)
                updated += 1
            return updated

    def get_node_frame(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            instance = self.instances.get(str(node_id))
            if instance is None:
                return None
            frame = instance.state.get("image_view_frame")
            return frame if isinstance(frame, dict) else None

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._prepare_locked(payload)

    def _prepare_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.close()
        configs = [self._parse_node(node) for node in payload.get("nodes", [])]
        links = [link for link in payload.get("links", []) if self._valid_link(link, configs)]
        self._apply_link_topics(configs, links)
        self._apply_builtin_param_topics(configs)
        response_nodes: dict[str, Any] = {}
        for config in configs:
            instance = self._instance_for(config)
            ok = self._ensure_node_environment(config, instance)
            response_nodes[config.id] = {
                "meta": {"environment": instance.env_status, "logs": instance.logs[-20:]},
                "values": {},
                "view": instance.view,
            }
            if not ok:
                return {
                    "ready": False,
                    "nodes": response_nodes,
                    "lwrclpy": self.runtime.status(instance.env_status),
                    "setup": {"complete": False},
                }
        return {
            "ready": True,
            "nodes": response_nodes,
            "lwrclpy": self.runtime.status(),
            "setup": {"complete": True},
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._run_locked(payload)

    def _run_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        configs = [self._parse_node(node) for node in payload.get("nodes", [])]
        links = [link for link in payload.get("links", []) if self._valid_link(link, configs)]
        self._apply_link_topics(configs, links)
        self._apply_builtin_param_topics(configs)
        # DDS is owned by isolated node worker processes. The Web server must
        # not create its own lwrclpy participant just because graph links exist;
        # doing so competes with the workers and can make the UI transport
        # interfere with DDS delivery.
        needs_lwrclpy = False
        if needs_lwrclpy and not self.runtime.ensure_initialized():
            return {
                "nodes": {},
                "lwrclpy": self.runtime.status("lwrclpy is required for web preview topic transport"),
                "setup": {"complete": False},
            }
        active_ids = {node.id for node in configs}
        for node_id in list(self.instances.keys()):
            if node_id not in active_ids:
                self.instances.pop(node_id).close()

        response_nodes: dict[str, Any] = {}
        for config in configs:
            instance = self._instance_for(config)
            self._apply_runtime_param_overrides(instance)
            setup_ok = self._ensure_node_environment(config, instance)
            response_nodes[config.id] = {"meta": {"environment": instance.env_status, "logs": instance.logs[-20:]}, "values": {}, "view": instance.view}
            if not setup_ok:
                return {
                    "nodes": response_nodes,
                    "lwrclpy": self.runtime.status(instance.env_status),
                    "setup": {"complete": False},
                }
            if not config.tool_type:
                if instance.env_python_bin is None:
                    instance.env_status = "python venv missing"
                    return {
                        "nodes": response_nodes,
                        "lwrclpy": self.runtime.status(instance.env_status),
                        "setup": {"complete": False},
                    }
                if not self._ensure_worker_process(config, instance, instance.env_python_bin):
                    return {
                        "nodes": response_nodes,
                        "lwrclpy": self.runtime.status(instance.env_status),
                        "setup": {"complete": False},
                    }

        if needs_lwrclpy:
            self.runtime.spin_some(1)
        for config in self._sort(configs, links):
            instance = self._instance_for(config)
            self._apply_runtime_param_overrides(instance)
            meta = instance.tick({})
            if needs_lwrclpy:
                self.runtime.spin_some(1)
            response_nodes[config.id] = {"meta": meta, "values": meta.get("outputs", {}), "view": instance.view}
        if needs_lwrclpy:
            self.runtime.spin_some(2)
        return {
            "nodes": response_nodes,
            "lwrclpy": self.runtime.status(),
            "setup": {"complete": True},
        }

    def _instance_for(self, config: CustomLwrclNodeConfig) -> CustomLwrclNodeInstance:
        instance = self.instances.get(config.id)
        if instance is None:
            instance = CustomLwrclNodeInstance(config, self.runtime)
            self.instances[config.id] = instance
        else:
            instance.update_config(config)
        self._apply_runtime_param_overrides(instance)
        return instance

    def _apply_runtime_param_overrides(self, instance: CustomLwrclNodeInstance) -> None:
        params = self._runtime_param_overrides.get(instance.config.id)
        if params:
            instance.config.params.update(params)

    def _ensure_node_environment(self, config: CustomLwrclNodeConfig, instance: CustomLwrclNodeInstance) -> bool:
        env_root = Path.cwd() / ".node_envs" / config.id
        req_text = self._requirements_text_for(config)
        req_hash = hashlib.sha256(req_text.encode("utf-8")).hexdigest()
        desired_env_signature = (str(env_root), req_hash)
        hash_file = env_root / ".requirements.sha256"
        python_marker = env_root / ".python-runtime"
        lwrclpy_marker = env_root / ".lwrclpy-installed"
        req_file = env_root / "requirements.txt"
        python_bin = env_root / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
        if (
            instance.env_signature == desired_env_signature
            and instance.env_path == env_root
            and instance.env_python_bin == python_bin
            and python_bin.exists()
        ):
            if instance.env_site_packages is None:
                instance.env_site_packages = self._site_packages_for(env_root)
            instance.env_status = "ready (built-in venv)" if config.tool_type else "ready"
            return True
        uv = self._uv_command()
        if not uv:
            instance.env_status = "uv command not found"
            instance.log(instance.env_status)
            return False
        expected_python = self._python_runtime_signature()
        try:
            current_python = python_marker.read_text(encoding="utf-8") if python_marker.exists() else ""
            if python_bin.exists() and (current_python != expected_python or not self._venv_python_matches(env_root)):
                instance.stop_worker(force=True)
                shutil.rmtree(env_root, ignore_errors=True)
                instance.env_signature = None
                instance.env_python_bin = None
                instance.env_path = None
            env_root.mkdir(parents=True, exist_ok=True)
            if not python_bin.exists():
                instance.env_status = "creating venv"
                real_python = self._real_python()
                subprocess.run([uv, "venv", "--clear", "--python", real_python, str(env_root)], cwd=Path.cwd(), check=True, capture_output=True, text=True)
                python_marker.write_text(self._python_runtime_signature(real_python), encoding="utf-8")
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
            current_lwrclpy_marker = lwrclpy_marker.read_text(encoding="utf-8").strip() if lwrclpy_marker.exists() else ""
            if current_lwrclpy_marker != LWRCLPY_INSTALL_MARKER and not self._ensure_lwrclpy_in_env(python_bin, instance):
                return False
            lwrclpy_marker.write_text(LWRCLPY_INSTALL_MARKER, encoding="utf-8")
            instance.env_signature = desired_env_signature
            instance.env_status = "ready (built-in venv)" if config.tool_type else "ready"
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

    def _real_python(self) -> str:
        """Return a path to a genuine CPython interpreter.

        When running as a PyInstaller frozen binary, sys.executable points to
        the app bundle rather than a Python interpreter, which causes
        ``uv venv --python <exe>`` to fail.  In that case we resolve the real
        interpreter by version tag (e.g. ``python3.13``) from PATH or common
        system locations.
        """
        if not getattr(sys, "frozen", False):
            return sys.executable
        tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
        candidates: list[str] = []
        found = shutil.which(tag)
        if found:
            candidates.append(found)
        for candidate in [
            f"/opt/homebrew/bin/{tag}",
            f"/usr/bin/{tag}",
            f"/usr/local/bin/{tag}",
            f"/opt/homebrew/opt/python@{sys.version_info.major}.{sys.version_info.minor}/bin/{tag}",
            f"/usr/local/opt/python@{sys.version_info.major}.{sys.version_info.minor}/bin/{tag}",
        ]:
            candidates.append(candidate)
        for candidate in candidates:
            if Path(candidate).exists() and self._python_executable_matches(candidate):
                return candidate
        raise RuntimeError(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is required to create node venvs. "
            f"Install {tag} or set PATH so {tag} is found."
        )

    def _python_executable_matches(self, python_path: str | Path) -> bool:
        try:
            completed = subprocess.run(
                [
                    str(python_path),
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        return completed.stdout.strip() == f"{sys.version_info.major}.{sys.version_info.minor}"

    def _python_runtime_signature(self, python_path: str | Path | None = None) -> str:
        real_python = str(python_path or self._real_python())
        version = self._python_full_version(real_python)
        return f"{real_python}\n{version}\n"

    def _python_full_version(self, python_path: str | Path) -> str:
        try:
            completed = subprocess.run(
                [
                    str(python_path),
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout.strip()
        except Exception:
            return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def _venv_python_matches(self, env_root: Path) -> bool:
        cfg = env_root / "pyvenv.cfg"
        if not cfg.exists():
            return False
        try:
            text = cfg.read_text(encoding="utf-8")
        except Exception:
            return False
        expected = f"version_info = {sys.version_info.major}.{sys.version_info.minor}."
        return expected in text

    def _requirements_text_for(self, config: CustomLwrclNodeConfig) -> str:
        if not config.tool_type:
            return config.requirements.strip() + "\n"
        req_file = Path.cwd() / "requirements.txt"
        try:
            return req_file.read_text(encoding="utf-8").strip() + "\n"
        except Exception:
            return "pillow\nopencv-python-headless\n"

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
        installer = find_lwrclpy_installer()
        if installer is None:
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
        write_json_atomic(config_path, self._worker_config(config))
        try:
            log_file = log_path.open("a", encoding="utf-8")
            instance.worker_process = subprocess.Popen(
                resolve_worker_command("node", config_path, python_bin),
                cwd=Path.cwd(),
                env=instance._worker_env(),
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
            "externalDdsCompatible": bool(config.params.get("_externalDdsCompatible")),
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
        uv_name = "uv.exe" if sys.platform.startswith("win") else "uv"
        candidates = [
            Path(sys.executable).parent / uv_name,           # next to executable (venv or frozen)
            Path(sys.executable).parent / "_internal" / uv_name,  # PyInstaller 6 onedir layout
        ]
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.insert(0, Path(meipass) / uv_name)    # highest priority in frozen mode
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

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
        for node in nodes:
            node.params.pop("_externalDdsCompatible", None)
        source_topics: dict[tuple[str, str], str] = {}
        for link in links:
            key = (str(link.get("fromNode")), str(link.get("fromPort")))
            if key not in source_topics:
                source_topics[key] = self._link_topic(link, by_id)
            link["name"] = source_topics[key]
        input_topics: dict[tuple[str, str], set[str]] = {}
        output_topics: dict[tuple[str, str], set[str]] = {}
        external_topics: set[str] = set()
        for link in links:
            src = by_id.get(str(link.get("fromNode")))
            dst = by_id.get(str(link.get("toNode")))
            src_port = next((port for port in (src.outputs if src else []) if port.id == str(link.get("fromPort"))), None)
            dst_port = next((port for port in (dst.inputs if dst else []) if port.id == str(link.get("toPort"))), None)
            if src and dst and src_port and dst_port:
                if (
                    dst.tool_type in {"image_view", "image_file_save", "topic_hz_monitor"}
                    and normalize_type(src_port.data_type) in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
                    and normalize_type(dst_port.data_type) in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
                ):
                    dst_port.data_type = src_port.data_type
                if not src_port.data_type:
                    src_port.data_type = dst_port.data_type
                if not dst_port.data_type:
                    dst_port.data_type = src_port.data_type
            topic = source_topics.get((str(link.get("fromNode")), str(link.get("fromPort"))), "")
            if not topic:
                continue
            output_topics.setdefault((str(link.get("fromNode")), str(link.get("fromPort"))), set()).add(topic)
            input_topics.setdefault((str(link.get("toNode")), str(link.get("toPort"))), set()).add(topic)
            if (src and src.tool_type == "topic_input") or (dst and dst.tool_type == "topic_output"):
                external_topics.add(topic)
        for node in nodes:
            for port in node.inputs:
                port.topic = ""
                port.topics = tuple(sorted(input_topics.get((node.id, port.id), set())))
            for port in node.outputs:
                port.topic = ""
                port.topics = tuple(sorted(output_topics.get((node.id, port.id), set())))
        if external_topics:
            topics_by_node = {
                node.id: {topic for port in [*node.inputs, *node.outputs] for topic in port.topics}
                for node in nodes
            }
            changed = True
            while changed:
                changed = False
                for node_topics in topics_by_node.values():
                    if not node_topics.intersection(external_topics):
                        continue
                    before = len(external_topics)
                    external_topics.update(node_topics)
                    if len(external_topics) != before:
                        changed = True
        for node in nodes:
            node_topics = {topic for port in [*node.inputs, *node.outputs] for topic in port.topics}
            if node_topics.intersection(external_topics):
                node.params["_externalDdsCompatible"] = True

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
        if (
            dst.tool_type in {"image_view", "image_file_save", "topic_hz_monitor"}
            and normalize_type(src_port.data_type) in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
            and normalize_type(dst_port.data_type) in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
        ):
            return True
        return src_port.data_type == dst_port.data_type
