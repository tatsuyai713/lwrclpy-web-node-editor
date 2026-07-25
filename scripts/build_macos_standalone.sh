#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
APP_NAME="lwrclpy-web-node-editor"
APP_TITLE="lwrclpy Web Node Editor"
BACKEND_NAME="$APP_NAME-server"
MAC_CODESIGN_IDENTITY="${MAC_CODESIGN_IDENTITY:--}"
MAC_CODESIGN_ENTITLEMENTS="${MAC_CODESIGN_ENTITLEMENTS:-}"
LWRCL_PREFIX="${LWRCL_PREFIX:-}"
CPP_DEP_PREFIXES="${CPP_DEP_PREFIXES:-}"


is_macho_file() {
  local _path="$1"
  file -b "$_path" 2>/dev/null | grep -qE 'Mach-O'
}


sign_macos_target() {
  local target_dir="$1"
  local verify_target="$2"
  local identity="$3"
  local entitlements="$4"

  if [[ -z "$identity" ]]; then
    echo "Skipping codesign: MAC_CODESIGN_IDENTITY is empty." >&2
    echo "To sign, export MAC_CODESIGN_IDENTITY='Developer ID Application: ...' and rebuild." >&2
    return 0
  fi
  if ! command -v codesign >/dev/null 2>&1; then
    echo "codesign command not found. Install Xcode command line tools." >&2
    return 3
  fi

  local sign_args=(--force --sign "$identity")
  if [[ "$identity" != "-" ]]; then
    sign_args+=(--timestamp --options runtime)
  fi
  if [[ -n "$entitlements" ]]; then
    if [[ ! -f "$entitlements" ]]; then
      echo "Entitlements file not found: $entitlements" >&2
      return 4
    fi
    sign_args+=(--entitlements "$entitlements")
  fi

  echo "Signing Mach-O files in: $target_dir"
  while IFS= read -r candidate; do
    if is_macho_file "$candidate"; then
      codesign "${sign_args[@]}" "$candidate"
    fi
  done < <(find "$target_dir" -type f -print | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-)

  # Sign the top-level target last so its signature reflects signed dependencies.
  if [[ -e "$verify_target" ]]; then
    codesign "${sign_args[@]}" "$verify_target"
  fi

  echo "Verifying signature..."
  codesign --verify --deep --strict --verbose=2 "$verify_target"
  spctl -a -vv "$verify_target" || true
}


create_macos_app_bundle() {
  local dist_dir="$1"
  local app_bundle="$2"
  local exe_name="$3"
  local app_title="$4"

  rm -rf "$app_bundle"
  mkdir -p "$app_bundle/Contents/MacOS" "$app_bundle/Contents/Resources"
  cp -R "$dist_dir" "$app_bundle/Contents/Resources/$exe_name"

  local launcher_c="$app_bundle/Contents/MacOS/${exe_name}_launcher.c"
  cat > "$launcher_c" <<EOF
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable_path[PATH_MAX];
    uint32_t size = sizeof(executable_path);
    if (_NSGetExecutablePath(executable_path, &size) != 0) {
        fprintf(stderr, "Executable path buffer too small\\n");
        return 1;
    }
    char real_path[PATH_MAX];
    if (realpath(executable_path, real_path) == NULL) {
        perror("realpath");
        return 1;
    }
    char *last_slash = strrchr(real_path, '/');
    if (last_slash == NULL) {
        fprintf(stderr, "Invalid executable path\\n");
        return 1;
    }
    *last_slash = '\\0';
    char target[PATH_MAX];
    if (snprintf(target, sizeof(target), "%s/../Resources/$exe_name/$exe_name", real_path) >= (int)sizeof(target)) {
        fprintf(stderr, "Target path too long\\n");
        return 1;
    }
    char **child_argv = calloc((size_t)argc + 1, sizeof(char *));
    if (child_argv == NULL) {
        perror("calloc");
        return 1;
    }
    child_argv[0] = target;
    for (int i = 1; i < argc; ++i) {
        child_argv[i] = argv[i];
    }
    child_argv[argc] = NULL;
    execv(target, child_argv);
    perror("execv");
    return 1;
}
EOF
  cc "$launcher_c" -o "$app_bundle/Contents/MacOS/$exe_name"
  rm -f "$launcher_c"
  chmod +x "$app_bundle/Contents/MacOS/$exe_name"

  cat > "$app_bundle/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>$app_title</string>
  <key>CFBundleExecutable</key>
  <string>$exe_name</string>
  <key>CFBundleIdentifier</key>
  <string>com.tatsuyai.lwrclpy-web-node-editor</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$app_title</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF
}


