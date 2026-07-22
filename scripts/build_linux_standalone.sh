#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="lwrclpy-web-node-editor"
APP_TITLE="lwrclpy Web Node Editor"
BACKEND_NAME="$APP_NAME-server"
APPIMAGE_TOOL_URL_X86_64="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGE_TOOL_URL_AARCH64="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-aarch64.AppImage"

normalize_arch() {
  case "${1:-}" in
    x86_64|amd64)
      echo "x86_64"
      ;;
    aarch64|arm64|armv8*|arm64v8)
      echo "aarch64"
      ;;
    *)
      echo "${1:-unknown}"
      ;;
  esac
}

append_unique() {
  local value="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "$item" == "$value" ]]; then
      return 1
    fi
  done
  return 0
}

normalize_appimage_elf_header() {
  local image_path="$1"
  [[ -f "$image_path" ]] || return 0
  "$PYTHON_BIN" - "$image_path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = bytearray(path.read_bytes())
if len(data) >= 11 and data[:8] == b"\x7fELF\x02\x01\x01\x00" and data[8:11] == b"AI\x02":
    # AppImage runtimes store their type marker in ELF e_ident padding.
    # Some Docker Desktop/Rosetta Linux containers reject that as an invalid
    # ABI version before the runtime can start, so normalize the padding.
    data[8:11] = b"\x00\x00\x00"
    path.write_bytes(data)
PY
}

download_appimagetool() {
  local arch="$1"
  local tool_url=""
  case "$arch" in
    x86_64)
      tool_url="$APPIMAGE_TOOL_URL_X86_64"
      ;;
    aarch64)
      tool_url="$APPIMAGE_TOOL_URL_AARCH64"
      ;;
    *)
      return 1
      ;;
  esac
  local tool_dir="$ROOT_DIR/.appimage-tools"
  local tool_path="$tool_dir/appimagetool-$arch.AppImage"
  mkdir -p "$tool_dir"
  if [[ ! -x "$tool_path" ]]; then
    rm -f "$tool_path"
    if command -v curl >/dev/null 2>&1; then
      curl -L "$tool_url" -o "$tool_path" && chmod +x "$tool_path" || return 1
    elif command -v wget >/dev/null 2>&1; then
      wget -O "$tool_path" "$tool_url" && chmod +x "$tool_path" || return 1
    else
      return 1
    fi
  fi
  normalize_appimage_elf_header "$tool_path" || return 1
  chmod +x "$tool_path"
  echo "$tool_path"
}

detect_output_arch() {
  local exe_path="$ROOT_DIR/dist/$APP_NAME/$APP_NAME"
  local description=""
  if command -v file >/dev/null 2>&1 && [[ -f "$exe_path" ]]; then
    description="$(file -b "$exe_path" 2>/dev/null || true)"
    case "$description" in
      *x86-64*|*x86_64*)
        echo "x86_64"
        return 0
        ;;
      *aarch64*|*ARM\ aarch64*|*ARM64*)
        echo "aarch64"
        return 0
        ;;
    esac
  fi
  normalize_arch "$(uname -m)"
}

if [[ -z "${PYTHON_BIN:-}" ]]; then
  for _candidate in \
    "${VIRTUAL_ENV:-}/bin/python" \
    "$ROOT_DIR/scripts/venv_linux/bin/python" \
    "$ROOT_DIR/scripts/.venv_linux/bin/python" \
    "$ROOT_DIR/venv_linux/bin/python" \
    "$ROOT_DIR/.venv_linux/bin/python" \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/venv/bin/python" \
    "$(command -v python3 2>/dev/null || true)" \
    "$(command -v python 2>/dev/null || true)"; do
    if [[ -n "$_candidate" && -x "$_candidate" ]]; then
      PYTHON_BIN="$_candidate"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: ${PYTHON_BIN:-<auto-detect failed>}" >&2
  echo "Activate a venv or set PYTHON_BIN=/path/to/python and retry." >&2
  exit 2
fi

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install --prefer-binary --progress-bar off -r requirements.txt pyinstaller

