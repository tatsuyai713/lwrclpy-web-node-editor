from __future__ import annotations

import base64
import importlib
import io
import json
import pkgutil
import time
from dataclasses import dataclass, field
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
TOOL_TYPE_PREFIX = "tool/"


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
class CustomLwrclNodeConfig:
    id: str
    name: str
    x: int = 0
    y: int = 0
    inputs: list[PortConfig] = field(default_factory=list)
    outputs: list[PortConfig] = field(default_factory=list)
    loop_code: str = ""
    tool_type: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def normalize_type(type_name: str) -> str:
    return type_name.replace(".", "/")


def split_type(type_name: str) -> tuple[str, str, str]:
    parts = normalize_type(type_name).split("/")
    if len(parts) != 3 or parts[1] not in {"msg", "srv"}:
        raise ValueError(f"Unsupported lwrclpy type: {type_name}")
    return parts[0], parts[1], parts[2]


def is_lwrclpy_type(type_name: str) -> bool:
    return not normalize_type(type_name).startswith(TOOL_TYPE_PREFIX)


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


class CustomLwrclNodeInstance:
    def __init__(self, config: CustomLwrclNodeConfig, runtime: LwrclpyRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.state: dict[str, Any] = {}
        self.last_inputs: dict[str, Any] = {}
        self.input_queues: dict[str, list[Any]] = {}
        self.last_outputs: dict[str, Any] = {}
        self.displays: dict[str, Any] = {}
        self.logs: list[str] = []
        self.publishers: dict[str, list[Any]] = {}
        self.clients: dict[str, list[Any]] = {}
        self.subscriptions: list[Any] = []
        self.services: list[Any] = []
        self.lwrcl_node = None
        self.signature = None
        self._setup_transport()

    def update_config(self, config: CustomLwrclNodeConfig) -> None:
        if self._signature(config) != self.signature:
            self.close()
            self.config = config
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

    def tick(self, linked_inputs: dict[str, Any]) -> dict[str, Any]:
        self.runtime.spin_once()
        self.displays = {}
        for key, value in linked_inputs.items():
            if value is not None:
                self._store_input(key, value)
        merged_inputs = {**self.last_inputs, **linked_inputs}
        local_outputs: dict[str, Any] = {}
        self._execute_loop(merged_inputs, local_outputs)
        for key, value in local_outputs.items():
            self.last_outputs[key] = value
            self.publish(key, value)
        return {
            "inputs": self._repr_values(merged_inputs),
            "outputs": self._repr_values(self.last_outputs),
            "logs": self.logs[-20:],
            "lwrclpy": self.runtime.available,
        }

    def publish(self, output_id: str, value: Any) -> None:
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

    def _setup_transport(self) -> None:
        self.signature = self._signature(self.config)
        needs_transport = any(is_lwrclpy_type(port.data_type) and self._port_topics(port) for port in [*self.config.inputs, *self.config.outputs])
        if not needs_transport or not self.runtime.ensure_initialized():
            return
        self.lwrcl_node = self.runtime.rclpy.create_node(f"ipn_user_{self.config.id}".replace("-", "_")[:80])
        for output in self.config.outputs:
            if not is_lwrclpy_type(output.data_type):
                continue
            topics = self._port_topics(output)
            type_cls = import_type_class(output.data_type)
            _, kind, _ = split_type(output.data_type)
            for topic in topics:
                if kind == "msg":
                    self.publishers.setdefault(output.id, []).append(self.lwrcl_node.create_publisher(type_cls, topic, 10))
                else:
                    self.clients.setdefault(output.id, []).append(self.lwrcl_node.create_client(type_cls, topic))
        for input_port in self.config.inputs:
            if not is_lwrclpy_type(input_port.data_type):
                continue
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
        if self.runtime.executor is not None:
            self.runtime.executor.add_node(self.lwrcl_node)

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

    def _globals(self) -> dict[str, Any]:
        return {
            "__builtins__": {
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

    def _locals(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "params": self.config.params,
            "state": self.state,
            "publish": self.publish,
            "show_image": self.show_image,
            "show_video": self.show_video,
            "show_plot": self.show_plot,
            "show_text": self.show_text,
            "image_grayscale": self.image_grayscale,
            "image_resize": self.image_resize,
            "image_blur": self.image_blur,
            "image_brightness": self.image_brightness,
            "image_contrast": self.image_contrast,
            "log": self.log,
            **extra,
        }

    def show_image(self, value: Any, title: str = "Image") -> Any:
        self.displays["image"] = {"kind": "image", "title": title, "value": value}
        return value

    def show_video(self, value: Any, title: str = "Video") -> Any:
        self.displays["video"] = {"kind": "video", "title": title, "value": value}
        return value

    def show_plot(self, series: Any, title: str = "Plot") -> Any:
        self.displays["plot"] = {"kind": "plot", "title": title, "series": series}
        return series

    def show_text(self, value: Any, title: str = "Data") -> Any:
        self.displays["text"] = {"kind": "text", "title": title, "value": self._repr_value(value)}
        return value

    def image_grayscale(self, value: Any) -> Any:
        image = self._open_data_url_image(value)
        return self._image_to_data_url(image.convert("L").convert("RGB"))

    def image_resize(self, value: Any, width: int, height: int) -> Any:
        image = self._open_data_url_image(value)
        return self._image_to_data_url(image.resize((int(width), int(height))))

    def image_blur(self, value: Any, radius: float = 2.0) -> Any:
        pillow = self._pillow_modules()
        image = self._open_data_url_image(value)
        return self._image_to_data_url(image.filter(pillow["ImageFilter"].GaussianBlur(float(radius))))

    def image_brightness(self, value: Any, factor: float = 1.2) -> Any:
        pillow = self._pillow_modules()
        image = self._open_data_url_image(value)
        return self._image_to_data_url(pillow["ImageEnhance"].Brightness(image).enhance(float(factor)))

    def image_contrast(self, value: Any, factor: float = 1.2) -> Any:
        pillow = self._pillow_modules()
        image = self._open_data_url_image(value)
        return self._image_to_data_url(pillow["ImageEnhance"].Contrast(image).enhance(float(factor)))

    def _pillow_modules(self) -> dict[str, Any]:
        try:
            return {
                "Image": importlib.import_module("PIL.Image"),
                "ImageEnhance": importlib.import_module("PIL.ImageEnhance"),
                "ImageFilter": importlib.import_module("PIL.ImageFilter"),
            }
        except Exception as exc:
            raise RuntimeError("Pillow is required for image processing nodes. Install it with: .venv/bin/python -m pip install Pillow") from exc

    def _open_data_url_image(self, value: Any) -> Any:
        pillow = self._pillow_modules()
        text = str(value or "")
        if "," not in text:
            raise ValueError("Expected an image data URL")
        _, encoded = text.split(",", 1)
        return pillow["Image"].open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")

    def _image_to_data_url(self, image: Any) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _store_input(self, input_id: str, value: Any) -> None:
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-100]

    def _input_port(self, input_id: str) -> PortConfig | None:
        for port in self.config.inputs:
            if port.id == input_id:
                return port
        return None

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
        if hasattr(msg, "data"):
            msg.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    setattr(msg, key, item)
        return msg

    def _coerce_service_request(self, data_type: str, value: Any) -> Any:
        srv_cls = import_type_class(data_type)
        request = srv_cls.Request()
        if hasattr(value, "_fields_and_field_types"):
            return value
        if hasattr(request, "data"):
            request.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(request, key):
                    setattr(request, key, item)
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
            json.dumps(config.params, sort_keys=True, default=str),
            tuple((p.id, p.data_type, p.topic, p.topics, p.receive_mode) for p in config.inputs),
            tuple((p.id, p.data_type, p.topic, p.topics) for p in config.outputs),
        )


class GraphRuntime:
    def __init__(self) -> None:
        self.runtime = LwrclpyRuntime()
        self.instances: dict[str, CustomLwrclNodeInstance] = {}

    @property
    def ros(self) -> LwrclpyRuntime:
        return self.runtime

    def close(self) -> None:
        for node in self.instances.values():
            node.close()
        self.instances.clear()

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        configs = [self._parse_node(node) for node in payload.get("nodes", [])]
        links = [link for link in payload.get("links", []) if self._valid_link(link, configs)]
        self._apply_link_topics(configs, links)
        active_ids = {node.id for node in configs}
        for node_id in list(self.instances.keys()):
            if node_id not in active_ids:
                self.instances.pop(node_id).close()

        outputs: dict[str, dict[str, Any]] = {}
        response_nodes: dict[str, Any] = {}
        for config in self._sort(configs, links):
            instance = self.instances.get(config.id)
            if instance is None:
                instance = CustomLwrclNodeInstance(config, self.runtime)
                self.instances[config.id] = instance
            else:
                instance.update_config(config)
            linked_inputs = {}
            for link in links:
                if link.get("toNode") == config.id:
                    linked_inputs[str(link.get("toPort"))] = outputs.get(str(link.get("fromNode")), {}).get(str(link.get("fromPort")))
            meta = instance.tick(linked_inputs)
            outputs[config.id] = instance.last_outputs
            response_nodes[config.id] = {"meta": meta, "values": meta.get("outputs", {}), "images": instance.displays}
        return {
            "nodes": response_nodes,
            "lwrclpy": {"available": self.runtime.available, "error": self.runtime.error},
        }

    def _parse_node(self, node: dict[str, Any]) -> CustomLwrclNodeConfig:
        return CustomLwrclNodeConfig(
            id=str(node.get("id")),
            name=str(node.get("name") or node.get("id")),
            x=int(node.get("x", 0)),
            y=int(node.get("y", 0)),
            inputs=[self._parse_port(port) for port in node.get("inputs", [])],
            outputs=[self._parse_port(port) for port in node.get("outputs", [])],
            loop_code=str(node.get("loopCode", "")),
            tool_type=str(node.get("toolType", "")),
            params=dict(node.get("params", {}) if isinstance(node.get("params", {}), dict) else {}),
        )

    def _parse_port(self, port: dict[str, Any]) -> PortConfig:
        return PortConfig(
            id=str(port.get("id")),
            name=str(port.get("name") or port.get("id")),
            data_type=normalize_type(str(port.get("dataType", "std_msgs/msg/String"))),
            topic=str(port.get("topic", "")),
            receive_mode=str(port.get("receiveMode", "callback")),
            callback_code=str(port.get("callbackCode", "")),
        )

    def _apply_link_topics(self, nodes: list[CustomLwrclNodeConfig], links: list[dict[str, Any]]) -> None:
        by_id = {node.id: node for node in nodes}
        input_topics: dict[tuple[str, str], set[str]] = {}
        output_topics: dict[tuple[str, str], set[str]] = {}
        for link in links:
            topic = self._link_topic(link, by_id)
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

    def _link_topic(self, link: dict[str, Any], nodes: dict[str, CustomLwrclNodeConfig]) -> str:
        name = str(link.get("name") or "").strip()
        if not name:
            src = nodes.get(str(link.get("fromNode")))
            dst = nodes.get(str(link.get("toNode")))
            src_port = next((port for port in (src.outputs if src else []) if port.id == str(link.get("fromPort"))), None)
            dst_port = next((port for port in (dst.inputs if dst else []) if port.id == str(link.get("toPort"))), None)
            name = f"{src_port.name if src_port else link.get('fromPort')}_to_{dst_port.name if dst_port else link.get('toPort')}"
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
        return bool(src_port and dst_port and src_port.data_type == dst_port.data_type)
