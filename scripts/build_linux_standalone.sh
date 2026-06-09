#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"
APP_NAME="lwrclpy-web-node-editor"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN=/path/to/python and retry." >&2
  exit 2
fi

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install pyinstaller pyside6 opencv-python-headless pillow

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
  --hidden-import cv2 \
  "${UV_EXTRA_ARGS[@]}" \
  --add-data "lwrclpy_web_node_editor/static:lwrclpy_web_node_editor/static" \
  --add-data "lwrclpy_web_node_editor/node_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/video_dds_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/dds_tap_worker.py:lwrclpy_web_node_editor" \
  --add-data "lwrclpy_web_node_editor/builtin_source_worker.py:lwrclpy_web_node_editor" \
  --add-data "scripts/install_lwrclpy.py:scripts" \
  main.py

echo ""
echo "Build complete: $ROOT_DIR/dist/$APP_NAME"
echo "Run desktop app: $ROOT_DIR/dist/$APP_NAME/$APP_NAME"
echo "Run server mode: $ROOT_DIR/dist/$APP_NAME/$APP_NAME --server --host 127.0.0.1 --port 8765"
