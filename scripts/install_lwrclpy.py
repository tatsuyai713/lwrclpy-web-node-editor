#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import urllib.request


LATEST_RELEASE_URL = "https://api.github.com/repos/tatsuyai713/lwrclpy/releases/latest"
RELEASES_URL = "https://api.github.com/repos/tatsuyai713/lwrclpy/releases"


def main() -> int:
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    machine = platform.machine().lower()
    release = fetch_latest_release()
    assets = release.get("assets", [])
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
    print(f"Installing {asset['name']} from {release.get('tag_name', 'latest')}")
    install_target(asset["browser_download_url"])
    return 0


def install_target(target: str) -> None:
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", "--upgrade", "--python", sys.executable, target])
    else:
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", target])


def fetch_latest_release() -> dict:
    try:
        with urllib.request.urlopen(LATEST_RELEASE_URL, timeout=20) as response:
            release = json.load(response)
        if release.get("assets"):
            return release
    except Exception:
        pass
    with urllib.request.urlopen(RELEASES_URL, timeout=20) as response:
        releases = json.load(response)
    for release in releases:
        if not release.get("draft") and not release.get("prerelease") and release.get("assets"):
            return release
    return {"tag_name": "latest", "assets": []}


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