# Locate the uv binary to bundle with the frozen app so auto-update works
# even when uv is not on the user's PATH at runtime.
UV_BIN="$(which uv 2>/dev/null || true)"
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  # Check common locations: venv sibling bin, ~/.local/bin, ~/.cargo/bin
  for _candidate in \
    "$(dirname "$PYTHON_BIN")/uv" \
    "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv"; do
    if [[ -x "$_candidate" ]]; then
      UV_BIN="$_candidate"
      break
    fi
  done
fi
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "WARNING: uv not found – lwrclpy auto-update will require uv to be on PATH at runtime." >&2
else
  echo "Bundling uv from: $UV_BIN"
fi

rm -rf build dist dist-electron

UV_EXTRA_ARGS=()
if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
  UV_EXTRA_ARGS+=("--add-binary" "${UV_BIN}:.")
fi

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --name "$BACKEND_NAME" \
  --onedir \
  --collect-all lwrclpy \
  --collect-all rclpy \
  --collect-all fastdds \
  --collect-all mcap \
  --collect-all mcap_ros2 \
  --hidden-import cv2 \
  --hidden-import yaml \
  --hidden-import asyncio \
  --hidden-import atexit \
  --hidden-import collections \
  --hidden-import concurrent \
  --hidden-import concurrent.futures \
  --hidden-import copy \
  --hidden-import enum \
  --hidden-import functools \
  --hidden-import importlib \
  --hidden-import importlib.util \
  --hidden-import inspect \
  --hidden-import logging \
  --hidden-import queue \
  --hidden-import threading \
  --hidden-import time \
  --hidden-import types \
  --hidden-import typing \
  --hidden-import uuid \
  --hidden-import weakref \
  --exclude-module webview \
  --exclude-module pythonnet \
  --exclude-module clr \
  --exclude-module qtpy \
  --exclude-module gi \
  --exclude-module PyQt5 \
  --exclude-module PyQt6 \
  --exclude-module PySide2 \
  --exclude-module PySide6 \
  "${UV_EXTRA_ARGS[@]}" \
  --add-data "lwrclpy_web_node_editor/static:lwrclpy_web_node_editor/static" \
  --add-data "lwrclpy_web_node_editor/node_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/video_dds_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/dds_tap_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/builtin_source_worker.py:lwrclpy_web_node_editor" \
  --add-data "scripts/install_lwrclpy.py:scripts" \
  --add-data "resources/fastdds.xml:." \
  --add-data ".app_settings/custom_nodes:custom_nodes" \
  --add-data "samples:samples" \
  main.py

"$ROOT_DIR/dist/$BACKEND_NAME/$BACKEND_NAME" --server-import-check

APP_ARCH="$(detect_output_arch)"
case "$APP_ARCH" in
  x86_64)
    ELECTRON_ARCH="x64"
    ;;
  aarch64)
    ELECTRON_ARCH="arm64"
    ;;
  *)
    ELECTRON_ARCH="$(uname -m)"
    ;;
esac
npm install --prefix electron --no-save electron@latest @electron/packager@latest
electron/node_modules/.bin/electron-packager \
  electron \
  "$APP_NAME" \
  --platform=linux \
  --arch="$ELECTRON_ARCH" \
  --out=dist-electron \
  --overwrite \
  --asar=false \
  --executable-name="$APP_NAME" \
  --extra-resource="dist/$BACKEND_NAME"

rm -rf "$ROOT_DIR/dist/$APP_NAME"
mv "$ROOT_DIR/dist-electron/$APP_NAME-linux-$ELECTRON_ARCH" "$ROOT_DIR/dist/$APP_NAME"
rm -rf "$ROOT_DIR/dist-electron"

