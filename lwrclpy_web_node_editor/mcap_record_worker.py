from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from array import array
from pathlib import Path
from typing import Any

RUNNING = True
EXTERNAL_FASTDDS_TRANSPORTS = "UDPv4?max_msg_size=64KB&sockets_size=16MB&non_blocking=false"
BUILTIN_TYPE_NAMES = {
    "bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16",
    "int32", "uint32", "int64", "uint64", "string", "wstring", "time", "duration",
}
BUILTIN_MSGDEFS = {
    "builtin_interfaces/msg/Time": "int32 sec\nuint32 nanosec",
    "builtin_interfaces/msg/Duration": "int32 sec\nuint32 nanosec",
}


def _configure_fastdds_transport(config: dict[str, Any]) -> None:
    if config.get("externalDdsCompatible"):
        os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = EXTERNAL_FASTDDS_TRANSPORTS
    else:
        os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "LARGE_DATA")


def _stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def _spin_executor(executor: Any, status_path: Path) -> None:
    global RUNNING
    while RUNNING:
        try:
            executor.spin_once(timeout_sec=0.01)
        except Exception as exc:
            traceback.print_exc()
            _write_status(status_path, running=False, error=str(exc), traceback=traceback.format_exc()[-4000:], status=f"DDS spin error: {exc}")
            RUNNING = False
            return


def _write_status(status_path: Path, **payload: Any) -> None:
    payload.setdefault("time", time.time())
    tmp_path = status_path.with_name(f"{status_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp_path.replace(status_path)


def _import_type_class(type_name: str):
    package, kind, name = [part for part in str(type_name).replace(".", "/").split("/") if part]
    module = __import__(f"{package}.{kind}", fromlist=[name])
    return getattr(module, name)


def _record_qos(data_type: str, depth: int = 4096) -> Any:
    depth = max(1, int(depth or 4096))
    try:
        import rclpy.qos as qos

        return qos.QoSProfile(
            history=qos.HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=qos.ReliabilityPolicy.RELIABLE,
            durability=qos.DurabilityPolicy.VOLATILE,
        )
    except Exception:
        return depth


def _ensure_mcap_dependencies() -> None:
    try:
        import mcap  # noqa: F401
        import mcap_ros2  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "mcap", "mcap-ros2-support"],
        check=True,
        capture_output=True,
        text=True,
    )


def _normalize_type(type_name: str) -> str:
    return str(type_name or "").replace(".", "/")


def _msg_search_roots() -> list[Path]:
    roots: list[Path] = []
    for entry in sys.path:
        if entry:
            roots.append(Path(entry))
    try:
        from ament_index_python.packages import get_packages_with_prefixes

        roots.extend(Path(prefix) / "share" for prefix in get_packages_with_prefixes().values())
    except Exception:
        pass
    roots.extend([
        Path.cwd(),
        Path(__file__).resolve().parents[1],
        Path.home() / "repos" / "lwrclpy" / "third_party" / "common_interfaces",
    ])
    seen: set[str] = set()
    result: list[Path] = []
    for root in roots:
        try:
            resolved = str(root.expanduser().resolve())
        except Exception:
            resolved = str(root)
        if resolved not in seen:
            seen.add(resolved)
            result.append(Path(resolved))
    return result


def _find_msg_file(datatype: str) -> Path | None:
    parts = [part for part in _normalize_type(datatype).split("/") if part]
    if len(parts) != 3 or parts[1] != "msg":
        return None
    rel = Path(parts[0]) / "msg" / f"{parts[2]}.msg"
    for root in _msg_search_roots():
        for candidate in (root / rel, root / parts[0] / rel):
            if candidate.is_file():
                return candidate
    return None


def _strip_array(type_name: str) -> str:
    return re.sub(r"\[[^\]]*\]$", "", type_name.strip())


def _referenced_types(datatype: str, text: str) -> list[str]:
    package = datatype.split("/")[0]
    refs: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" in line:
            continue
        field_type = _strip_array(line.split()[0])
        if field_type in BUILTIN_TYPE_NAMES:
            continue
        if "/" not in field_type:
            field_type = f"{package}/msg/{field_type}"
        elif field_type.count("/") == 1:
            pkg, name = field_type.split("/", 1)
            field_type = f"{pkg}/msg/{name}"
        refs.append(field_type)
    return refs


