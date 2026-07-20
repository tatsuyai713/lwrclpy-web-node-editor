#!/usr/bin/env python
from __future__ import annotations

import os
import sys
import builtins
import importlib.util
import shutil
from pathlib import Path


def _frozen_site_dir() -> "Path | None":
    """Return the user-local lwrclpy_site directory for frozen-app mode, else None."""
    if not getattr(sys, "frozen", False):
        return None
    env_home = os.environ.get("LWRCLPY_WEB_NODE_EDITOR_HOME", "").strip()
    if env_home:
        home = Path(env_home).expanduser().resolve()
    elif sys.platform == "darwin":
        home = (Path.home() / "Library" / "Application Support" / "lwrclpy-web-node-editor").resolve()
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        home = (base / "lwrclpy-web-node-editor").resolve()
    else:
        home = (Path.home() / ".local" / "share" / "lwrclpy-web-node-editor").resolve()
    return home / "lwrclpy_site"


def _prepend_to_sys_path(directory: "Path | None") -> None:
    if directory is None:
        return
    s = str(directory)
    if s not in sys.path:
        sys.path.insert(0, s)


def _prepend_extra_site_paths_from_env() -> None:
    raw = os.environ.get("LWRCLPY_EXTRA_SITE_PACKAGES", "").strip()
    if not raw:
        return
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        _prepend_to_sys_path(Path(entry))


# Prepend the user-local lwrclpy_site to sys.path before *any* package imports.
# This runs for both the main process and every worker sub-process launched by
# the frozen binary so that rclpy (and fastdds_python) resolve correctly.
_prepend_to_sys_path(_frozen_site_dir())
_prepend_extra_site_paths_from_env()


def _ensure_orig_import_alias() -> None:
    # PySide6/libshiboken expects builtins.__orig_import__ in some embedded
    # execution flows. Ensure it exists before any worker logic runs.
    if not hasattr(builtins, "__orig_import__"):
        builtins.__orig_import__ = builtins.__import__


_ensure_orig_import_alias()


from lwrclpy_web_node_editor.runtime_exec import standalone_app_home  # noqa: E402


def _configure_startup_local_lwrclpy(argv: list[str]) -> None:
    for index, arg in enumerate(argv):
        value = ""
        if arg == "--lwrclpy-wheel" and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith("--lwrclpy-wheel="):
            value = arg.split("=", 1)[1]
        if not value:
            continue
        from lwrclpy_web_node_editor.runtime_exec import configure_local_lwrclpy_wheel

        configure_local_lwrclpy_wheel(value)
        if not getattr(sys, "frozen", False):
            _install_local_lwrclpy_for_current_python()
        return


def _install_local_lwrclpy_for_current_python() -> None:
    from lwrclpy_web_node_editor.runtime_exec import LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV, local_lwrclpy_wheel, local_lwrclpy_wheel_marker

    wheel = local_lwrclpy_wheel()
    if wheel is None:
        return
    marker = local_lwrclpy_wheel_marker(wheel)
    if os.environ.get(LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV) == marker:
        return
    import shutil
    import subprocess
    import importlib

    uv = shutil.which("uv")
    if uv:
        command = [
            uv,
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache",
            "--python",
            sys.executable,
            str(wheel),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            str(wheel),
        ]
    print(f"[lwrclpy-web-node-editor] Installing local lwrclpy wheel: {wheel}", flush=True)
    subprocess.run(command, check=True)
    os.environ[LWRCLPY_LOCAL_WHEEL_INSTALLED_ENV] = marker
    importlib.invalidate_caches()


def _dispatch_worker(argv: list[str]) -> int | None:
    if not argv:
        return None
    mode = argv[0]
    if mode == "--worker-node":
        from lwrclpy_web_node_editor import node_worker

        if len(argv) < 2:
            print("usage: lwrclpy-web-node-editor --worker-node CONFIG_JSON", file=sys.stderr)
            return 2
        return node_worker.main([argv[1]])
    if mode == "--worker-video":
        from lwrclpy_web_node_editor import video_dds_worker

        if len(argv) < 2:
            print("usage: lwrclpy-web-node-editor --worker-video CONFIG_JSON", file=sys.stderr)
            return 2
        sys.argv = [sys.argv[0], argv[1]]
        return video_dds_worker.main()
    if mode == "--worker-dds-tap":
        from lwrclpy_web_node_editor import dds_tap_worker

        if len(argv) < 2:
            print("usage: lwrclpy-web-node-editor --worker-dds-tap CONFIG_JSON", file=sys.stderr)
            return 2
        sys.argv = [sys.argv[0], argv[1]]
        return dds_tap_worker.main()
    if mode == "--worker-builtin-source":
        from lwrclpy_web_node_editor import builtin_source_worker

        if len(argv) < 2:
            print("usage: lwrclpy-web-node-editor --worker-builtin-source CONFIG_JSON", file=sys.stderr)
            return 2
        sys.argv = [sys.argv[0], argv[1]]
        return builtin_source_worker.main()
    if mode == "--worker-mcap-record":
        from lwrclpy_web_node_editor import mcap_record_worker

        if len(argv) < 2:
            print("usage: lwrclpy-web-node-editor --worker-mcap-record CONFIG_JSON", file=sys.stderr)
            return 2
        sys.argv = [sys.argv[0], argv[1]]
        return mcap_record_worker.main()
    return None