APP_DIR="$ROOT_DIR/dist/$APP_NAME.AppDir"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin" "$APP_DIR/usr/share/applications" "$APP_DIR/usr/share/icons/hicolor/256x256/apps"
cp -R "$ROOT_DIR/dist/$APP_NAME" "$APP_DIR/usr/bin/$APP_NAME"
cat > "$APP_DIR/AppRun" <<EOF
#!/usr/bin/env bash
set -euo pipefail
HERE="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export UV_LINK_MODE="\${UV_LINK_MODE:-copy}"
exec "\$HERE/usr/bin/$APP_NAME/$APP_NAME" "\$@"
EOF
chmod +x "$APP_DIR/AppRun"
cat > "$APP_DIR/$APP_NAME.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_TITLE
Comment=Visual ROS 2/lwrclpy node editor
Exec=AppRun
Icon=$APP_NAME
Terminal=false
Categories=Development;
StartupNotify=true
EOF
cp "$APP_DIR/$APP_NAME.desktop" "$APP_DIR/usr/share/applications/$APP_NAME.desktop"

ICON_FILE="$APP_DIR/$APP_NAME.png"
if command -v convert >/dev/null 2>&1; then
  convert -size 256x256 xc:'#1f6feb' \
    -fill white -gravity center -pointsize 72 -font DejaVu-Sans-Bold \
    -annotate 0 'IPN' "$ICON_FILE" || true
fi
if [[ ! -f "$ICON_FILE" ]]; then
  "$PYTHON_BIN" - <<PY
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
path = Path("$ICON_FILE")
img = Image.new("RGB", (256, 256), "#1f6feb")
draw = ImageDraw.Draw(img)
text = "IPN"
try:
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
except Exception:
    font = ImageFont.load_default()
bbox = draw.textbbox((0, 0), text, font=font)
draw.text(((256 - (bbox[2] - bbox[0])) / 2, (256 - (bbox[3] - bbox[1])) / 2 - 8), text, fill="white", font=font)
img.save(path)
PY
fi
cp "$ICON_FILE" "$APP_DIR/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png"

APPIMAGE_TOOL="${APPIMAGE_TOOL:-}"
APPIMAGE_TOOL_CANDIDATES=()
if [[ -n "$APPIMAGE_TOOL" ]]; then
  APPIMAGE_TOOL_CANDIDATES+=("$APPIMAGE_TOOL")
fi
SYSTEM_APPIMAGE_TOOL="$(command -v appimagetool 2>/dev/null || true)"
if [[ -n "$SYSTEM_APPIMAGE_TOOL" ]]; then
  APPIMAGE_TOOL_CANDIDATES+=("$SYSTEM_APPIMAGE_TOOL")
fi

ARCH_CANDIDATES=()
for _raw_arch in \
  "$(uname -m 2>/dev/null || true)" \
  "$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
import platform
print(platform.machine())
PY
)" \
  "$(dpkg --print-architecture 2>/dev/null || true)"; do
  _arch="$(normalize_arch "$_raw_arch")"
  if [[ "$_arch" != "unknown" ]] && append_unique "$_arch" "${ARCH_CANDIDATES[@]}"; then
    ARCH_CANDIDATES+=("$_arch")
  fi
done
for _arch in x86_64 aarch64; do
  if append_unique "$_arch" "${ARCH_CANDIDATES[@]}"; then
    ARCH_CANDIDATES+=("$_arch")
  fi
done
for _arch in "${ARCH_CANDIDATES[@]}"; do
  _tool="$(download_appimagetool "$_arch" 2>/dev/null || true)"
  if [[ -n "$_tool" ]]; then
    APPIMAGE_TOOL_CANDIDATES+=("$_tool")
  fi
done

APPIMAGE_PATH="$ROOT_DIR/dist/$APP_NAME-$APP_ARCH.AppImage"
APPIMAGE_BUILT=0
for _tool in "${APPIMAGE_TOOL_CANDIDATES[@]}"; do
  if [[ ! -x "$_tool" ]]; then
    continue
  fi
  echo "Trying appimagetool: $_tool"
  if APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$APP_ARCH" "$_tool" "$APP_DIR" "$APPIMAGE_PATH"; then
    normalize_appimage_elf_header "$APPIMAGE_PATH"
    chmod +x "$APPIMAGE_PATH"
    APPIMAGE_BUILT=1
    break
  fi
  echo "WARNING: appimagetool failed: $_tool" >&2
