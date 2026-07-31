#!/usr/bin/env python3
"""Make a copied C++ dependency prefix relocatable.

A prefix built elsewhere records the absolute paths of the machine that built
it, so copying it into the standalone bundle leaves two kinds of dangling
reference:

1. Shared-library symlinks pointing at absolute build-machine paths, e.g.
   ``libfastrtps.so -> /home/runner/work/.../lwrcl_cpp/lib/libfastrtps.so.2.11``.
   Every soname link breaks, so C++ node executables cannot even load.
2. CMake package files and pkg-config files naming absolute paths, e.g.
   ``/home/runner/work/.../.build_cpp_prefix/fastdds/lib/libtinyxml2.so``.
   find_package() then succeeds while linking fails with
   "No rule to make target", so C++ custom nodes cannot be built.

This converts in-prefix absolute symlinks to relative ones and rewrites stale
absolute references to paths relative to the file that contains them
(``${CMAKE_CURRENT_LIST_DIR}`` for CMake, ``${pcfiledir}`` for pkg-config).
Only references that are broken *and* satisfiable inside the prefix are
touched; anything that still resolves is left alone.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Directories a prefix-relative path can start from.
ANCHORS = ("lib", "include", "share", "bin", "tools", "lib64")

# Absolute POSIX/Windows paths as they appear in CMake lists and .pc files.
# The lookbehind keeps us from matching the tail of a variable expansion such as
# "${PACKAGE_PREFIX_DIR}/include", which is already relocatable.
PATH_PATTERN = re.compile(r"(?<![\w}$.\-/\\])(?:[A-Za-z]:)?[/\\][^\s\"';:()$]{3,}")

# A stale build path always has several components; requiring that avoids
# rewriting bare roots like "/include" or "/lib".
MIN_PATH_SEGMENTS = 3


def _relative_target(prefix: Path, token: str, *, allow_dangling: bool = False) -> Path | None:
    """Return the in-prefix path *token* was meant to point at, if any.

    With *allow_dangling* a symlink counts as present even when its own target
    has not been repaired yet, which is what retargeting a symlink chain needs.
    """
    present = os.path.lexists if allow_dangling else os.path.exists
    normalized = token.replace("\\", "/").rstrip("/")
    if len([part for part in normalized.split("/") if part]) < MIN_PATH_SEGMENTS:
        return None
    for anchor in ANCHORS:
        index = normalized.rfind(f"/{anchor}/")
        if index >= 0:
            candidate = prefix / normalized[index + 1:]
            if present(candidate):
                return candidate
        # Paths that stop at the anchor itself, as in a pkg-config includedir.
        if normalized.endswith(f"/{anchor}"):
            candidate = prefix / anchor
            if candidate.is_dir():
                return candidate
    return None


def _fix_symlinks(prefix: Path, *, verbose: bool) -> int:
    """Turn absolute symlinks that belong to this prefix into relative ones.

    Sonames form chains (``libfoo.so -> libfoo.so.1 -> libfoo.so.1.2.3``), so
    this repeats until nothing changes rather than assuming an order.
    """
    fixed = 0
    while True:
        changed = 0
        for path in sorted(prefix.rglob("*")):
            if not path.is_symlink():
                continue
            raw = os.readlink(path)
            if not os.path.isabs(raw):
                continue
            target = _relative_target(prefix, raw, allow_dangling=True)
            if target is None:
                # Fall back to a same-directory sibling with the same name.
                sibling = path.parent / Path(raw.replace("\\", "/")).name
                if not os.path.lexists(sibling):
                    continue
                target = sibling
            relative = os.path.relpath(target, path.parent)
            path.unlink()
            path.symlink_to(relative)
            changed += 1
            if verbose:
                print(f"  symlink {path.relative_to(prefix)} -> {relative}")
        fixed += changed
        if not changed:
            return fixed


def _rewrite_text(prefix: Path, file_path: Path, text: str, variable: str) -> tuple[str, int]:
    base = file_path.parent
    count = 0
    # A pkg-config "prefix=" line names the prefix root, which has no anchor
    # directory to key off, so resolve those against the prefix directly.
    pc_roots = set()
    if variable == "${pcfiledir}":
        for line in text.splitlines():
            for key in ("prefix", "exec_prefix"):
                head = f"{key}="
                if line.startswith(head):
                    value = line[len(head):].strip()
                    if os.path.isabs(value) and not Path(value).exists():
                        pc_roots.add(value.rstrip("/"))

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        token = match.group(0)
        if Path(token).exists():
            return token
        target = _relative_target(prefix, token)
        if target is None and token.rstrip("/") in pc_roots:
            target = prefix
        if target is None:
            return token
        suffix = os.path.relpath(target, base).replace(os.sep, "/")
        count += 1
        return f"{variable}/{suffix}"

    return PATH_PATTERN.sub(replace, text), count


def relocate(prefix: Path, *, verbose: bool = True) -> int:
    if not prefix.is_dir():
        print(f"relocate_cpp_prefix: no such prefix: {prefix}", file=sys.stderr)
        return 0
    links = _fix_symlinks(prefix, verbose=verbose)
    total = 0
    targets: list[tuple[Path, str]] = []
    for path in prefix.rglob("*.cmake"):
        targets.append((path, "${CMAKE_CURRENT_LIST_DIR}"))
    for path in prefix.rglob("*.pc"):
        targets.append((path, "${pcfiledir}"))
    for path, variable in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated, count = _rewrite_text(prefix, path, text, variable)
        if count and updated != text:
            path.write_text(updated, encoding="utf-8")
            total += count
            if verbose:
                print(f"  relocated {count} path(s) in {path.relative_to(prefix)}")
    if verbose:
        print(f"relocate_cpp_prefix: {links} symlink(s) and {total} path(s) made relative under {prefix}")
    return links + total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefix", help="C++ dependency prefix to make relocatable.")
    parser.add_argument("--quiet", action="store_true", help="Only report failures.")
    args = parser.parse_args(argv)
    relocate(Path(args.prefix), verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