def _prepare_standalone_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    home = standalone_app_home()
    home.mkdir(parents=True, exist_ok=True)
    _sync_bundled_samples(home)
    os.chdir(home)
    # Ensure lwrclpy_site exists and is in sys.path (may not have existed yet at
    # module load time when _frozen_site_dir() first ran).
    site_dir = home / "lwrclpy_site"
    site_dir.mkdir(parents=True, exist_ok=True)
    _prepend_to_sys_path(site_dir)
    from lwrclpy_web_node_editor.runtime_exec import local_lwrclpy_wheel

    if local_lwrclpy_wheel() is not None:
        _auto_update_lwrclpy(site_dir)
        _prefer_lwrclpy_site_packages(site_dir)
    elif _has_lwrclpy_site_packages(site_dir):
        _prefer_lwrclpy_site_packages(site_dir)
    elif not _bundled_lwrclpy_is_available():
        _auto_update_lwrclpy(site_dir)
        _prefer_lwrclpy_site_packages(site_dir)


def _bundled_samples_dir() -> Path | None:
    candidates: list[Path] = []
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "_internal" / "samples")
    candidates.append(exe_dir / "samples")
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.insert(0, Path(meipass) / "samples")
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _sync_bundled_samples(home: Path) -> None:
    source = _bundled_samples_dir()
    if source is None:
        return
    target = home / "samples"
    target.mkdir(parents=True, exist_ok=True)
    for source_path in source.rglob("*"):
        if not source_path.is_file():
            continue
        rel = source_path.relative_to(source)
        target_path = target / rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target_path.exists() and target_path.read_bytes() == source_path.read_bytes():
                continue
        except Exception:
            pass
        shutil.copy2(source_path, target_path)


def _has_lwrclpy_site_packages(site_dir: Path) -> bool:
    return (site_dir / "lwrclpy" / "__init__.py").exists() and (site_dir / "rclpy" / "__init__.py").exists()


def _bundled_lwrclpy_is_available() -> bool:
    try:
        import fastdds  # noqa: F401
        import lwrclpy  # noqa: F401
        import rclpy  # noqa: F401
    except Exception:
        for package in ("fastdds", "lwrclpy", "rclpy"):
            for loaded_name in [name for name in sys.modules if name == package or name.startswith(package + ".")]:
                sys.modules.pop(loaded_name, None)
        return False
    return True


def _prefer_lwrclpy_site_packages(site_dir: Path) -> None:
    """Force lwrclpy packages from the writable site dir ahead of PyInstaller's importer."""
    if not getattr(sys, "frozen", False):
        return
    for package in ("fastdds", "lwrclpy", "rclpy"):
        package_dir = site_dir / package
        init_py = package_dir / "__init__.py"
        if not init_py.exists():
            continue
        for loaded_name in [name for name in sys.modules if name == package or name.startswith(package + ".")]:
            sys.modules.pop(loaded_name, None)
        spec = importlib.util.spec_from_file_location(
            package,
            init_py,
            submodule_search_locations=[str(package_dir)],
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop(package, None)
            raise


def _auto_update_lwrclpy(site_dir: Path) -> None:
    """Download and install the latest lwrclpy release into *site_dir*."""
    import importlib.util
    from lwrclpy_web_node_editor.runtime_exec import find_lwrclpy_installer

    installer = find_lwrclpy_installer()
    if installer is None:
        print("[lwrclpy-web-node-editor] lwrclpy installer not found; skipping auto-update.", flush=True)
        return
    spec = importlib.util.spec_from_file_location("_install_lwrclpy", installer)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"[lwrclpy-web-node-editor] Failed to load lwrclpy installer: {exc}", flush=True)
        return
    if not hasattr(mod, "install_to_target"):
        return
    try:
        print("[lwrclpy-web-node-editor] Checking for lwrclpy updates...", flush=True)
        mod.install_to_target(site_dir)
    except Exception as exc:
        print(f"[lwrclpy-web-node-editor] lwrclpy auto-update failed (continuing): {exc}", flush=True)


if __name__ == "__main__":
    exit_code: int | None = None
    try:
        argv = sys.argv[1:]
        worker_exit = _dispatch_worker(argv)
        if worker_exit is not None:
            exit_code = int(worker_exit)
        else:
            _configure_startup_local_lwrclpy(argv)
            _prepare_standalone_runtime()
            if argv and argv[0] == "--cli-run":
                from lwrclpy_web_node_editor import cli_run

                exit_code = int(cli_run.main(argv[1:]))
            else:
                # Import server lazily, AFTER auto-update has installed lwrclpy into
                # lwrclpy_site and it has been prepended to sys.path. This ensures
                # that fastdds_python and other native extensions bundled inside the
                # latest lwrclpy wheel are importable when graph.py is first loaded.
                from lwrclpy_web_node_editor import server  # noqa: E402
                if argv and argv[0] == "--desktop":
                    from lwrclpy_web_node_editor import desktop_app

                    exit_code = int(desktop_app.main(argv[1:]))
                elif argv and argv[0] == "--server":
                    exit_code = int(server.main(argv[1:]))
                elif getattr(sys, "frozen", False) and not argv:
                    from lwrclpy_web_node_editor import desktop_app

                    exit_code = int(desktop_app.main([]))
                else:
                    exit_code = int(server.main(argv))
    except SystemExit as exc:
        value = exc.code
        if isinstance(value, int):
            exit_code = value
        elif value is None:
            exit_code = 0
        else:
            exit_code = 1
    if exit_code is None:
        exit_code = 0
    if getattr(sys, "frozen", False):
        os._exit(exit_code)
    raise SystemExit(exit_code)