done
if [[ "$APPIMAGE_BUILT" != "1" ]]; then
  echo "WARNING: no usable appimagetool was found or downloaded. AppDir is still available: $APP_DIR" >&2
  APPIMAGE_PATH=""
else
  APPIMAGE_RUNTIME_PATH="$APPIMAGE_PATH.runtime"
  mv -f "$APPIMAGE_PATH" "$APPIMAGE_RUNTIME_PATH"
  cat > "$APPIMAGE_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
HERE="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export APPIMAGE_EXTRACT_AND_RUN="\${APPIMAGE_EXTRACT_AND_RUN:-1}"
exec "\$HERE/$(basename "$APPIMAGE_RUNTIME_PATH")" "\$@"
EOF
  chmod +x "$APPIMAGE_PATH"
fi

SELF_EXTRACTING_PATH="$ROOT_DIR/dist/$APP_NAME-$APP_ARCH.run"
PAYLOAD_PATH="$ROOT_DIR/dist/$APP_NAME-$APP_ARCH.AppDir.tar.gz"
rm -f "$SELF_EXTRACTING_PATH" "$PAYLOAD_PATH"
tar -C "$ROOT_DIR/dist" -czf "$PAYLOAD_PATH" "$APP_NAME.AppDir"
cat > "$SELF_EXTRACTING_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APP_NAME="$APP_NAME"
APP_ARCH="$APP_ARCH"
MARKER="__LWRCLPY_WEB_NODE_EDITOR_PAYLOAD_BELOW__"
SELF="\$(readlink -f "\${BASH_SOURCE[0]}")"
CACHE_ROOT="\${XDG_CACHE_HOME:-\$HOME/.cache}/\$APP_NAME"
PAYLOAD_HASH="\$(sha256sum "\$SELF" | awk '{print \$1}')"
RUN_DIR="\$CACHE_ROOT/\$APP_ARCH-\$PAYLOAD_HASH"
APP_RUN="\$RUN_DIR/$APP_NAME.AppDir/AppRun"
if [[ ! -x "\$APP_RUN" ]]; then
  rm -rf "\$RUN_DIR"
  mkdir -p "\$RUN_DIR"
  PAYLOAD_LINE="\$(awk -v marker="\$MARKER" '\$0 == marker { print NR + 1; exit }' "\$SELF")"
  if [[ -z "\$PAYLOAD_LINE" ]]; then
    echo "Payload marker not found in \$SELF" >&2
    exit 2
  fi
  tail -n +"\$PAYLOAD_LINE" "\$SELF" | tar -xz -C "\$RUN_DIR"
fi
export UV_LINK_MODE="\${UV_LINK_MODE:-copy}"
exec "\$APP_RUN" "\$@"
exit 127
\$MARKER
EOF
cat "$PAYLOAD_PATH" >> "$SELF_EXTRACTING_PATH"
chmod +x "$SELF_EXTRACTING_PATH"
rm -f "$PAYLOAD_PATH"

DESKTOP_FILE="$ROOT_DIR/dist/$APP_NAME.desktop"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_TITLE
Comment=Visual ROS 2/lwrclpy node editor
Exec=$ROOT_DIR/dist/$APP_NAME/$APP_NAME
Icon=$APP_DIR/$APP_NAME.png
Terminal=false
Categories=Development;
StartupNotify=true
EOF
chmod +x "$DESKTOP_FILE"

echo ""
echo "Build complete: $ROOT_DIR/dist/$APP_NAME"
echo "Run desktop app: $ROOT_DIR/dist/$APP_NAME/$APP_NAME"
echo "Double-click launcher: $DESKTOP_FILE"
echo "AppDir launcher: $APP_DIR/AppRun"
if [[ -n "$APPIMAGE_PATH" ]]; then
  echo "FUSE-free AppImage launcher: $APPIMAGE_PATH"
  echo "AppImage runtime payload: $APPIMAGE_PATH.runtime"
fi
echo "Single-file self-extracting app: $SELF_EXTRACTING_PATH"
echo "Run server mode: $ROOT_DIR/dist/$APP_NAME/$APP_NAME --server --host 127.0.0.1 --port 8765"