def _load_msgdef(datatype: str, seen: set[str] | None = None) -> str:
    datatype = _normalize_type(datatype)
    seen = seen or set()
    if datatype in seen:
        return ""
    seen.add(datatype)
    if datatype in BUILTIN_MSGDEFS:
        return BUILTIN_MSGDEFS[datatype]
    path = _find_msg_file(datatype)
    if path is None:
        raise RuntimeError(f"message definition not found for {datatype}")
    text = path.read_text(encoding="utf-8")
    sections = [text.rstrip()]
    for ref in _referenced_types(datatype, text):
        nested = _load_msgdef(ref, seen).strip()
        if nested:
            sections.append(f"================================================================================\nMSG: {ref}\n{nested}")
    return "\n".join(sections)


def _msgdef_for(datatype: str, msg_cls: Any | None = None) -> str:
    if msg_cls is not None:
        text = getattr(msg_cls, "_full_text", None)
        if isinstance(text, str) and text.strip():
            return text
    return _load_msgdef(datatype)


def _rosbag_output_paths(raw_output_path: str) -> tuple[Path, Path]:
    selected = Path(raw_output_path).expanduser()
    if selected.suffix.lower() == ".mcap":
        bag_dir = selected.with_suffix("")
        bag_name = selected.stem
    else:
        bag_dir = selected
        bag_name = selected.name or "recording"
    return bag_dir, bag_dir / f"{bag_name}_0.mcap"


def _rosbag_segment_path(first_path: Path, index: int) -> Path:
    stem = first_path.stem
    base = stem[:-2] if stem.endswith("_0") else stem
    return first_path.with_name(f"{base}_{index}.mcap")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "''"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _write_rosbag2_metadata(
    bag_dir: Path,
    *,
    storage_id: str,
    started_ns: int,
    ended_ns: int,
    topic_types: dict[str, str],
    topic_counts: dict[str, int],
    file_infos: list[dict[str, Any]],
) -> None:
    duration_ns = max(0, int(ended_ns) - int(started_ns))
    total_count = sum(int(value) for value in topic_counts.values())
    if not file_infos:
        file_infos = [{"path": bag_dir / "recording_0.mcap", "started_ns": started_ns, "ended_ns": ended_ns, "message_count": total_count}]
    lines = [
        "rosbag2_bagfile_information:",
        "  version: 5",
        f"  storage_identifier: {_yaml_scalar(storage_id)}",
        "  duration:",
        f"    nanoseconds: {duration_ns}",
        "  starting_time:",
        f"    nanoseconds_since_epoch: {int(started_ns)}",
        f"  message_count: {total_count}",
        "  topics_with_message_count:",
    ]
    for topic in sorted(topic_types):
        lines.extend([
            "    - topic_metadata:",
            f"        name: {_yaml_scalar(topic)}",
            f"        type: {_yaml_scalar(topic_types[topic])}",
            "        serialization_format: cdr",
            "        offered_qos_profiles: \"\"",
            f"      message_count: {int(topic_counts.get(topic, 0))}",
        ])
    lines.extend([
        "  compression_format: \"\"",
        "  compression_mode: \"\"",
        "  relative_file_paths:",
    ])
    for info in file_infos:
        path = Path(info.get("path") or "")
        rel_path = path.name
        lines.append(f"    - {_yaml_scalar(rel_path)}")
    lines.append("  files:")
    for info in file_infos:
        path = Path(info.get("path") or "")
        file_started_ns = int(info.get("started_ns") or started_ns)
        file_ended_ns = int(info.get("ended_ns") or file_started_ns)
        file_duration_ns = max(0, file_ended_ns - file_started_ns)
        file_message_count = int(info.get("message_count") or 0)
        lines.extend([
            f"    - path: {_yaml_scalar(path.name)}",
            "      starting_time:",
            f"        nanoseconds_since_epoch: {file_started_ns}",
            "      duration:",
            f"        nanoseconds: {file_duration_ns}",
            f"      message_count: {file_message_count}",
        ])
    lines.extend([
        "  custom_data: {}",
        "  ros_distro: lwrclpy",
        "",
    ])
    tmp_path = bag_dir / f"metadata.yaml.{os.getpid()}.{time.time_ns()}.tmp"
    tmp_path.write_text("\n".join(lines), encoding="utf-8")
    tmp_path.replace(bag_dir / "metadata.yaml")