copy_cpp_dependency_prefixes() {
  local target="$1"
  rm -rf "$target"
  mkdir -p "$target"

  local candidates=()
  if [[ -n "$LWRCL_PREFIX" ]]; then
    candidates+=("$LWRCL_PREFIX")
  fi
  if [[ -n "$CPP_DEP_PREFIXES" ]]; then
    local extra_prefixes=()
    IFS=: read -r -a extra_prefixes <<< "$CPP_DEP_PREFIXES"
    candidates+=("${extra_prefixes[@]}")
  fi
  candidates+=(
    "/opt/fast-dds-libs"
    "/opt/fast-dds"
  )

  local copied=0
  local seen=":"
  for prefix in "${candidates[@]}"; do
    [[ -n "$prefix" && -d "$prefix" ]] || continue
    local resolved
    resolved="$(cd "$prefix" && pwd)"
    case "$seen" in
      *":$resolved:"*) continue ;;
    esac
    seen="${seen}${resolved}:"

    echo "Bundling C++ dependency prefix: $resolved"
    for subdir in include lib share bin tools; do
      if [[ -e "$resolved/$subdir" ]]; then
        mkdir -p "$target/$subdir"
        ditto "$resolved/$subdir" "$target/$subdir"
        copied=1
      fi
    done
  done

  if [[ "$copied" -eq 0 ]]; then
    echo "WARNING: no C++ dependency prefixes found. C++ custom nodes will require external lwrcl/FastDDS setup." >&2
    return 0
  fi
  if [[ ! -f "$target/include/lwrcl.hpp" || ! -e "$target/lib/liblwrcl.dylib" ]]; then
    echo "WARNING: bundled C++ prefix does not contain lwrcl.hpp and liblwrcl.dylib." >&2
    echo "Set LWRCL_PREFIX=/path/to/lwrcl/install or CPP_DEP_PREFIXES=/path/one:/path/two and rebuild." >&2
  fi
}

if [[ -z "$PYTHON_BIN" ]]; then
  for _candidate in \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/venv/bin/python" \
    "/opt/homebrew/bin/python3.13" \
    "/usr/local/bin/python3.13" \
    "$(command -v python3 2>/dev/null || true)"; do
    if [[ -n "$_candidate" && -x "$_candidate" ]]; then
      PYTHON_BIN="$_candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found. Set PYTHON_BIN=/path/to/python and retry." >&2
  exit 2
fi

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install --prefer-binary --progress-bar off -r requirements.txt pyinstaller

UV_BIN="$(which uv 2>/dev/null || true)"
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  for _candidate in \
    "$(dirname "$PYTHON_BIN")/uv" \
    "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv" \
    "/opt/homebrew/bin/uv" \
    "/usr/local/bin/uv"; do
    if [[ -x "$_candidate" ]]; then
      UV_BIN="$_candidate"
      break
    fi
  done
fi
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "WARNING: uv not found - lwrclpy auto-update will require uv to be on PATH at runtime." >&2
else
  echo "Bundling uv from: $UV_BIN"
fi

if command -v chflags >/dev/null 2>&1; then
  for cleanup_path in build dist .build_cpp_deps; do
    if [[ -e "$cleanup_path" ]]; then
      chflags -R nouchg,noschg,nohidden "$cleanup_path" 2>/dev/null || true
    fi
  done
fi
rm -rf build dist dist-electron .build_cpp_deps

CPP_BUNDLE_PREFIX="$ROOT_DIR/.build_cpp_deps/lwrcl_cpp"
copy_cpp_dependency_prefixes "$CPP_BUNDLE_PREFIX"

UV_EXTRA_ARGS=()
if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
  UV_EXTRA_ARGS+=("--add-binary" "${UV_BIN}:.")
fi

FASTDDS_EXTRA_ARGS=()
VENDORED_FASTDDS_SO="$("$PYTHON_BIN" - <<'PY'
from pathlib import Path
import sysconfig

purelib = Path(sysconfig.get_paths()["purelib"])
candidate = purelib / "lwrclpy" / "_vendor" / "fastdds" / "_fastdds_python.so"
print(candidate if candidate.exists() else "")
PY
)"
if [[ -n "$VENDORED_FASTDDS_SO" && -f "$VENDORED_FASTDDS_SO" ]]; then
  FASTDDS_EXTRA_ARGS+=("--add-binary" "${VENDORED_FASTDDS_SO}:lwrclpy/_vendor/fastdds")
else
  echo "WARNING: vendored lwrclpy FastDDS extension was not found; bundled lwrclpy may fail to import fastdds." >&2
fi

