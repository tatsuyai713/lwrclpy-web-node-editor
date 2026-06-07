#!/usr/bin/env python3
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


LATEST_TAG_RELEASE_URL = "https://github.com/tatsuyai713/lwrclpy/releases/expanded_assets/latest"
GITHUB_BASE_URL = "https://github.com"


def main() -> int:
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
    if not candidates:
        print(f"No lwrclpy wheel found for Python {py_tag} on {platform.platform()}", file=sys.stderr)
        return 1
    asset = prefer_platform(candidates)
    print(f"Installing {asset['name']} from latest")
    install_target(asset["url"], force_reinstall=True, no_cache=True)
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
