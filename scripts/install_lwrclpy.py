#!/usr/bin/env python3
from __future__ import annotations

import platform
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
import tempfile
from pathlib import Path


LATEST_TAG_RELEASE_URL = "https://github.com/tatsuyai713/lwrclpy/releases/expanded_assets/latest"
GITHUB_BASE_URL = "https://github.com"
LOCAL_WHEEL_ENV = "LWRCLPY_LOCAL_WHEEL"


def main() -> int:
    local_wheel = local_wheel_from_env()
    if local_wheel is not None:
        print(f"Installing local lwrclpy wheel {local_wheel}")
        install_target(str(local_wheel), force_reinstall=True, no_cache=True)
        return 0
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    machine = platform.machine().lower()
    assets = fetch_latest_tag_assets()
    candidates = []
    for asset in assets:
        name = asset["name"]
        if not name.endswith(".whl") or py_tag not in name:
            continue
        if system == "darwin" and "macosx" in name:
            candidates.append(asset)
        elif system == "linux" and "linux" in name:
            if "x86_64" in name and machine in {"x86_64", "amd64"}:
                candidates.append(asset)
            elif "aarch64" in name and machine in {"aarch64", "arm64"}:
                candidates.append(asset)
        elif system == "windows" and ("win" in name or "windows" in name):
            if "amd64" in name or "x86_64" in name:
                if machine in {"amd64", "x86_64"}:
                    candidates.append(asset)
            elif "arm64" in name or "aarch64" in name:
                if machine in {"arm64", "aarch64"}:
                    candidates.append(asset)
    if not candidates:
        print(f"No lwrclpy wheel found for Python {py_tag} on {platform.platform()}", file=sys.stderr)
        return 1
    asset = prefer_platform(candidates)
    print(f"Installing {asset['name']} from latest")
    install_target(asset["url"], force_reinstall=True, no_cache=True)
    return 0


def local_wheel_from_env() -> Path | None:
    raw = os.environ.get(LOCAL_WHEEL_ENV, "").strip()
    if not raw:
        return None
    wheel = Path(raw).expanduser().resolve()
    return wheel if wheel.exists() and wheel.suffix == ".whl" else None


def install_target(target: str, force_reinstall: bool = False, no_cache: bool = False) -> None:
    uv = shutil.which("uv")
    if uv:
        command = [uv, "pip", "install", "--upgrade", "--python", sys.executable]
        if force_reinstall:
            command.append("--force-reinstall")
        if no_cache:
            command.append("--no-cache")
        subprocess.check_call([*command, target])
    else:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "--version"])
        except subprocess.CalledProcessError:
            subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        command = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if force_reinstall:
            command.append("--force-reinstall")
        if no_cache:
            command.append("--no-cache-dir")
        subprocess.check_call([*command, target])