CPP_EXTRA_ARGS=()
if [[ -d "$CPP_BUNDLE_PREFIX" && -n "$(find "$CPP_BUNDLE_PREFIX" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  CPP_EXTRA_ARGS+=("--add-data" "${CPP_BUNDLE_PREFIX}:lwrcl_cpp")
fi

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --name "$BACKEND_NAME"
  --onedir
  --collect-all lwrclpy
  --collect-all rclpy
  --collect-all fastdds
  --collect-all mcap
  --collect-all mcap_ros2
  --collect-all cmake
  --hidden-import cv2
  --hidden-import yaml
  --exclude-module webview
  --exclude-module pythonnet
  --exclude-module clr
  --exclude-module PySide6
  --add-data "lwrclpy_web_node_editor/static:lwrclpy_web_node_editor/static"
  --add-data "lwrclpy_web_node_editor/node_worker.py:lwrclpy_web_node_editor"
  --add-data "lwrclpy_web_node_editor/video_dds_worker.py:lwrclpy_web_node_editor"
  --add-data "lwrclpy_web_node_editor/dds_tap_worker.py:lwrclpy_web_node_editor"
  --add-data "lwrclpy_web_node_editor/builtin_source_worker.py:lwrclpy_web_node_editor"
  --add-data "scripts/install_lwrclpy.py:scripts"
  --add-data "resources/fastdds.xml:."
  --add-data ".app_settings/custom_nodes:custom_nodes"
  --add-data "samples:samples"
)
if [[ ${#UV_EXTRA_ARGS[@]} -gt 0 ]]; then
  PYINSTALLER_ARGS+=("${UV_EXTRA_ARGS[@]}")
fi
if [[ ${#FASTDDS_EXTRA_ARGS[@]} -gt 0 ]]; then
  PYINSTALLER_ARGS+=("${FASTDDS_EXTRA_ARGS[@]}")
fi
if [[ ${#CPP_EXTRA_ARGS[@]} -gt 0 ]]; then
  PYINSTALLER_ARGS+=("${CPP_EXTRA_ARGS[@]}")
fi

"$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}" main.py

BACKEND_DIR="$ROOT_DIR/dist/$BACKEND_NAME"
BACKEND_EXE="$BACKEND_DIR/$BACKEND_NAME"
BACKEND_ZIP="$ROOT_DIR/dist/$BACKEND_NAME.zip"
APP_BUNDLE="$ROOT_DIR/dist/$APP_NAME.app"

"$BACKEND_EXE" --server-import-check
ditto -c -k --sequesterRsrc --keepParent "$BACKEND_DIR" "$BACKEND_ZIP"

ELECTRON_VERSION_PINNED="${ELECTRON_VERSION:-36.9.5}"
ELECTRON_PACKAGER_VERSION_PINNED="${ELECTRON_PACKAGER_VERSION:-20.0.3}"
npm install --prefix electron --no-save "electron@${ELECTRON_VERSION_PINNED}" "@electron/packager@${ELECTRON_PACKAGER_VERSION_PINNED}" extract-zip@latest
if ! node -e "require('./electron/node_modules/electron')" >/dev/null 2>&1; then
  echo "Electron binary is missing; running Electron installer explicitly."
  env -u ELECTRON_RUN_AS_NODE node electron/node_modules/electron/install.js
fi
if ! node -e "require('./electron/node_modules/electron')" >/dev/null 2>&1; then
  echo "Electron installer did not produce a runnable binary; downloading Electron directly."
  env -u ELECTRON_RUN_AS_NODE node --input-type=module <<'JS'
import { createRequire } from 'module';
import childProcess from 'child_process';
import fs from 'fs';
import path from 'path';
const require = createRequire(import.meta.url);
const { downloadArtifact } = require('./electron/node_modules/electron/node_modules/@electron/get');
const electronDir = path.resolve('electron/node_modules/electron');
const version = require('./electron/node_modules/electron/package.json').version;
try {
  fs.rmSync(path.join(electronDir, 'dist'), { recursive: true, force: true });
  fs.rmSync(path.join(electronDir, 'path.txt'), { force: true });
  fs.mkdirSync(path.join(electronDir, 'dist'), { recursive: true });
  const zipPath = await downloadArtifact({
    version,
    artifactName: 'electron',
    platform: 'darwin',
    arch: 'arm64',
  });
  childProcess.execFileSync('ditto', ['-x', '-k', zipPath, path.join(electronDir, 'dist')], { stdio: 'inherit' });
  fs.writeFileSync(path.join(electronDir, 'path.txt'), 'Electron.app/Contents/MacOS/Electron');
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
JS
fi
node -e "require('./electron/node_modules/electron')" >/dev/null
ELECTRON_VERSION="$(node -p "require('./electron/node_modules/electron/package.json').version")"
electron/node_modules/.bin/electron-packager \
  electron \
  "$APP_NAME" \
  --platform=darwin \
  --arch=arm64 \
  --electron-version="$ELECTRON_VERSION" \
  --out=dist-electron \
  --overwrite \
  --asar=false \
  --executable-name="$APP_NAME" \
  --extra-resource="$BACKEND_ZIP"

rm -rf "$APP_BUNDLE"
mv "$ROOT_DIR/dist-electron/$APP_NAME-darwin-arm64/$APP_NAME.app" "$APP_BUNDLE"
rm -rf "$ROOT_DIR/dist-electron"

sign_macos_target "$APP_BUNDLE" "$APP_BUNDLE" "$MAC_CODESIGN_IDENTITY" "$MAC_CODESIGN_ENTITLEMENTS"

echo ""
echo "Build complete: $BACKEND_DIR"
echo "App bundle: $APP_BUNDLE"
echo "Open macOS app: open $APP_BUNDLE"
echo "Run server mode: $BACKEND_EXE --server --host 127.0.0.1 --port 8765"
