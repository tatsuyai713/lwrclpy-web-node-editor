#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import urllib.request


RELEASES_URL = "https://api.github.com/repos/tatsuyai713/lwrclpy/releases"


def main() -> int:
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    system = platform.system().lower()
    machine = platform.machine().lower()
    assets = fetch_assets()
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
    asset = prefer_latest(candidates)
    print(f"Installing {asset['name']}")
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", "--python", sys.executable, asset["browser_download_url"]])
    else:
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", asset["browser_download_url"]])
    return 0


def fetch_assets() -> list[dict]:
    with urllib.request.urlopen(RELEASES_URL, timeout=20) as response:
        releases = json.load(response)
    assets = []
    for release in releases:
        if release.get("tag_name") in {"v0.3.2", "latest-macos", "latest"}:
            assets.extend(release.get("assets", []))
    return assets


def prefer_latest(assets: list[dict]) -> dict:
    def score(asset: dict) -> tuple[int, str]:
        url = asset["browser_download_url"]
        tag_score = 2 if "/v0.3.2/" in url else 1
        universal_score = 1 if "universal2" in asset["name"] else 0
        return (tag_score + universal_score, asset["name"])

    return sorted(assets, key=score, reverse=True)[0]


if __name__ == "__main__":
    raise SystemExit(main())