def fetch_latest_tag_assets() -> list[dict[str, str]]:
    request = urllib.request.Request(
        LATEST_TAG_RELEASE_URL,
        headers={
            "User-Agent": "lwrclpy-web-node-editor-installer",
            "Accept": "text/html",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")
    assets: dict[str, dict[str, str]] = {}
    for href in re.findall(r'href="([^"]+\.whl(?:\?[^"]*)?)"', html):
        decoded = urllib.parse.unquote(href.replace("&amp;", "&"))
        path = decoded.split("?", 1)[0]
        if "/tatsuyai713/lwrclpy/releases/download/latest/" not in path:
            continue
        url = urllib.parse.urljoin(GITHUB_BASE_URL, decoded)
        name = _path_like_name(path)
        assets[name] = {"name": name, "url": url}
    return sorted(assets.values(), key=lambda asset: asset["name"])


def _path_like_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def prefer_platform(assets: list[dict]) -> dict:
    def score(asset: dict) -> tuple[int, str]:
        name = asset["name"].lower()
        universal_score = 2 if "universal2" in name else 0
        machine = platform.machine().lower()
        machine_score = 1
        if machine in {"arm64", "aarch64"} and any(tag in name for tag in ("arm64", "aarch64")):
            machine_score = 3
        elif machine in {"x86_64", "amd64"} and "x86_64" in name:
            machine_score = 3
        return (machine_score + universal_score, asset["name"])

    return sorted(assets, key=score, reverse=True)[0]


def install_to_target(target_dir: "Path | str") -> int:
    """Install the latest lwrclpy wheel into *target_dir* using uv --target.

    This is used by the frozen (PyInstaller) desktop app to install lwrclpy
    including native extensions such as fastdds_python into a writable
    user-local directory that is prepended to sys.path at startup.
    """
    from pathlib import Path as _Path
    target_dir = _Path(target_dir)
    local_wheel = local_wheel_from_env()
    if local_wheel is not None:
        print(f"Installing local lwrclpy wheel {local_wheel} to {target_dir}")
        _install_wheel_to_target(str(local_wheel), target_dir)
        return 0
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    machine = platform.machine().lower()
    assets = fetch_latest_tag_assets()
    candidates = []
    for asset in assets:
        name = asset["name"]
        if not name.endswith(".whl") or py_tag not in name:
            continue
        if system == "darwin" and "macosx" in name:
            candidates.append(asset)
        elif system == "linux" and "linux" in name:
            if "x86_64" in name and machine in {"x86_64", "amd64"}:
                candidates.append(asset)
            elif "aarch64" in name and machine in {"aarch64", "arm64"}:
                candidates.append(asset)
        elif system == "windows" and ("win" in name or "windows" in name):
            if "amd64" in name or "x86_64" in name:
                if machine in {"amd64", "x86_64"}:
                    candidates.append(asset)
            elif "arm64" in name or "aarch64" in name:
                if machine in {"arm64", "aarch64"}:
                    candidates.append(asset)
    if not candidates:
        print(f"No lwrclpy wheel found for Python {py_tag} on {platform.platform()}", file=sys.stderr)
        return 1
    asset = prefer_platform(candidates)
    print(f"Installing {asset['name']} to {target_dir}")
    _install_wheel_to_target(asset["url"], target_dir)
    return 0


def _install_wheel_to_target(wheel_url: str, target_dir: "Path") -> None:
    """Install *wheel_url* into *target_dir* via uv --target (no Python interpreter needed)."""
    import shutil as _shutil
    from pathlib import Path as _Path

    uv = _shutil.which("uv")
    if not uv:
        # Common non-PATH locations for uv, checked in priority order
        _meipass = getattr(sys, "_MEIPASS", "")
        _candidates = [
            _Path(_meipass) / "uv" if _meipass else None,           # PyInstaller _internal/
            _Path(sys.executable).resolve().parent / "uv",           # next to frozen binary
            _Path(sys.executable).resolve().parent / "_internal" / "uv",  # onedir layout
            _Path.home() / ".local" / "bin" / "uv",                  # pip/pipx install
            _Path.home() / ".cargo" / "bin" / "uv",                  # cargo install
        ]
        for _c in _candidates:
            if _c is not None and _c.exists():
                uv = str(_c)
                break
    if not uv:
        raise RuntimeError(
            "uv is required for standalone-app lwrclpy installation. "
            "Install uv: https://docs.astral.sh/uv/"
        )
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    python_platform = _uv_python_platform()
    command = [
        uv, "pip", "install",
        "--no-cache",
        "--python-version", python_version,
        "--python-platform", python_platform,
        "--target", str(target_dir),
        wheel_url,
    ]
    try:
        subprocess.check_call(command)
    except subprocess.CalledProcessError:
        _extract_wheel_to_target(wheel_url, target_dir)


def _uv_python_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
    if system == "linux":
        return "aarch64-manylinux2014" if machine in {"arm64", "aarch64"} else "x86_64-manylinux2014"
    if system == "windows":
        return "aarch64-pc-windows-msvc" if machine in {"arm64", "aarch64"} else "x86_64-pc-windows-msvc"
    return system


def _extract_wheel_to_target(wheel_url: str, target_dir: "Path") -> None:
    """Fallback installer for frozen apps when uv rejects a valid macOS wheel tag.

    lwrclpy release wheels are self-contained for our runtime use.  Extracting
    the wheel directly avoids uv's static macOS platform floor check, which can
    reject ``macosx_15_0_universal2`` wheels on newer macOS hosts.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    local_path = Path(wheel_url).expanduser()
    if local_path.exists():
        tmp_path = str(local_path)
        remove_tmp = False
    else:
        remove_tmp = True
        with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as tmp:
            tmp_path = tmp.name
            with urllib.request.urlopen(wheel_url, timeout=60) as response:
                shutil.copyfileobj(response, tmp)
    try:
        with zipfile.ZipFile(tmp_path) as wheel:
            wheel.extractall(target_dir)
    finally:
        if remove_tmp:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
