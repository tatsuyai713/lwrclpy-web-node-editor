#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
APP_NAME="lwrclpy-web-node-editor"
APP_TITLE="lwrclpy Web Node Editor"
MAC_CODESIGN_IDENTITY="${MAC_CODESIGN_IDENTITY:--}"
MAC_CODESIGN_ENTITLEMENTS="${MAC_CODESIGN_ENTITLEMENTS:-}"


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
  while IFS= read -r -d '' candidate; do
    if is_macho_file "$candidate"; then
      codesign "${sign_args[@]}" "$candidate"
    fi
  done < <(find "$target_dir" -type f -print0)

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

rm -rf build dist

UV_EXTRA_ARGS=()
if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
  UV_EXTRA_ARGS+=("--add-binary" "${UV_BIN}:.")
fi

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --name "$APP_NAME" \
  --onedir \
  --collect-all lwrclpy \
  --collect-all rclpy \
  --collect-all fastdds \
  --collect-all webview \
  --hidden-import cv2 \
  --hidden-import webview.platforms.cocoa \
  --hidden-import objc \
  --hidden-import Cocoa \
  --hidden-import WebKit \
  "${UV_EXTRA_ARGS[@]}" \
  --add-data "lwrclpy_web_node_editor/static:lwrclpy_web_node_editor/static" \
  --add-data "lwrclpy_web_node_editor/node_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/video_dds_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/dds_tap_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/builtin_source_worker.py:lwrclpy_web_node_editor" \
  --add-data "scripts/install_lwrclpy.py:scripts" \
  main.py

DIST_DIR="$ROOT_DIR/dist/$APP_NAME"
APP_EXE="$DIST_DIR/$APP_NAME"
APP_BUNDLE="$ROOT_DIR/dist/$APP_NAME.app"

create_macos_app_bundle "$DIST_DIR" "$APP_BUNDLE" "$APP_NAME" "$APP_TITLE"

sign_macos_target "$DIST_DIR" "$APP_EXE" "$MAC_CODESIGN_IDENTITY" "$MAC_CODESIGN_ENTITLEMENTS"
sign_macos_target "$APP_BUNDLE" "$APP_BUNDLE" "$MAC_CODESIGN_IDENTITY" "$MAC_CODESIGN_ENTITLEMENTS"

echo ""
echo "Build complete: $DIST_DIR"
echo "App bundle: $APP_BUNDLE"
echo "Run desktop app: $APP_EXE"
echo "Open macOS app: open $APP_BUNDLE"
echo "Run server mode: $APP_EXE --server --host 127.0.0.1 --port 8765"