def _read_status(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _merge_mcap_parts(part_paths: list[Path], output_path: Path) -> tuple[int, list[str]]:
    from mcap.reader import make_reader
    from mcap.writer import CompressionType, Writer as RawMcapWriter

    tmp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    schema_ids: dict[tuple[str, str, bytes], int] = {}
    channel_ids: dict[tuple[str, str, int, tuple[tuple[str, str], ...]], int] = {}
    message_count = 0
    errors: list[str] = []
    with tmp_path.open("wb") as out:
        writer = RawMcapWriter(out, compression=CompressionType.ZSTD)
        writer.start(profile="ros2", library="lwrclpy_web_node_editor")
        for part_path in part_paths:
            if not part_path.exists() or part_path.stat().st_size <= 0:
                continue
            try:
                with part_path.open("rb") as src:
                    reader = make_reader(src, validate_crcs=False)
                    for schema, channel, message in reader.iter_messages(log_time_order=False):
                        schema_id = 0
                        if schema is not None:
                            schema_key = (schema.name, schema.encoding, schema.data)
                            schema_id = schema_ids.get(schema_key) or writer.register_schema(
                                name=schema.name,
                                encoding=schema.encoding,
                                data=schema.data,
                            )
                            schema_ids[schema_key] = schema_id
                        metadata = dict(channel.metadata or {})
                        channel_key = (
                            channel.topic,
                            channel.message_encoding,
                            schema_id,
                            tuple(sorted((str(k), str(v)) for k, v in metadata.items())),
                        )
                        channel_id = channel_ids.get(channel_key)
                        if channel_id is None:
                            channel_id = writer.register_channel(
                                topic=channel.topic,
                                message_encoding=channel.message_encoding,
                                schema_id=schema_id,
                                metadata=metadata,
                            )
                            channel_ids[channel_key] = channel_id
                        writer.add_message(
                            channel_id=channel_id,
                            log_time=message.log_time,
                            publish_time=message.publish_time,
                            sequence=message.sequence,
                            data=message.data,
                        )
                        message_count += 1
            except Exception as exc:
                errors.append(f"{part_path.name}: {exc}")
        writer.finish()
    tmp_path.replace(output_path)
    return message_count, errors


def _write_empty_mcap(output_path: Path) -> None:
    from mcap.writer import CompressionType, Writer as RawMcapWriter

    tmp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with tmp_path.open("wb") as out:
        writer = RawMcapWriter(out, compression=CompressionType.ZSTD)
        writer.start(profile="ros2", library="lwrclpy_web_node_editor")
        writer.finish()
    tmp_path.replace(output_path)


def _run_manager(config: dict[str, Any], config_path: Path, status_path: Path, output_path: Path, input_topics: list[tuple[dict[str, Any], str]]) -> int:
    worker_dir = status_path.parent / f"{status_path.stem}.parts"
    worker_dir.mkdir(parents=True, exist_ok=True)
    _write_empty_mcap(output_path)
    children: list[dict[str, Any]] = []
    for index, (item, topic) in enumerate(input_topics, start=1):
        safe_topic = re.sub(r"[^A-Za-z0-9_.-]+", "_", topic).strip("_") or f"topic{index}"
        part_path = worker_dir / f"{index:02d}_{safe_topic}.mcap"
        child_status_path = worker_dir / f"{index:02d}_{safe_topic}.status.json"
        child_config_path = worker_dir / f"{index:02d}_{safe_topic}.json"
        child_pid_path = worker_dir / f"{index:02d}_{safe_topic}.pid"
        child_input = dict(item)
        child_input["topics"] = [topic]
        child_params = dict(config.get("params") or {})
        child_params["mcapPath"] = str(part_path)
        child_config = dict(config)
        child_config["childRecord"] = True
        child_config["childIndex"] = index
        child_config["childTopic"] = topic
        child_config["inputs"] = [child_input]
        child_config["params"] = child_params
        child_config["statusPath"] = str(child_status_path)
        try:
            part_path.unlink(missing_ok=True)
            child_status_path.unlink(missing_ok=True)
            old_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(old_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            child_pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        child_config_path.write_text(json.dumps(child_config, ensure_ascii=False, default=str), encoding="utf-8")
        log_path = worker_dir / f"{index:02d}_{safe_topic}.log"
        log_file = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(child_config_path)],
            cwd=Path.cwd(),
            env=dict(os.environ),
            stdout=log_file,
            stderr=log_file,
            text=True,
            start_new_session=False,
        )
        child_pid_path.write_text(str(process.pid), encoding="utf-8")
        children.append({
            "topic": topic,
            "partPath": part_path,
            "statusPath": child_status_path,
            "process": process,
            "logFile": log_file,
            "pidPath": child_pid_path,
        })

    _write_status(status_path, running=True, phase="record", status=f"recording to {output_path}", received=0, recorded=0, queued=0, dropped=0, path=str(output_path), topics={topic: 0 for _item, topic in input_topics})
    try:
        while RUNNING:
            received_total = 0
            recorded_total = 0
            queued_total = 0
            dropped_total = 0
            by_topic: dict[str, int] = {}
            errors: list[str] = []
            for child in children:
                status = _read_status(child["statusPath"])
                topic = str(child["topic"])
                if status:
                    received_total += int(status.get("received") or 0)
                    recorded = int(status.get("recorded") or 0)
                    recorded_total += recorded
                    queued_total += int(status.get("queued") or 0)
                    dropped_total += int(status.get("dropped") or 0)
                    by_topic[topic] = recorded
                    if status.get("error"):
                        errors.append(f"{topic}: {status.get('error')}")
                else:
                    by_topic.setdefault(topic, 0)
                process = child["process"]
                if process.poll() is not None and not status:
                    errors.append(f"{topic}: recorder exited before status was written")
            status_text = f"{output_path.name} recording / {recorded_total} messages"
            if errors:
                status_text = f"{status_text} / errors: {'; '.join(errors[:2])}"
            _write_status(
                status_path,
                running=True,
                phase="record",
                status=status_text,
                received=received_total,
                recorded=recorded_total,
                queued=queued_total,
                dropped=dropped_total,
                path=str(output_path),
                topics=by_topic,
                childProcesses=[{"topic": child["topic"], "pid": child["process"].pid} for child in children],
            )
            time.sleep(0.5)
    finally:
        _write_status(status_path, running=True, phase="stopping", status=f"stopping {output_path.name}", path=str(output_path))
        for child in children:
            process = child["process"]
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 8.0
        while time.time() < deadline and any(child["process"].poll() is None for child in children):
            time.sleep(0.1)
        for child in children:
            process = child["process"]
            if process.poll() is None:
                process.kill()
        for child in children:
            try:
                child["process"].wait(timeout=1.0)
            except Exception:
                pass
            try:
                child["logFile"].close()
            except Exception:
                pass
            try:
                child["pidPath"].unlink(missing_ok=True)
            except Exception:
                pass

    part_paths = [child["partPath"] for child in children]
    _write_status(status_path, running=True, phase="merge", status=f"merging {len(part_paths)} MCAP parts", path=str(output_path))
    merged_count, merge_errors = _merge_mcap_parts(part_paths, output_path)
    child_topics: dict[str, int] = {}
    received_total = 0
    dropped_total = 0
    for child in children:
        status = _read_status(child["statusPath"]) or {}
        topic = str(child["topic"])
        recorded = int(status.get("recorded") or 0)
        child_topics[topic] = recorded
        received_total += int(status.get("received") or 0)
        dropped_total += int(status.get("dropped") or 0)
    status = f"{output_path.name} record stopped / {merged_count} messages"
    if merge_errors:
        status = f"{status} / merge warnings: {'; '.join(merge_errors[:2])}"
    _write_status(status_path, running=False, ended=True, status=status, received=received_total, recorded=merged_count, queued=0, dropped=dropped_total, path=str(output_path), topics=child_topics, mergeErrors=merge_errors)
    return 0 if not merge_errors else 1


