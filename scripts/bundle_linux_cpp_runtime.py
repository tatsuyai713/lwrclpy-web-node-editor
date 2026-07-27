#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


CONDA_FORGE_BASE = "https://conda.anaconda.org/conda-forge"


def normalize_arch(value: str) -> str:
    value = (value or "").lower()
    if value in {"x86_64", "amd64"}:
        return "linux-64"
    if value in {"aarch64", "arm64"}:
        return "linux-aarch64"
    raise ValueError(f"unsupported Linux architecture: {value or '<empty>'}")


def version_key(value: str) -> tuple[object, ...]:
    parts: list[object] = []
    for part in re.split(r"[._-]", value):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def latest_package(subdir: str, package_name: str) -> str:
    with urllib.request.urlopen(f"{CONDA_FORGE_BASE}/{subdir}/repodata.json", timeout=60) as response:
        repodata = json.load(response)
    candidates: list[tuple[tuple[object, ...], int, str]] = []
    for section in ("packages", "packages.conda"):
        for filename, metadata in repodata.get(section, {}).items():
            if metadata.get("name") != package_name:
                continue
            candidates.append((
                version_key(str(metadata.get("version") or "0")),
                int(metadata.get("build_number") or 0),
                filename,
            ))
    if not candidates:
        raise RuntimeError(f"conda-forge package not found: {subdir}/{package_name}")
    return sorted(candidates)[-1][2]


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def extract_conda_package(package_path: Path, target_dir: Path) -> None:
    import zstandard  # type: ignore

    if package_path.name.endswith(".tar.bz2"):
        with tarfile.open(package_path, "r:bz2") as archive:
            extract_runtime_members(archive, target_dir)
        return

    with zipfile.ZipFile(package_path) as conda:
        pkg_members = [name for name in conda.namelist() if name.startswith("pkg-") and name.endswith(".tar.zst")]
        if not pkg_members:
            raise RuntimeError(f"package payload not found in {package_path}")
        compressed = conda.read(pkg_members[0])
    decompressed = zstandard.ZstdDecompressor().decompress(compressed)
    with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:") as archive:
        extract_runtime_members(archive, target_dir)


def extract_runtime_members(archive: tarfile.TarFile, target_dir: Path) -> None:
    wanted_prefixes = (
        "lib/libstdc++.so",
        "lib/libgcc_s.so",
    )
    for member in archive.getmembers():
        if not any(member.name.startswith(prefix) for prefix in wanted_prefixes):
            continue
        if not member.isfile() and not member.issym():
            continue
        member_path = Path(member.name)
        if len(member_path.parts) < 2 or member_path.parts[0] != "lib":
            continue
        try:
            archive.extract(member, target_dir, filter="fully_trusted")
        except TypeError:
            archive.extract(member, target_dir)


def copy_runtime_libs(source_root: Path, internal_dir: Path) -> list[Path]:
    copied: list[Path] = []
    source_lib = source_root / "lib"
    internal_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("libstdc++.so*", "libgcc_s.so*"):
        for source in source_lib.glob(pattern):
            target = internal_dir / source.name
            if target.exists() or target.is_symlink():
                target.unlink()
            if source.is_symlink():
                os.symlink(os.readlink(source), target)
            elif source.is_file():
                shutil.copy2(source, target)
            copied.append(target)
    required = ["libstdc++.so.6", "libgcc_s.so.1"]
    missing = [name for name in required if not (internal_dir / name).exists()]
    if missing:
        raise RuntimeError(f"missing bundled GCC runtime libraries: {', '.join(missing)}")
    return copied


def assert_glibcxx(internal_dir: Path, required: str) -> None:
    libstdcxx = internal_dir / "libstdc++.so.6"
    try:
        output = subprocess.check_output(["strings", str(libstdcxx)], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        print("WARNING: could not run strings to verify GLIBCXX versions", file=sys.stderr)
        return
    if required not in output.splitlines():
        raise RuntimeError(f"{libstdcxx} does not provide {required}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("internal_dir", type=Path)
    parser.add_argument("--arch", default=platform.machine())
    parser.add_argument("--required-glibcxx", default="GLIBCXX_3.4.32")
    args = parser.parse_args()

    subdir = normalize_arch(args.arch)
    with tempfile.TemporaryDirectory(prefix="lwrclpy-gcc-runtime-") as temp_name:
        temp_dir = Path(temp_name)
        extract_root = temp_dir / "extract"
        for package_name in ("libstdcxx", "libgcc"):
            filename = latest_package(subdir, package_name)
            package_path = temp_dir / filename
            url = f"{CONDA_FORGE_BASE}/{subdir}/{filename}"
            print(f"Bundling Linux C++ runtime from {url}")
            download(url, package_path)
            extract_conda_package(package_path, extract_root)
        copied = copy_runtime_libs(extract_root, args.internal_dir)
    assert_glibcxx(args.internal_dir, args.required_glibcxx)
    for path in copied:
        print(f"Bundled runtime library: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
