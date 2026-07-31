from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def find_backend_root(app_root: Path) -> Path:
    candidates = [
        app_root / "resources" / "lwrclpy-web-node-editor-server",
        app_root / "Contents" / "Resources" / "lwrclpy-web-node-editor-server",
        app_root,
    ]
    for candidate in candidates:
        if (candidate / "_internal").is_dir():
            return candidate
    for candidate in app_root.rglob("lwrclpy-web-node-editor-server"):
        if candidate.is_dir() and (candidate / "_internal").is_dir():
            return candidate
    raise FileNotFoundError(f"backend _internal directory not found under {app_root}")


def any_match(root: Path, *patterns: str) -> bool:
    return any(path.exists() for pattern in patterns for path in root.glob(pattern))


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app_root", type=Path)
    parser.add_argument("--require-cpp-prefix", action="store_true")
    args = parser.parse_args(argv)

    app_root = args.app_root.resolve()
    backend = find_backend_root(app_root)
    internal = backend / "_internal"
    errors: list[str] = []

    require((internal / "lwrclpy").is_dir(), "missing lwrclpy package", errors)
    require((internal / "fastdds").exists() or (internal / "lwrclpy" / "_vendor" / "fastdds").exists(), "missing FastDDS Python package", errors)
    require(any_match(internal, "lwrclpy/_vendor/lib/*fastdds*", "lwrclpy/_vendor/lib/*fastrtps*", "*fastdds*", "*fastrtps*"), "missing FastDDS runtime library", errors)
    require(any_match(internal, "lwrclpy/_vendor/lib/*fastcdr*", "*fastcdr*"), "missing Fast-CDR runtime library", errors)
    require(any_match(internal, "cmake/data/bin/cmake*", "cmake/data/bin/cmake.exe", "cmake-*.dist-info/*"), "missing bundled CMake package", errors)
    require((internal / "samples").is_dir(), "missing sample projects", errors)
    require((internal / "custom_nodes").is_dir(), "missing bundled custom nodes directory", errors)

    cpp_prefix = internal / "lwrcl_cpp"
    cpp_header = cpp_prefix / "include" / "lwrcl.hpp"
    cpp_lib = any((cpp_prefix / "lib").glob("*lwrcl*")) if (cpp_prefix / "lib").is_dir() else False
    if args.require_cpp_prefix:
        require(cpp_header.is_file(), "missing C++ prefix header lwrcl_cpp/include/lwrcl.hpp", errors)
        require(cpp_lib, "missing C++ prefix lwrcl library under lwrcl_cpp/lib", errors)
    elif cpp_prefix.exists() and (not cpp_header.is_file() or not cpp_lib):
        errors.append("incomplete lwrcl_cpp prefix")

    if cpp_prefix.is_dir():
        # A prefix that still points at the build machine passes find_package()
        # and then fails to link, so catch it here instead of in a user's build.
        dangling = [
            str(path.relative_to(cpp_prefix))
            for path in cpp_prefix.rglob("*")
            if path.is_symlink() and not path.exists()
        ]
        if dangling:
            errors.append(f"lwrcl_cpp has {len(dangling)} dangling symlink(s), e.g. {dangling[0]}")
        stale = []
        for pattern in ("**/*.cmake", "**/*.pc"):
            for path in cpp_prefix.glob(pattern):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for token in re.findall(r"(?<![\w}$.\-/\\])/[^\s\"';:()$]{3,}", text):
                    if not Path(token).exists() and token.startswith(("/home/", "/Users/", "/build")):
                        stale.append(f"{path.relative_to(cpp_prefix)}: {token}")
                        break
        if stale:
            errors.append(f"lwrcl_cpp references {len(stale)} unusable build path(s), e.g. {stale[0]}")

    print(f"Checked app: {app_root}")
    print(f"Backend: {backend}")
    print(f"Require C++ prefix: {args.require_cpp_prefix}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Standalone bundle verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