class _McapMessageProxy:
    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def __getattr__(self, name: str) -> Any:
        try:
            return _mcap_value(getattr(self._value, name))
        except AttributeError:
            try:
                return _mcap_value(self._value[name])
            except (KeyError, TypeError):
                raise AttributeError(name) from None

    def __getitem__(self, name: str) -> Any:
        return _mcap_value(self._value[name])

    def __int__(self) -> int:
        return int(_mcap_scalar(self._value))

    def __float__(self) -> float:
        return float(_mcap_scalar(self._value))

    def __bool__(self) -> bool:
        return bool(_mcap_scalar(self._value))

    def __str__(self) -> str:
        return str(_mcap_scalar(self._value))


def _mcap_scalar(value: Any) -> Any:
    if callable(value):
        try:
            value = value()
        except TypeError:
            pass
    if isinstance(value, _McapMessageProxy):
        return _mcap_scalar(value._value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _mcap_value(value: Any) -> Any:
    value = _mcap_scalar(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, (str, bytes, int, float, bool, array)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_mcap_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _mcap_value(item) for key, item in value.items()}
    return _McapMessageProxy(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    _configure_fastdds_transport(config)
    status_path = Path(config["statusPath"])
    params = config.get("params") or {}
    raw_output_path = str(params.get("mcapPath") or params.get("recordPath") or "").strip()
    if not raw_output_path:
        _write_status(status_path, running=False, status="No MCAP output path selected")
        return 0
    bag_dir, output_path = _rosbag_output_paths(raw_output_path)
    bag_dir.mkdir(parents=True, exist_ok=True)
    input_topics = [
        (item, str(topic))
        for item in config.get("inputs", [])
        if isinstance(item, dict) and item.get("topics") and item.get("dataType")
        for topic in item.get("topics", [])
        if str(topic)
    ]
    if not input_topics:
        _write_status(status_path, running=False, status="MCAP record node has no connected inputs")
        return 0
    try:
        _write_status(status_path, running=True, phase="dependencies", status="starting: loading MCAP dependencies", recorded=0)
        _ensure_mcap_dependencies()
        from mcap_ros2.writer import Writer as McapWriter
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        try:
            from lwrclpy.message_utils import expose_callable_fields
        except Exception:
            expose_callable_fields = None

        if not rclpy.ok():
            rclpy.init(args=None)
        node = rclpy.create_node(f"ipn_mcap_record_{config.get('nodeId', 'sink')}".replace("-", "_")[:80])
        try:
            split_size_mb = max(0.0, float(params.get("splitSizeMb") or 0))
        except Exception:
            split_size_mb = 0.0
        max_split_bytes = int(split_size_mb * 1024 * 1024)
        current_mcap_path = output_path

        def make_writer(path: Path):
            if max_split_bytes > 0:
                return McapWriter(str(path), chunk_size=max(1, min(1048576, max_split_bytes)))
            return McapWriter(str(path))

        writer = make_writer(current_mcap_path)
        schemas: dict[str, Any] = {}
        msgdefs_by_datatype: dict[str, str] = {}
        topic_types: dict[str, str] = {}
        received_counts: dict[str, int] = {}
        written_counts: dict[str, int] = {}
        dropped_counts: dict[str, int] = {}
        lock = threading.Lock()
        segment_lock = threading.Lock()
        sequence = 0
        current_segment_index = 0
        current_segment_started_ns = time.time_ns()
        current_segment_message_count = 0
        file_infos: list[dict[str, Any]] = []
        max_queue_size = max(0, int(params.get("queueSize") or 0))
        qos_depth = max(1, int(params.get("qosDepth") or 4096))
        record_queue: queue.Queue[tuple[str, str, Any, int] | None] = queue.Queue(maxsize=max_queue_size)
        writer_error: dict[str, str] = {}

        def total(values: dict[str, int]) -> int:
            return sum(values.values())

        def rotate_writer(next_started_ns: int) -> None:
            nonlocal writer, schemas, current_mcap_path, current_segment_index
            nonlocal current_segment_started_ns, current_segment_message_count
            writer.finish()
            file_infos.append({
                "path": current_mcap_path,
                "started_ns": current_segment_started_ns,
                "ended_ns": next_started_ns,
                "message_count": current_segment_message_count,
            })
            current_segment_index += 1
            current_mcap_path = _rosbag_segment_path(output_path, current_segment_index)
            writer = make_writer(current_mcap_path)
            schemas = {
                datatype: writer.register_msgdef(datatype, msgdef)
                for datatype, msgdef in msgdefs_by_datatype.items()
            }
            current_segment_started_ns = next_started_ns
            current_segment_message_count = 0

        def maybe_rotate_writer(next_started_ns: int) -> None:
            if max_split_bytes <= 0 or current_segment_message_count <= 0:
                return
            try:
                current_size = current_mcap_path.stat().st_size
            except Exception:
                current_size = 0
            if current_size >= max_split_bytes:
                rotate_writer(next_started_ns)

        def writer_loop() -> None:
            nonlocal sequence, current_segment_message_count
            global RUNNING
            while True:
                item = record_queue.get()
                try:
                    if item is None:
                        return
                    topic, datatype, msg, timestamp_ns = item
                    if expose_callable_fields is not None:
                        try:
                            expose_callable_fields(msg)
                        except Exception:
                            pass
                    with segment_lock:
                        maybe_rotate_writer(timestamp_ns)
                        schema = schemas[datatype]
                    with lock:
                        current_sequence = sequence
                        sequence += 1
                    writer.write_message(topic, schema, _mcap_value(msg), log_time=timestamp_ns, publish_time=timestamp_ns, sequence=current_sequence)
                    with segment_lock:
                        current_segment_message_count += 1
                    with lock:
                        written_counts[topic] = written_counts.get(topic, 0) + 1
                except Exception as exc:
                    with lock:
                        writer_error["error"] = str(exc)
                        writer_error["traceback"] = traceback.format_exc()[-4000:]
                    RUNNING = False
                    return
                finally:
                    record_queue.task_done()

        def make_callback(topic: str, datatype: str):
            def callback(msg: Any) -> None:
                timestamp_ns = time.time_ns()
                queued = False
                try:
                    record_queue.put_nowait((topic, datatype, msg, timestamp_ns))
                    queued = True
                except queue.Full:
                    pass
                with lock:
                    if queued:
                        received_counts[topic] = received_counts.get(topic, 0) + 1
                    else:
                        dropped_counts[topic] = dropped_counts.get(topic, 0) + 1

            return callback

        subscriptions = []
        for item, topic in input_topics:
            datatype = _normalize_type(str(item.get("dataType") or ""))
            msg_cls = _import_type_class(datatype)
            if datatype not in schemas:
                msgdefs_by_datatype[datatype] = _msgdef_for(datatype, msg_cls)
                schemas[datatype] = writer.register_msgdef(datatype, msgdefs_by_datatype[datatype])
            topic_types[topic] = datatype
            subscription = node.create_subscription(msg_cls, topic, make_callback(topic, datatype), _record_qos(datatype, qos_depth))
            listener = getattr(subscription, "_listener", None)
            set_auto_loan_receive = getattr(listener, "set_auto_loan_receive", None)
            if callable(set_auto_loan_receive):
                try:
                    set_auto_loan_receive(False)
                except Exception:
                    pass
            subscriptions.append(subscription)
            received_counts[topic] = 0
            written_counts[topic] = 0
            dropped_counts[topic] = 0

        executor = MultiThreadedExecutor()
        executor.add_node(node)
        writer_thread = threading.Thread(target=writer_loop, name="mcap-record-writer", daemon=True)
        writer_thread.start()
        executor_thread = threading.Thread(target=_spin_executor, args=(executor, status_path), name="mcap-record-dds", daemon=True)
        executor_thread.start()
        last_status_at = 0.0
        last_written_total = -1
        next_status_at = 0.0
        started_ns = current_segment_started_ns
        _write_status(status_path, running=True, phase="record", status=f"recording to {bag_dir}", received=0, recorded=0, queued=0, dropped=0, path=str(bag_dir), mcapPath=str(output_path), metadataPath=str(bag_dir / "metadata.yaml"), splitSizeMb=split_size_mb, segments=1, topics=list(written_counts))
        while RUNNING:
            time.sleep(0.05)
            now = time.time()
            if now >= next_status_at:
                with lock:
                    received_total = total(received_counts)
                    written_total = total(written_counts)
                    dropped_total = total(dropped_counts)
                    by_topic = dict(written_counts)
                    error = dict(writer_error)
                with segment_lock:
                    status_mcap_path = current_mcap_path
                    status_segments = len(file_infos) + 1
                if error:
                    raise RuntimeError(error.get("traceback") or error.get("error") or "MCAP writer failed")
                if written_total != last_written_total or now - last_status_at >= 1.0:
                    _write_status(
                        status_path,
                        running=True,
                        status=f"{bag_dir.name} recording / {written_total} messages / {status_segments} file{'s' if status_segments != 1 else ''}",
                        received=received_total,
                        recorded=written_total,
                        queued=record_queue.qsize(),
                        dropped=dropped_total,
                        path=str(bag_dir),
                        mcapPath=str(status_mcap_path),
                        metadataPath=str(bag_dir / "metadata.yaml"),
                        splitSizeMb=split_size_mb,
                        segments=status_segments,
                        topics=by_topic,
                    )
                    last_written_total = written_total
                    last_status_at = now
                next_status_at = now + 0.25
        with lock:
            error = dict(writer_error)
        if error:
            raise RuntimeError(error.get("traceback") or error.get("error") or "MCAP writer failed")
        with segment_lock:
            status_mcap_path = current_mcap_path
            status_segments = len(file_infos) + 1
        _write_status(status_path, running=True, phase="drain", status=f"draining {bag_dir.name}", queued=record_queue.qsize(), path=str(bag_dir), mcapPath=str(status_mcap_path), metadataPath=str(bag_dir / "metadata.yaml"), splitSizeMb=split_size_mb, segments=status_segments)
        record_queue.join()
        record_queue.put(None)
        writer_thread.join(timeout=5.0)
        with lock:
            error = dict(writer_error)
        if error:
            raise RuntimeError(error.get("traceback") or error.get("error") or "MCAP writer failed")
        with lock:
            written_total = total(written_counts)
            received_total = total(received_counts)
            dropped_total = total(dropped_counts)
            by_topic = dict(written_counts)
        ended_ns = time.time_ns()
        with segment_lock:
            writer.finish()
            file_infos.append({
                "path": current_mcap_path,
                "started_ns": current_segment_started_ns,
                "ended_ns": ended_ns,
                "message_count": current_segment_message_count,
            })
            final_mcap_path = current_mcap_path
            final_segments = len(file_infos)
        _write_rosbag2_metadata(
            bag_dir,
            storage_id="mcap",
            started_ns=started_ns,
            ended_ns=ended_ns,
            topic_types=topic_types,
            topic_counts=by_topic,
            file_infos=file_infos,
        )
        try:
            executor.remove_node(node)
        except Exception:
            pass
        try:
            executor.shutdown(timeout_sec=0.2)
        except Exception:
            pass
        if executor_thread.is_alive():
            _write_status(status_path, running=True, phase="shutdown", status="MCAP record stopped; DDS thread did not exit cleanly", received=received_total, recorded=written_total, queued=record_queue.qsize(), dropped=dropped_total, path=str(bag_dir), mcapPath=str(final_mcap_path), metadataPath=str(bag_dir / "metadata.yaml"), splitSizeMb=split_size_mb, segments=final_segments, topics=by_topic)
        try:
            node.destroy_node()
        except Exception:
            pass
        try:
            rclpy.shutdown()
        except Exception:
            pass
        _write_status(status_path, running=False, ended=True, status=f"{bag_dir.name} record stopped / {written_total} messages / {final_segments} file{'s' if final_segments != 1 else ''}", received=received_total, recorded=written_total, queued=record_queue.qsize(), dropped=dropped_total, path=str(bag_dir), mcapPath=str(final_mcap_path), metadataPath=str(bag_dir / "metadata.yaml"), splitSizeMb=split_size_mb, segments=final_segments, topics=by_topic)
        return 0
    except Exception as exc:
        traceback.print_exc()
        try:
            error_split_size_mb = max(0.0, float(params.get("splitSizeMb") or 0))
        except Exception:
            error_split_size_mb = 0.0
        _write_status(status_path, running=False, error=str(exc), traceback=traceback.format_exc()[-4000:], status=f"error: {exc}", path=str(bag_dir), mcapPath=str(output_path), metadataPath=str(bag_dir / "metadata.yaml"), splitSizeMb=error_split_size_mb)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
