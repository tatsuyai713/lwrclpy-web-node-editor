from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _path_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def write_bytes_atomic(path: Path, data: bytes | bytearray | memoryview) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("status write returned 0 bytes")
                view = view[written:]
        finally:
            os.close(fd)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    write_bytes_atomic(path, data)
