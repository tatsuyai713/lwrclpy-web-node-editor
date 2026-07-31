from __future__ import annotations

import ast
import importlib
import json
import keyword
import os
import re
import sys
import builtins
import time
from pathlib import Path
from typing import Any


DEFAULT_PYTHON_LOOP_CODE = """# Main Loop is this node's own loop, like a hand-written rclpy node.
# rclpy.spin(node) dispatches input callbacks and Timer Callbacks until
# shutdown, so periodic work belongs in a Timer Callback.
# Scope (see the reference under the editor for types):
#   rclpy, node, rate, run_hz, loop_period, state, params, now
#   latest(input_id), take(input_id), has_input(input_id)
#   publish(output_id, value), log(...)
#
# Write the loop yourself instead when you prefer an explicit tick:
#   while rclpy.ok():
#       rclpy.spin_once(node, timeout_sec=0.0)
#       # periodic work
#       rate.sleep()
rclpy.spin(node)
"""


# Upper bound on callbacks dispatched in one spin_tick(), so a publisher that
# outruns this node cannot starve the timer and Main Loop code entirely.
MAX_CALLBACKS_PER_TICK = 256


EXTERNAL_FASTDDS_TRANSPORTS = os.environ.get(
    "LWRCLPY_WEB_FASTDDS_TRANSPORTS",
    "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=true",
)


def configure_fastdds_transport(config: dict[str, Any]) -> None:
    if config.get("externalDdsCompatible"):
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = EXTERNAL_FASTDDS_TRANSPORTS
    else:
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = os.environ.get(
            "LWRCLPY_WEB_INTERNAL_FASTDDS_TRANSPORTS",
            "UDPv4",
        )


def disable_lwrclpy_side_channels(config: dict[str, Any]) -> None:
    setting = os.environ.get("LWRCLPY_WEB_ENABLE_LWRCLPY_SIDE_CHANNELS", "").strip().lower()
    if setting not in {"0", "false", "no", "off"}:
        return
    try:
        import lwrclpy.node as lwrclpy_node

        node_cls = getattr(lwrclpy_node, "Node", None)
        if node_cls is None:
            return
        for name in (
            "_configure_cuda_ipc_publisher",
            "_configure_cuda_ipc_subscription",
            "_configure_shared_memory_publisher",
            "_configure_shared_memory_subscription",
        ):
            if hasattr(node_cls, name):
                setattr(node_cls, name, lambda self, *args, **kwargs: None)
    except Exception:
        pass

