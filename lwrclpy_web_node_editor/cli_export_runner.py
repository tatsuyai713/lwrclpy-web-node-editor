#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_PATH = Path(__file__).with_name("project.json")
LATEST_TAG_RELEASE_URL = "https://github.com/tatsuyai713/lwrclpy/releases/expanded_assets/latest"
GITHUB_BASE_URL = "https://github.com"


def bootstrap_venv() -> None:
    if os.environ.get("LWRCLPY_CLI_EXPORT_NO_BOOTSTRAP") == "1":
        return
    if os.environ.get("LWRCLPY_CLI_EXPORT_BOOTSTRAPPED") == "1":
        return
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return
    root = Path(__file__).resolve().parent
    venv = root / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        uv = shutil.which("uv")
        if uv:
            subprocess.check_call([uv, "venv", str(venv)])
        else:
            subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    env = os.environ.copy()
    env["LWRCLPY_CLI_EXPORT_BOOTSTRAPPED"] = "1"
    install_runtime(python)
    os.execve(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def install_runtime(python: Path) -> None:
    marker = Path(__file__).resolve().parent / ".venv" / ".lwrclpy_cli_export_installed"
    requirements = ["pillow", "opencv-python-headless", "numpy", "mcap", "mcap-ros2-support", "PyYAML"]
    if marker.exists():
        return
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", "--python", str(python), *requirements])
        subprocess.check_call([uv, "pip", "install", "--python", str(python), "--force-reinstall", "--no-cache", latest_lwrclpy_wheel_url()])
    else:
        subprocess.check_call([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        subprocess.check_call([str(python), "-m", "pip", "install", *requirements])
        subprocess.check_call([str(python), "-m", "pip", "install", "--force-reinstall", "--no-cache-dir", latest_lwrclpy_wheel_url()])
    marker.write_text(str(time.time()), encoding="utf-8")


def latest_lwrclpy_wheel_url() -> str:
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    machine = platform.machine().lower()
    request = urllib.request.Request(LATEST_TAG_RELEASE_URL, headers={"User-Agent": "lwrclpy-cli-export", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")
    candidates: list[tuple[int, str, str]] = []
    for href in re.findall(r'href="([^"]+\.whl(?:\?[^"]*)?)"', html):
        decoded = urllib.parse.unquote(href.replace("&amp;", "&"))
        path = decoded.split("?", 1)[0]
        name = path.rstrip("/").rsplit("/", 1)[-1]
        lowered = name.lower()
        if py_tag not in name:
            continue
        if system == "darwin" and "macosx" not in lowered:
            continue
        if system == "linux" and "linux" not in lowered:
            continue
        if system == "windows" and not ("win" in lowered or "windows" in lowered):
            continue
        score = 1
        if "universal2" in lowered:
            score += 1
        if machine in {"arm64", "aarch64"} and any(token in lowered for token in ("arm64", "aarch64")):
            score += 3
        if machine in {"x86_64", "amd64"} and any(token in lowered for token in ("x86_64", "amd64")):
            score += 3
        candidates.append((score, name, urllib.parse.urljoin(GITHUB_BASE_URL, decoded)))
    if not candidates:
        raise RuntimeError(f"No lwrclpy wheel found for Python {py_tag} on {platform.platform()}")
    return sorted(candidates, reverse=True)[0][2]


def import_type_class(type_name: str):
    package, kind, name = type_name.split("/")
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


def split_kind(type_name: str) -> str:
    return type_name.split("/")[1]


def set_field(msg: Any, key: str, value: Any) -> None:
    field = getattr(msg, key, None)
    if callable(field):
        try:
            field(value)
            return
        except TypeError:
            pass
    setattr(msg, key, value)


def plain_value(value: Any) -> Any:
    if callable(value):
        try:
            value = value()
        except TypeError:
            pass
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if hasattr(value, "get_buffer"):
        return value
    if value.__class__.__name__.endswith("_vector"):
        return value
    return value


def message_to_value(msg: Any) -> Any:
    fields = getattr(msg, "_fields_and_field_types", None)
    if not fields and hasattr(msg, "get_fields_and_field_types"):
        try:
            fields = msg.get_fields_and_field_types()
        except Exception:
            fields = None
    if not fields:
        image_keys = ("height", "width", "encoding", "is_bigendian", "step", "data")
        if all(hasattr(msg, key) for key in ("height", "width", "encoding", "data")):
            return {key: plain_value(getattr(msg, key, None)) for key in image_keys if hasattr(msg, key)}
        if hasattr(msg, "data"):
            return {"data": plain_value(getattr(msg, "data"))}
        return msg
    return {key: plain_value(getattr(msg, key, None)) for key in fields}


def coerce_message(data_type: str, value: Any) -> Any:
    if hasattr(value, "_fields_and_field_types"):
        return value
    msg = import_type_class(data_type)()
    if isinstance(value, dict):
        for key, item in value.items():
            if hasattr(msg, key):
                set_field(msg, key, item)
    elif hasattr(msg, "data"):
        set_field(msg, "data", value)
    return msg


def normalize_topic(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return text if text.startswith("/") else f"/{text}"


def default_topic(src: str, src_port: str, dst: str, dst_port: str) -> str:
    return f"/{src}_{src_port}_to_{dst}_{dst_port}"


def apply_link_topics(project: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [json.loads(json.dumps(node)) for node in project.get("nodes", []) if isinstance(node, dict)]
    by_id = {str(node.get("id")): node for node in nodes}
    for node in nodes:
        for port in node.get("inputs", []):
            port["topics"] = []
        for port in node.get("outputs", []):
            port["topics"] = []
    for link in project.get("links", []):
        if not isinstance(link, dict):
            continue
        src = by_id.get(str(link.get("fromNode")))
        dst = by_id.get(str(link.get("toNode")))
        if not src or not dst:
            continue
        topic = normalize_topic(link.get("name") or default_topic(link.get("fromNode"), link.get("fromPort"), link.get("toNode"), link.get("toPort")))
        for port in src.get("outputs", []):
            if port.get("id") == link.get("fromPort"):
                port.setdefault("topics", []).append(topic)
        for port in dst.get("inputs", []):
            if port.get("id") == link.get("toPort"):
                port.setdefault("topics", []).append(topic)
    for node in nodes:
        params = node.setdefault("params", {})
        tool = node.get("toolType")
        if tool in {"video_file_input", "image_file_input", "function_generator"}:
            outputs = [topic for port in node.get("outputs", []) for topic in port.get("topics", [])]
            if outputs:
                params["topic"] = outputs[0]
        if tool in {"image_view", "topic_hz_monitor", "image_file_save", "string_view"}:
            inputs = [topic for port in node.get("inputs", []) for topic in port.get("topics", [])]
            if inputs:
                params["topic"] = inputs[0]
    return nodes


class RuntimeNode:
    def __init__(self, rclpy: Any, config: dict[str, Any]) -> None:
        self.rclpy = rclpy
        self.config = config
        self.params = config.get("params") if isinstance(config.get("params"), dict) else {}
        self.tool = str(config.get("toolType") or "")
        self.node = rclpy.create_node(str(config.get("name") or config.get("id") or "exported_node"))
        self.state: dict[str, Any] = {}
        self.last_inputs: dict[str, Any] = {}
        self.input_queues: dict[str, list[Any]] = {}
        self.publishers: dict[str, list[Any]] = {}
        self.next_timer_at = 0.0
        self.next_builtin_at = 0.0
        self.video_capture = None
        self.mcap_thread: threading.Thread | None = None
        self.mcap_record_process: subprocess.Popen | None = None
        self.video_period = 1.0 / max(0.1, float(self.params.get("publishHz") or 30.0))
        self._globals_cache: dict[str, Any] | None = None
        self._setup_transport()

    def _setup_transport(self) -> None:
        if self.tool in {"video_file_input", "image_file_input", "function_generator"}:
            topic = normalize_topic(self.params.get("topic"))
            if topic and self.config.get("outputs"):
                output = self.config["outputs"][0]
                self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(import_type_class(output["dataType"]), topic, 10))
            return
        for output in self.config.get("outputs", []):
            type_cls = import_type_class(output["dataType"])
            for topic in output.get("topics", []):
                if split_kind(output["dataType"]) == "msg":
                    self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(type_cls, topic, 10))
        for input_port in self.config.get("inputs", []):
            type_cls = import_type_class(input_port["dataType"])
            for topic in input_port.get("topics", []):
                self.node.create_subscription(type_cls, topic, self._make_callback(input_port), 10)
        if self.tool in {"image_view", "topic_hz_monitor", "image_file_save", "string_view"}:
            topic = normalize_topic(self.params.get("topic"))
            data_type = self.config.get("inputs", [{}])[0].get("dataType", "sensor_msgs/msg/Image")
            if topic:
                self.node.create_subscription(import_type_class(data_type), topic, self._make_callback({"id": "in1", "receiveMode": "manual", "dataType": data_type}), 10)

    def _make_callback(self, input_port: dict[str, Any]):
        def callback(msg: Any) -> None:
            if self.tool == "mcap_record":
                return
            value = message_to_value(msg)
            self.last_inputs[input_port["id"]] = value
            queue = self.input_queues.setdefault(input_port["id"], [])
            queue.append(value)
            del queue[:-100]
            if self.tool == "image_crop_resize":
                out = self._crop_resize(value)
                if out:
                    self.publish("out1", out)
                return
            if self.tool == "llm_text":
                prompt = self._text_from_value(value)
                if prompt:
                    try:
                        response = self._run_llm(prompt)
                    except Exception as exc:
                        print(f"[{self.config.get('name')}] LLM error: {exc}")
                        response = ""
                    if response:
                        self.publish("response", {"data": response})
                return
            if self.tool == "image_file_save":
                self._save_image(value)
                return
            if self.tool == "string_view":
                data = value.get("data") if isinstance(value, dict) else value
                print(f"[{self.config.get('name')}] {data}")
                return
            if input_port.get("receiveMode", "callback") == "callback":
                outputs: dict[str, Any] = {}
                code = str(input_port.get("callbackCode") or "").strip()
                if code:
                    exec(code, self._globals(), self._locals({"msg": value, "input_id": input_port["id"], "outputs": outputs}))
                    for key, item in outputs.items():
                        self.publish(key, item)
        return callback

    def publish(self, output_id: str, value: Any) -> None:
        output = next((item for item in self.config.get("outputs", []) if item.get("id") == output_id), None)
        if not output:
            return
        msg = coerce_message(output["dataType"], value)
        for publisher in self.publishers.get(output_id, []):
            publisher.publish(msg)

    def tick(self) -> None:
        now = time.time()
        if self.tool == "function_generator" and now >= self.next_builtin_at:
            self._tick_function_generator(now)
        elif self.tool == "video_file_input" and now >= self.next_builtin_at:
            self._tick_video()
        elif self.tool == "image_file_input" and not self.state.get("image_published"):
            image = self.params.get("imageMessage")
            if image:
                self.publish("out1", image)
                self.state["image_published"] = True
        elif self.tool == "mcap_file_input" and self.mcap_thread is None:
            self.mcap_thread = threading.Thread(target=self._run_mcap_input, name=f"mcap-input-{self.config.get('id')}", daemon=True)
            self.mcap_thread.start()
        elif self.tool == "mcap_record" and self.mcap_record_process is None:
            self._start_mcap_record_process()
        elif self.tool == "topic_hz_monitor":
            self._tick_hz(now)
        self._tick_timer(now)
        self._tick_loop()

    def _tick_timer(self, now: float) -> None:
        if not self.config.get("timerEnabled"):
            return
        period = max(0.001, float(self.config.get("timerPeriodSec") or 1.0))
        if self.next_timer_at <= 0:
            self.next_timer_at = now
        if now < self.next_timer_at:
            return
        self.next_timer_at = now + period
        outputs: dict[str, Any] = {}
        code = str(self.config.get("timerCode") or "").strip()
        if code:
            exec(code, self._globals(), self._locals({"outputs": outputs, "now": now, "period": period}))
        for key, item in outputs.items():
            self.publish(key, item)

    def _tick_loop(self) -> None:
        code = str(self.config.get("loopCode") or "").strip()
        if not code:
            return
        outputs: dict[str, Any] = {}
        exec(code, self._globals(), self._locals({"inputs": dict(self.last_inputs), "outputs": outputs, "now": time.time()}))
        for key, item in outputs.items():
            self.publish(key, item)

    def _tick_function_generator(self, now: float) -> None:
        hz = max(0.1, float(self.params.get("publishHz") or 10.0))
        self.next_builtin_at = now + 1.0 / hz
        elapsed = now - float(self.state.setdefault("started_at", now))
        amp = float(self.params.get("amplitude") or 1.0)
        freq = float(self.params.get("frequency") or 1.0)
        offset = float(self.params.get("offset") or 0.0)
        signal_type = str(self.params.get("signalType") or "sine")
        phase = 2.0 * math.pi * freq * elapsed
        if signal_type == "square":
            value = offset + amp * (1.0 if math.sin(phase) >= 0 else -1.0)
        elif signal_type == "triangle":
            value = offset + amp * (2.0 / math.pi) * math.asin(math.sin(phase))
        else:
            value = offset + amp * math.sin(phase)
        self.publish("out1", {"data": float(value)})

    def _tick_video(self) -> None:
        import cv2
        self.next_builtin_at = time.time() + self.video_period
        path = str(self.params.get("videoPath") or "")
        if not path:
            return
        if self.video_capture is None:
            self.video_capture = cv2.VideoCapture(path)
        ok, frame = self.video_capture.read()
        if not ok:
            if self.params.get("loop", True):
                self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.video_capture.read()
            if not ok:
                return
        frame_skip = max(0, int(float(self.params.get("frameSkip") or 0)))
        for _ in range(frame_skip):
            self.video_capture.grab()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        max_side = int(self.params.get("maxSide") or 0)
        if max_side > 0 and max(rgb.shape[:2]) > max_side:
            h, w = rgb.shape[:2]
            scale = max_side / max(h, w)
            rgb = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        rgb = rgb.copy()
        h, w = rgb.shape[:2]
        self.publish("out1", {"height": h, "width": w, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": rgb.tobytes()})

    def _mcap_input_files(self) -> list[Path]:
        path = Path(str(self.params.get("mcapPath") or "")).expanduser()
        if path.is_file() and path.suffix.lower() == ".mcap":
            return [path]
        if path.is_file() and path.name in {"metadata.yaml", "metadata.yml", "metadata.json"}:
            path = path.parent
        if not path.is_dir():
            raise RuntimeError(f"MCAP path does not exist: {path}")
        files = sorted(path.glob("*.mcap"), key=lambda item: item.name)
        if not files:
            raise RuntimeError(f"MCAP directory has no .mcap files: {path}")
        return files

    def _run_mcap_input(self) -> None:
        try:
            from mcap_ros2.reader import read_ros2_messages
            playback_rate = max(0.001, float(self.params.get("playbackRate") or 1.0))
            loop = bool(self.params.get("loop", False))
            topic_by_port = {
                str(port.get("id")): str((self.params.get("mcapOutputTopics") or {}).get(port.get("id")) or port.get("name") or "")
                for port in self.config.get("outputs", [])
            }
            ports_by_topic: dict[str, list[str]] = {}
            for port_id, topic in topic_by_port.items():
                if topic:
                    ports_by_topic.setdefault(topic, []).append(port_id)
            selected_topics = sorted(ports_by_topic)
            if not selected_topics:
                print(f"[{self.config.get('name')}] no MCAP output topics")
                return
            files = self._mcap_input_files()
            while True:
                first_log_time: int | None = None
                wall_start = time.monotonic()
                played_any = False
                for file_path in files:
                    for item in read_ros2_messages(str(file_path), topics=selected_topics, log_time_order=True):
                        log_time = int(getattr(item, "log_time_ns", 0) or getattr(getattr(item, "message", None), "log_time", 0) or 0)
                        if first_log_time is None:
                            first_log_time = log_time
                            wall_start = time.monotonic()
                        wait_sec = ((log_time - first_log_time) / 1e9) / playback_rate
                        target = wall_start + max(0.0, wait_sec)
                        while True:
                            remaining = target - time.monotonic()
                            if remaining <= 0:
                                break
                            time.sleep(min(0.01, remaining))
                        item_topic = str(getattr(item.channel, "topic", ""))
                        for port_id in ports_by_topic.get(item_topic, []):
                            self.publish(port_id, item.ros_msg)
                            played_any = True
                if not loop or not played_any:
                    return
        except Exception as exc:
            print(f"[{self.config.get('name')}] MCAP input error: {exc}")

    def _start_mcap_record_process(self) -> None:
        ros2 = shutil.which("ros2")
        if not ros2:
            print(f"[{self.config.get('name')}] ros2 command not found; MCAP record export requires a ROS 2 environment")
            self.mcap_record_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10**9)"])
            return
        output_path = str(self.params.get("mcapPath") or self.params.get("recordPath") or "").strip()
        if not output_path:
            print(f"[{self.config.get('name')}] no MCAP record output path")
            self.mcap_record_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10**9)"])
            return
        topics = sorted({
            str(topic)
            for port in self.config.get("inputs", [])
            for topic in port.get("topics", [])
            if str(topic)
        })
        if not topics:
            print(f"[{self.config.get('name')}] no topics to record")
            self.mcap_record_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10**9)"])
            return
        command = [ros2, "bag", "record", "-s", "mcap", "-o", output_path, *topics]
        split_size_mb = float(self.params.get("splitSizeMb") or 0)
        if split_size_mb > 0:
            command[3:3] = ["--max-bag-size", str(int(split_size_mb * 1024 * 1024))]
        print(f"[{self.config.get('name')}] starting: {' '.join(command)}")
        self.mcap_record_process = subprocess.Popen(command)

    def close(self) -> None:
        if self.video_capture is not None:
            try:
                self.video_capture.release()
            except Exception:
                pass
        if self.mcap_record_process is not None and self.mcap_record_process.poll() is None:
            self.mcap_record_process.terminate()
            try:
                self.mcap_record_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mcap_record_process.kill()

    def _crop_resize(self, img: dict[str, Any]) -> dict[str, Any] | None:
        import cv2
        import numpy as np
        data = img.get("data") or b""
        h = int(img.get("height") or 0)
        w = int(img.get("width") or 0)
        enc = str(img.get("encoding") or "rgb8").lower()
        if h <= 0 or w <= 0:
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        channels = 1 if enc == "mono8" else 3
        if arr.size < h * w * channels:
            return None
        arr = arr[:h * w * channels].reshape((h, w, channels))
        if enc == "rgb8":
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif enc == "mono8":
            bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        else:
            bgr = arr.copy()
        out = bgr
        crop_h, crop_w = out.shape[:2]
        if self.params.get("cropEnabled"):
            x = self._positive_int(self.params.get("cropX"))
            y = self._positive_int(self.params.get("cropY"))
            cw = self._positive_int(self.params.get("cropWidth"))
            ch = self._positive_int(self.params.get("cropHeight"))
            if cw <= 0:
                cw = crop_w - x
            if ch <= 0:
                ch = crop_h - y
            cw = max(1, min(cw, crop_w))
            ch = max(1, min(ch, crop_h))
            if self.params.get("cropCenter"):
                x = max(0, (crop_w - cw) // 2)
                y = max(0, (crop_h - ch) // 2)
            else:
                x = min(max(0, x), max(0, crop_w - cw))
                y = min(max(0, y), max(0, crop_h - ch))
            out = out[y:y + ch, x:x + cw]
        if self.params.get("resizeEnabled"):
            target_w = int(float(self.params.get("targetWidth") or 0))
            target_h = int(float(self.params.get("targetHeight") or 0))
            if self.params.get("keepAspect", True) and target_w > 0:
                target_h = max(1, round(target_w * out.shape[0] / out.shape[1]))
            if target_w > 0 and target_h > 0:
                out = cv2.resize(out, (target_w, target_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB).copy()
        oh, ow = rgb.shape[:2]
        return {"height": oh, "width": ow, "encoding": "rgb8", "is_bigendian": 0, "step": ow * 3, "data": rgb.tobytes()}

    def _positive_int(self, value: Any) -> int:
        try:
            return max(0, int(float(value)))
        except Exception:
            return 0

    def _text_from_value(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("data") or "")
        return str(getattr(value, "data", value) or "")

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 60.0) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **(headers or {})}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _chat_messages(self, system_prompt: str, prompt: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if str(system_prompt or "").strip():
            messages.append({"role": "system", "content": str(system_prompt)})
        messages.append({"role": "user", "content": str(prompt)})
        return messages

    def _run_llm(self, prompt: str) -> str:
        provider = str(self.params.get("provider") or "ollama").lower()
        model = str(self.params.get("model") or ("llama3.2" if provider == "ollama" else "gpt-4o-mini"))
        system_prompt = str(self.params.get("systemPrompt") or "")
        temperature = float(self.params.get("temperature", 0.2) or 0.0)
        max_tokens = int(float(self.params.get("maxTokens", 512) or 512))
        timeout = max(1.0, float(self.params.get("timeoutSec", 60) or 60))
        api_base = str(self.params.get("apiBase") or "").rstrip("/")
        if provider == "ollama":
            api_base = api_base or "http://127.0.0.1:11434"
            payload = {
                "model": model,
                "messages": self._chat_messages(system_prompt, prompt),
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            result = self._post_json(api_base + "/api/chat", payload, timeout=timeout)
            return str((result.get("message") or {}).get("content") or "")
        if provider in {"openai", "openai_compatible", "lmstudio"}:
            api_base = api_base or ("http://127.0.0.1:1234/v1" if provider == "lmstudio" else "https://api.openai.com/v1")
            api_key_env = str(self.params.get("apiKeyEnv") or ("OPENAI_API_KEY" if provider == "openai" else ""))
            headers = {}
            if api_key_env:
                api_key = os.environ.get(api_key_env, "")
                if api_key:
                    headers["authorization"] = "Bearer " + api_key
            payload = {
                "model": model,
                "messages": self._chat_messages(system_prompt, prompt),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            result = self._post_json(api_base + "/chat/completions", payload, headers=headers, timeout=timeout)
            choices = result.get("choices") or []
            if choices:
                return str((choices[0].get("message") or {}).get("content") or "")
            return ""
        raise RuntimeError("Unsupported LLM provider: " + provider)

    def _tick_hz(self, now: float) -> None:
        count = int(self.state.get("hz_count") or 0)
        last = float(self.state.get("hz_last_print") or 0.0)
        if self.last_inputs:
            count += 1
            self.state["hz_count"] = count
        if now - last >= 1.0:
            print(f"[{self.config.get('name')}] approx count={count}/s")
            self.state["hz_count"] = 0
            self.state["hz_last_print"] = now

    def _save_image(self, img: dict[str, Any]) -> None:
        import cv2
        import numpy as np
        if not isinstance(img, dict):
            return
        data = img.get("data") or b""
        h = int(img.get("height") or 0)
        w = int(img.get("width") or 0)
        enc = str(img.get("encoding") or "rgb8").lower()
        if h <= 0 or w <= 0:
            return
        channels = 1 if enc == "mono8" else 3
        arr = np.frombuffer(data, dtype=np.uint8)
        if arr.size < h * w * channels:
            return
        arr = arr[:h * w * channels].reshape((h, w, channels))
        if enc == "rgb8":
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif enc == "mono8":
            bgr = arr
        else:
            bgr = arr
        out_dir = Path("saved_images")
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{self.config.get('id', 'image')}_{int(time.time() * 1000)}.png"
        cv2.imwrite(str(path), bgr)
        print(f"[{self.config.get('name')}] saved {path}")

    def _globals(self) -> dict[str, Any]:
        if self._globals_cache is not None:
            return self._globals_cache
        globals_dict = {"__builtins__": __builtins__}
        code = str(self.config.get("importCode") or "").strip()
        if code:
            exec(code, globals_dict, globals_dict)
        self._globals_cache = globals_dict
        return globals_dict

    def _locals(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "node": self.node,
            "params": self.params,
            "state": self.state,
            "publish": self.publish,
            "log": lambda *values: print(f"[{self.config.get('name')}]", *values),
            "latest": lambda input_id, default=None: self.last_inputs.get(input_id, default),
            "take": self.take,
            "has_input": lambda input_id: bool(self.input_queues.get(input_id)),
            **extra,
        }

    def take(self, input_id: str, default: Any = None) -> Any:
        queue = self.input_queues.get(input_id) or []
        return queue.pop(0) if queue else default


def main() -> int:
    bootstrap_venv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(PROJECT_PATH))
    parser.add_argument("--duration", "-d", type=float, default=None)
    parser.add_argument("--hz", type=float, default=60.0)
    args = parser.parse_args()
    project = json.loads(Path(args.project).read_text(encoding="utf-8"))
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    rclpy.init(args=None)
    nodes = [RuntimeNode(rclpy, node) for node in apply_link_topics(project)]
    executor = MultiThreadedExecutor()
    for node in nodes:
        executor.add_node(node.node)
    started = time.time()
    period = 1.0 / max(1.0, float(args.hz))
    next_at = time.perf_counter()
    try:
        while rclpy.ok():
            if args.duration is not None and time.time() - started >= args.duration:
                break
            executor.spin_once(timeout_sec=0.001)
            now = time.perf_counter()
            if now >= next_at:
                for node in nodes:
                    node.tick()
                next_at = now + period
            else:
                time.sleep(min(0.002, next_at - now))
    finally:
        for node in nodes:
            node.close()
            executor.remove_node(node.node)
            node.node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
