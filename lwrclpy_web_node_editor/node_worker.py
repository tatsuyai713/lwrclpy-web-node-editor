from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


os.environ.setdefault("LWRCLPY_NO_DATASHARING", "1")

def import_type_class(type_name: str):
    package, kind, name = type_name.split("/")
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


def split_kind(type_name: str) -> str:
    return type_name.split("/")[1]


def topic_qos(data_type: str) -> Any:
    if data_type.replace(".", "/") not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
        return 10
    try:
        qos = importlib.import_module("rclpy.qos")
        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=4,
            reliability=qos.ReliabilityPolicy.RELIABLE,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return 1


class LwrclpyWorkerNode:
    def __init__(self, config: dict[str, Any]) -> None:
        import rclpy
        from rclpy.executors import MultiThreadedExecutor

        self.rclpy = rclpy
        self.executor = MultiThreadedExecutor()
        self.config = config
        self.node_config = config["node"]
        self.port_topics = config.get("portTopics", {"inputs": {}, "outputs": {}})
        self.state: dict[str, Any] = {}
        self.last_inputs: dict[str, Any] = {}
        self.input_queues: dict[str, list[Any]] = {}
        self.last_outputs: dict[str, Any] = {}
        self.next_timer_at = 0.0
        self.next_timer_by_id: dict[str, float] = {}
        self.publishers: dict[str, list[Any]] = {}
        self.clients: dict[str, list[Any]] = {}
        self.subscriptions: list[Any] = []
        self.services: list[Any] = []
        self._globals_cache: dict[str, Any] | None = None
        if not self.rclpy.ok():
            self.rclpy.init(args=None)
        self.node = self.rclpy.create_node(self.node_config["name"])
        self._setup_transport()
        self.executor.add_node(self.node)

    def _setup_transport(self) -> None:
        for output in self.node_config.get("outputs", []):
            type_cls = import_type_class(output["dataType"])
            for topic in self.port_topics.get("outputs", {}).get(output["id"], []):
                if split_kind(output["dataType"]) == "msg":
                    self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(type_cls, topic, topic_qos(output["dataType"])))
                else:
                    self.clients.setdefault(output["id"], []).append(self.node.create_client(type_cls, topic))
        for input_port in self.node_config.get("inputs", []):
            type_cls = import_type_class(input_port["dataType"])
            for topic in self.port_topics.get("inputs", {}).get(input_port["id"], []):
                if split_kind(input_port["dataType"]) == "msg":
                    self.subscriptions.append(self.node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port), topic_qos(input_port["dataType"])))
                else:
                    self.services.append(self.node.create_service(type_cls, topic, self._make_service_callback(input_port)))

    def publish(self, output_id: str, value: Any) -> None:
        self.last_outputs[output_id] = value
        output = self._output_port(output_id)
        if output is None:
            return
        if output_id in self.publishers:
            msg = self._coerce_message(output["dataType"], value)
            for publisher in self.publishers[output_id]:
                publisher.publish(msg)
        if output_id in self.clients:
            request = self._coerce_service_request(output["dataType"], value)
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
        print(f"[{self.node_config['name']}]", *values, flush=True)

    def spin_tick(self) -> None:
        outputs: dict[str, Any] = {}
        inputs = dict(self.last_inputs)
        self._execute_timer_if_due(inputs, outputs)
        self._execute_loop(inputs, outputs)
        self._flush_outputs(outputs)

    def _make_subscription_callback(self, input_port: dict[str, Any]):
        def callback(msg):
            value = self._message_to_value(msg)
            self._store_input(input_port["id"], value)
            if input_port.get("receiveMode", "callback") != "callback":
                return
            outputs: dict[str, Any] = {}
            self._execute_callback(input_port, value, None, outputs)
            self._flush_outputs(outputs)

        return callback

    def _make_service_callback(self, input_port: dict[str, Any]):
        def callback(request, response):
            self._store_input(input_port["id"], request)
            outputs: dict[str, Any] = {}
            if input_port.get("receiveMode", "callback") == "callback":
                self._execute_callback(input_port, request, response, outputs)
            self._flush_outputs(outputs)
            return response

        return callback

    def _execute_callback(self, input_port: dict[str, Any], msg: Any, response: Any, outputs: dict[str, Any]) -> None:
        code = input_port.get("callbackCode", "").strip()
        if not code:
            return
        local = self._locals({"input_id": input_port["id"], "msg": msg, "request": msg, "response": response, "outputs": outputs})
        try:
            exec(code, self._globals(), local)
        except Exception as exc:
            self.log(f"{input_port['id']} callback error: {exc}")

    def _execute_loop(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        code = self.node_config.get("loopCode", "").strip()
        if not code:
            return
        local = self._locals({"inputs": inputs, "outputs": outputs, "now": time.time(), "latest": self.latest, "take": self.take, "has_input": self.has_input})
        try:
            exec(code, self._globals(), local)
        except Exception as exc:
            self.log(f"loop error: {exc}")

    def _execute_timer_if_due(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        now = time.time()
        for timer in self._timers():
            code = str(timer.get("callbackCode") or "").strip()
            if not code:
                continue
            timer_id = str(timer.get("id") or "timer1")
            period = max(0.001, float(timer.get("periodSec", 1.0) or 1.0))
            next_at = self.next_timer_by_id.get(timer_id, 0.0)
            if next_at <= 0:
                next_at = now
            if now < next_at:
                self.next_timer_by_id[timer_id] = next_at
                continue
            next_due = (next_at if next_at > 0 else now) + period
            while next_due <= now:
                next_due += period
            self.next_timer_by_id[timer_id] = next_due
            local = self._locals({
                "timer_id": timer_id,
                "timer_name": str(timer.get("name") or timer_id),
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
                self.log(f"{timer_id} timer callback error: {exc}")

    def _timers(self) -> list[dict[str, Any]]:
        timers = self.node_config.get("timers")
        if isinstance(timers, list):
            return [timer for timer in timers if isinstance(timer, dict)]
        if self.node_config.get("timerEnabled", False):
            return [{
                "id": "timer1",
                "name": "timer1",
                "periodSec": self.node_config.get("timerPeriodSec", 1.0),
                "callbackCode": self.node_config.get("timerCode", ""),
            }]
        return []

    def _flush_outputs(self, outputs: dict[str, Any]) -> None:
        for key, value in outputs.items():
            self.publish(key, value)

    def _globals(self) -> dict[str, Any]:
        if self._globals_cache is not None:
            return self._globals_cache
        globals_dict = {
            "__builtins__": {
                "__import__": __import__,
                "abs": abs,
                "bool": bool,
                "bytes": bytes,
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
            }
        }
        import_code = self.node_config.get("importCode", "").strip()
        if import_code:
            try:
                exec(import_code, globals_dict, globals_dict)
            except Exception as exc:
                self.log(f"import setup error: {exc}")
        self._globals_cache = globals_dict
        return globals_dict

    def _locals(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {"node": self.node, "params": self.node_config.get("params", {}), "state": self.state, "publish": self.publish, "log": self.log, **extra}

    def _store_input(self, input_id: str, value: Any) -> None:
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-100]

    def _message_to_value(self, value: Any) -> Any:
        fields = getattr(value, "_fields_and_field_types", None)
        if not fields and hasattr(value, "get_fields_and_field_types"):
            try:
                fields = value.get_fields_and_field_types()
            except Exception:
                fields = None
        if not fields:
            image_keys = ("height", "width", "encoding", "is_bigendian", "step", "data")
            if all(hasattr(value, key) for key in ("height", "width", "encoding", "data")):
                return {key: self._plain_value(getattr(value, key, None)) for key in image_keys if hasattr(value, key)}
            if hasattr(value, "data"):
                return {"data": self._plain_value(getattr(value, "data"))}
            return value
        result: dict[str, Any] = {}
        for key in fields:
            item = getattr(value, key, None)
            if hasattr(item, "_fields_and_field_types"):
                item = self._message_to_value(item)
            else:
                item = self._plain_value(item)
            result[key] = item
        return result

    def _plain_value(self, value: Any) -> Any:
        if callable(value):
            try:
                value = value()
            except TypeError:
                pass
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if hasattr(value, "tolist"):
            try:
                return value.tolist()
            except Exception:
                pass
        return value

    def _set_field(self, target: Any, key: str, value: Any) -> None:
        field = getattr(target, key, None)
        if callable(field):
            try:
                field(value)
                return
            except TypeError:
                pass
        setattr(target, key, value)

    def _output_port(self, output_id: str) -> dict[str, Any] | None:
        for output in self.node_config.get("outputs", []):
            if output["id"] == output_id:
                return output
        return None

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
                    self._set_field(msg, key, item)
        elif hasattr(msg, "data"):
            self._set_field(msg, "data", value)

    def _coerce_service_request(self, data_type: str, value: Any) -> Any:
        srv_cls = import_type_class(data_type)
        request = srv_cls.Request()
        if hasattr(value, "_fields_and_field_types"):
            return value
        if isinstance(value, dict):
            for key, item in value.items():
                if hasattr(request, key):
                    self._set_field(request, key, item)
        elif hasattr(request, "data"):
            self._set_field(request, "data", value)
        return request

    def close(self) -> None:
        self.executor.remove_node(self.node)
        self.node.destroy_node()
        self.rclpy.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: node_worker.py CONFIG_JSON", file=sys.stderr)
        return 2
    config = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    worker = LwrclpyWorkerNode(config)
    try:
        while worker.rclpy.ok():
            worker.executor.spin_once(timeout_sec=0.001)
            worker.spin_tick()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