def loop_code_is_self_driving(code: str) -> bool:
    """True when *code* drives its own loop rather than acting as one tick.

    Parsed rather than pattern matched so a commented-out example, or a spin
    call inside a helper string, does not change how the loop is executed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for statement in tree.body:
        if isinstance(statement, (ast.While, ast.AsyncFor)):
            return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name in {"spin", "spin_until_future_complete"}:
            return True
    return False


def sanitize_node_name(name: str) -> str:
    """Make *name* a valid ROS 2 node name ([A-Za-z0-9_], not starting with a digit)."""
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip())
    if not text:
        return "lwrclpy_node"
    if text[0].isdigit():
        text = f"node_{text}"
    return text


def import_type_class(type_name: str):
    package, kind, name = type_name.split("/")
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


def split_kind(type_name: str) -> str:
    return type_name.split("/")[1]


def topic_qos(data_type: str, depth: int = 1, reliable: bool = False, topic: str = "") -> Any:
    normalized = data_type.replace(".", "/")
    if normalized == "tf2_msgs/msg/TFMessage" and str(topic or "").rstrip("/") == "/tf_static":
        try:
            qos = importlib.import_module("lwrclpy.qos")
            return qos.QoSProfile(
                history=qos.HistoryPolicy.KEEP_LAST,
                depth=1,
                reliability=qos.ReliabilityPolicy.RELIABLE,
                durability=qos.DurabilityPolicy.TRANSIENT_LOCAL,
            )
        except Exception:
            return 1
    if normalized not in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
        return 10
    try:
        qos = importlib.import_module("lwrclpy.qos")
        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=qos.ReliabilityPolicy.RELIABLE if reliable else qos.ReliabilityPolicy.BEST_EFFORT,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return depth


def publisher_qos(data_type: str, external: bool = False, topic: str = "") -> Any:
    return topic_qos(data_type, depth=5, reliable=False, topic=topic)


def subscriber_qos(data_type: str, topic: str = "") -> Any:
    return topic_qos(data_type, depth=5, reliable=False, topic=topic)


class LoopRate:
    """Rate helper with ROS 2 ``Rate`` semantics.

    ``sleep()`` waits until the end of the current period instead of always
    sleeping a whole period.  Sleeping a full period on every call would add
    the time already spent in callbacks and Main Loop code on top of the
    requested period, so a node configured for 60 Hz whose work takes 30 ms
    would only tick at 21 Hz.
    """

    def __init__(self, hz: float) -> None:
        self.hz = max(1.0, float(hz or 60.0))
        self._next_at = 0.0

    @property
    def period(self) -> float:
        return 1.0 / self.hz

    def set_hz(self, hz: float) -> None:
        hz = max(1.0, float(hz or 60.0))
        if hz != self.hz:
            self.hz = hz
            self._next_at = 0.0

    def sleep(self) -> None:
        now = time.monotonic()
        if self._next_at <= 0.0:
            self._next_at = now
        self._next_at += self.period
        if self._next_at <= now:
            # Behind schedule: do not sleep, and re-anchor to now so the next
            # call measures its remainder from here instead of being handed a
            # free extra period.
            self._next_at = now
            return
        time.sleep(self._next_at - now)


class LwrclpyWorkerNode:
    def __init__(self, config: dict[str, Any]) -> None:
        import lwrclpy as rclpy

        self.rclpy = rclpy
        self.config = config
        self.node_config = config["node"]
        self.port_topics = config.get("portTopics", {"inputs": {}, "outputs": {}})
        self.state: dict[str, Any] = {}
        self.last_inputs: dict[str, Any] = {}
        self.input_queues: dict[str, list[Any]] = {}
        self.last_outputs: dict[str, Any] = {}
        self.next_timer_at = 0.0
        self.next_timer_by_id: dict[str, float] = {}
        self.next_loop_at = 0.0
        self.publishers: dict[str, list[Any]] = {}
        self.clients: dict[str, list[Any]] = {}
        self.subscriptions: list[Any] = []
        self.services: list[Any] = []
        self.timers: list[Any] = []
        self._globals_cache: dict[str, Any] | None = None
        self._loop_rate = LoopRate(self._run_hz())
        if not self.rclpy.ok():
            self.rclpy.init(args=None)
        self.node = self.rclpy.create_node(sanitize_node_name(self.node_config["name"]))
        # One long-lived executor: the module-level rclpy.spin_once() helper
        # builds and tears down a throwaway executor on every call.
        self.executor = self.rclpy.SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self._setup_transport()
        self._setup_timers()

    def _setup_transport(self) -> None:
        for output in self.node_config.get("outputs", []):
            type_cls = import_type_class(output["dataType"])
            for topic in self.port_topics.get("outputs", {}).get(output["id"], []):
                if split_kind(output["dataType"]) == "msg":
                    self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(
                        type_cls,
                        topic,
                        publisher_qos(output["dataType"], bool(self.config.get("externalDdsCompatible")), topic),
                    ))
                else:
                    self.clients.setdefault(output["id"], []).append(self.node.create_client(type_cls, topic))
        for input_port in self.node_config.get("inputs", []):
            type_cls = import_type_class(input_port["dataType"])
            for topic in self.port_topics.get("inputs", {}).get(input_port["id"], []):
                if split_kind(input_port["dataType"]) == "msg":
                    self.subscriptions.append(self.node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port), subscriber_qos(input_port["dataType"], topic)))
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

    def loop_is_self_driving(self) -> bool:
        """True when Main Loop code runs its own loop instead of being ticked.

        Code written the way a hand-written rclpy node is written — an explicit
        ``while rclpy.ok():``, or a blocking ``rclpy.spin(node)`` — has to be
        executed once and left to own execution.  Anything else is a single tick
        body and is called repeatedly at Run Hz, which is what projects saved
        before this convention contain.
        """
        code = self.node_config.get("loopCode", "").strip() or DEFAULT_PYTHON_LOOP_CODE
        return loop_code_is_self_driving(code)

    def run_self_driving_loop(self) -> None:
        # Hand the node over so the executor the user's code spins owns its wake
        # event; otherwise that executor only notices new work on its poll
        # timeout and every message picks up avoidable latency.
        try:
            self.executor.remove_node(self.node)
        except Exception:
            pass
        outputs: dict[str, Any] = {}
        self._execute_loop(dict(self.last_inputs), outputs)
        self._flush_outputs(outputs)

    def spin_tick(self) -> None:
        self.pump_callbacks(self.next_event_delay())
        outputs: dict[str, Any] = {}
        inputs = dict(self.last_inputs)
        if self._loop_due():
            self._execute_loop(inputs, outputs)
        self._flush_outputs(outputs)

    def pump_callbacks(self, timeout_sec: float = 0.0) -> int:
        """Dispatch every callback that is already queued, and return the spin count.

        A single spin_once() only dispatches one callback, while a
        shared-memory backed image topic queues several callbacks per frame
        (the payload plus the side-channel metadata).  Relying on the Main
        Loop to call spin_once() once per Run Hz tick therefore caps the node
        at a fraction of the publish rate and the surplus frames are dropped
        by DDS before they ever reach the callback.

        The first spin blocks for up to *timeout_sec* so an idle node waits on
        the executor instead of busy-looping; queued work wakes it immediately.
        """
        spins = 0
        wait = max(0.0, float(timeout_sec or 0.0))
        for _ in range(MAX_CALLBACKS_PER_TICK):
            self.executor.spin_once(timeout_sec=wait)
            spins += 1
            wait = 0.0
            if not self._has_queued_callbacks():
                break
        return spins

    def _has_queued_callbacks(self) -> bool:
        queue = getattr(self.node, "_callback_queue", None)
        if queue is None:
            # Unknown runtime: fall back to a single dispatch per tick.
            return False
        try:
            return bool(len(queue))
        except Exception:
            return False

    def _run_hz(self) -> float:
        try:
            return max(1.0, min(float(self.node_config.get("params", {}).get("_runHz") or 60.0), 120.0))
        except Exception:
            return 60.0

    def _loop_due(self) -> bool:
        # Main Loop code runs at the configured tick frequency (Run Hz), not at
        # the raw executor spin rate.
        now = time.time()
        if now < self.next_loop_at:
            return False
        period = 1.0 / self._run_hz()
        next_at = (self.next_loop_at if self.next_loop_at > 0 else now) + period
        while next_at <= now:
            next_at += period
        self.next_loop_at = next_at
        return True

    def next_event_delay(self, max_delay: float = 0.02) -> float:
        """Spin timeout until the next Main Loop deadline.

        Subscription and timer callbacks wake the executor immediately, so a
        longer idle timeout only reduces busy-loop CPU without delaying message
        handling.
        """
        if not str(self.node_config.get("loopCode", "")).strip():
            return max_delay
        if self.next_loop_at <= time.time():
            return 0.001
        return max(0.001, min(max_delay, self.next_loop_at - time.time()))

    def _make_subscription_callback(self, input_port: dict[str, Any]):
        def callback(msg):
            value = self._message_to_value(msg)
            queue_limit = self._input_queue_limit(input_port)
            self._store_input(input_port["id"], value, queue_limit=queue_limit)
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
            code = DEFAULT_PYTHON_LOOP_CODE
        run_hz = self._run_hz()
        # Reuse one rate object so its period schedule survives across ticks.
        self._loop_rate.set_hz(run_hz)
        local = self._locals({
            "inputs": inputs,
            "outputs": outputs,
            "now": time.time(),
            "latest": self.latest,
            "take": self.take,
            "has_input": self.has_input,
            "rclpy": self.rclpy,
            "run_hz": run_hz,
            "loop_period": 1.0 / run_hz,
            "rate": self._loop_rate,
        })
        try:
            exec(code, self._globals(), local)
        except Exception as exc:
            self.log(f"loop error: {exc}")

    def _setup_timers(self) -> None:
        """Register configured timers as real node timers.

        Using node.create_timer() rather than scheduling them by hand means they
        fire from the executor, so they keep working when Main Loop code owns the
        loop and blocks in rclpy.spin(node).  It also matches ROS 2 semantics,
        including the first callback firing one full period after start.
        """
        for timer in self._timers():
            code = str(timer.get("callbackCode") or "").strip()
            if not code:
                continue
            timer_id = str(timer.get("id") or "timer1")
            timer_name = str(timer.get("name") or timer_id)
            period = max(0.001, float(timer.get("periodSec", 1.0) or 1.0))
            self.timers.append(self.node.create_timer(
                period,
                self._make_timer_callback(timer_id, timer_name, period, code),
            ))

    def _make_timer_callback(self, timer_id: str, timer_name: str, period: float, code: str):
        def callback() -> None:
            outputs: dict[str, Any] = {}
            local = self._locals({
                "timer_id": timer_id,
                "timer_name": timer_name,
                "inputs": dict(self.last_inputs),
                "outputs": outputs,
                "now": time.time(),
                "period": period,
                "latest": self.latest,
                "take": self.take,
                "has_input": self.has_input,
            })
            try:
                exec(code, self._globals(), local)
            except Exception as exc:
                self.log(f"{timer_id} timer callback error: {exc}")
            self._flush_outputs(outputs)

        return callback

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
            self._sync_param_globals(self._globals_cache)
            return self._globals_cache
        if not hasattr(builtins, "__orig_import__"):
            builtins.__orig_import__ = builtins.__import__
        globals_dict = {
            "__builtins__": builtins,
            # Keep print routed to node log while preserving full builtins/import behavior.
            "print": self.log,
        }
        self._sync_param_globals(globals_dict)
        import_code = self.node_config.get("importCode", "").strip()
        if import_code:
            try:
                exec(import_code, globals_dict, globals_dict)
            except Exception as exc:
                self.log(f"import setup error: {exc}")
        self._sync_param_globals(globals_dict)
        self._globals_cache = globals_dict
        return globals_dict

    def _locals(self, extra: dict[str, Any]) -> dict[str, Any]:
        params = self.node_config.get("params", {})
        return {"node": self.node, "params": params, "state": self.state, "publish": self.publish, "log": self.log, **self._param_globals(params), **extra}

    def _sync_param_globals(self, globals_dict: dict[str, Any]) -> None:
        previous = globals_dict.get("__lwrclpy_param_names__", set())
        if isinstance(previous, set):
            for name in previous:
                globals_dict.pop(name, None)
        param_globals = self._param_globals(self.node_config.get("params", {}))
        globals_dict.update(param_globals)
        globals_dict["__lwrclpy_param_names__"] = set(param_globals)

    def _param_globals(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {}
        reserved = {
            "node",
            "params",
            "state",
            "publish",
            "log",
            "input_id",
            "msg",
            "request",
            "response",
            "inputs",
            "outputs",
            "now",
            "period",
            "run_hz",
            "loop_period",
            "rate",
            "rclpy",
            "timer_id",
            "timer_name",
            "latest",
            "take",
            "has_input",
        }
        result: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and key not in reserved:
                result[key] = value
        return result

    def _input_queue_limit(self, input_port: dict[str, Any]) -> int:
        data_type = str(input_port.get("dataType") or "").replace(".", "/")
        if data_type in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}:
            return 2
        return 100

    def _store_input(self, input_id: str, value: Any, queue_limit: int = 100) -> None:
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-max(1, queue_limit)]

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
                result = {key: self._message_field_value(value, key) for key in image_keys if hasattr(value, key)}
                result["__lwrclpy_msg_ref"] = value
                return result
            if hasattr(value, "data"):
                return {"data": self._message_field_value(value, "data"), "__lwrclpy_msg_ref": value}
            return value
        result: dict[str, Any] = {}
        for key in fields:
            item = getattr(value, key, None)
            if hasattr(item, "_fields_and_field_types"):
                item = self._message_to_value(item)
            else:
                item = self._message_field_value(value, key)
            result[key] = item
        if all(key in result for key in ("height", "width", "encoding", "data")):
            result["__lwrclpy_msg_ref"] = value
        return result

    def _message_field_value(self, message: Any, key: str) -> Any:
        memoryview_getter = getattr(message, f"_lwrclpy_{key}_memoryview", None)
        if callable(memoryview_getter):
            try:
                return memoryview(memoryview_getter())
            except Exception:
                pass
        bytes_getter = getattr(message, f"_lwrclpy_{key}_bytes", None)
        if callable(bytes_getter):
            try:
                return bytes_getter()
            except Exception:
                pass
        raw = getattr(message, key, None)
        if key == "data" and self._is_image_payload(message):
            payload = self._as_bytes_like(raw)
            if payload is not None:
                return payload
        return self._plain_value(raw)

    @staticmethod
    def _is_image_payload(message: Any) -> bool:
        """True when ``message.data`` is a uint8 image payload."""
        if all(hasattr(message, name) for name in ("height", "width", "encoding")):
            return True
        return hasattr(message, "format")

    @staticmethod
    def _as_bytes_like(value: Any) -> Any:
        """Return *value* as bytes without going through a Python list.

        Image payloads arrive as a plain uint8 sequence whenever the
        shared-memory side channel is not attached — the first frames of a run,
        or every frame when side channels are disabled.  Converting those with
        tolist() builds a list with one Python int per byte and, worse, makes
        np.frombuffer() in node code fail with "a bytes-like object is
        required, not 'list'".  Returns None if no safe conversion applies, so
        the caller can fall back to the generic handling.
        """
        if value is None:
            return None
        if callable(value):
            try:
                value = value()
            except TypeError:
                pass
        if isinstance(value, (bytes, bytearray, memoryview)):
            return value
        try:
            expected = len(value)
        except Exception:
            expected = None
        attempts = []
        tobytes = getattr(value, "tobytes", None)
        if callable(tobytes):
            attempts.append(tobytes)
        attempts.append(lambda: bytes(value))
        for attempt in attempts:
            try:
                result = attempt()
            except Exception:
                continue
            if not isinstance(result, (bytes, bytearray, memoryview)):
                continue
            if expected is not None and len(result) != expected:
                continue
            return result
        return None

    def _plain_value(self, value: Any) -> Any:
        if callable(value):
            try:
                value = value()
            except TypeError:
                pass
        if isinstance(value, (bytes, bytearray, memoryview)):
            return value
        if hasattr(value, "get_buffer"):
            return value
        if value.__class__.__name__.endswith("_vector"):
            return value
        if hasattr(value, "tolist"):
            try:
                return value.tolist()
            except Exception:
                pass
        return value

    def _set_field(self, target: Any, key: str, value: Any) -> None:
        if key == "data" and isinstance(value, (bytes, bytearray, memoryview)):
            resize = getattr(target, "_lwrclpy_data_resize", None)
            buffer_getter = getattr(target, "_lwrclpy_data_memoryview", None)
            if callable(resize) and callable(buffer_getter):
                try:
                    source = memoryview(value)
                    resize(len(source))
                    memoryview(buffer_getter())[:len(source)] = source
                    return
                except Exception:
                    pass
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
        if isinstance(value, msg_cls) or hasattr(value, "_fields_and_field_types"):
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
        try:
            self.executor.remove_node(self.node)
            self.executor.shutdown()
        except Exception:
            pass
        self.node.destroy_node()
        self.rclpy.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: node_worker.py CONFIG_JSON", file=sys.stderr)
        return 2
    config = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    configure_fastdds_transport(config)
    disable_lwrclpy_side_channels(config)
    worker = LwrclpyWorkerNode(config)
    try:
        if worker.loop_is_self_driving():
            # Main Loop code owns the loop, the way a hand-written rclpy node
            # does. Run it once and let it spin until shutdown.
            worker.run_self_driving_loop()
        else:
            # Main Loop code is a single tick body: drive it at Run Hz and
            # dispatch callbacks around it.
            while worker.rclpy.ok():
                worker.spin_tick()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
