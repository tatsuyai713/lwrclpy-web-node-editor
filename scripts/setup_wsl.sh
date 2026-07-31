#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_CPP=0

usage() {
  cat <<'EOF'
Usage: bash scripts/setup_wsl.sh [--with-cpp]

Prepare lwrclpy Web Node Editor on Ubuntu running under WSL.

Options:
  --with-cpp  Also build FastDDS, message types, and lwrcl for C++ nodes.
  -h, --help  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-cpp)
      WITH_CPP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" || -n "${WSL_INTEROP:-}" ]] \
    || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

if ! is_wsl; then
  echo "This script must be run inside WSL." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script currently supports Ubuntu/Debian-based WSL distributions." >&2
  exit 1
fi

echo "Installing WSL system packages..."
sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  python3-tk \
  python3-venv

if [[ "$WITH_CPP" -eq 1 ]]; then
  sudo apt-get install -y \
    autoconf \
    automake \
    bison \
    build-essential \
    cmake \
    flex \
    libasio-dev \
    libssl-dev \
    libtool \
    pkg-config \
    tar \
    unzip \
    wget
fi

UV_BIN="$(command -v uv 2>/dev/null || true)"
PYTHON_BIN="$(command -v python3.13 2>/dev/null || true)"

if [[ -z "$PYTHON_BIN" ]] && apt-cache show python3.13 >/dev/null 2>&1; then
  echo "Installing Python 3.13 from apt..."
  python_packages=(python3.13 python3.13-venv)
  if apt-cache show python3.13-tk >/dev/null 2>&1; then
    python_packages+=(python3.13-tk)
  fi
  sudo apt-get install -y "${python_packages[@]}"
  PYTHON_BIN="$(command -v python3.13 2>/dev/null || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -z "$UV_BIN" ]]; then
    echo "Python 3.13 is not available from apt; installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV_BIN="${HOME}/.local/bin/uv"
  fi
  if [[ ! -x "$UV_BIN" ]]; then
    echo "uv installation failed: $UV_BIN was not found." >&2
    exit 1
  fi
  "$UV_BIN" python install 3.13
  PYTHON_BIN="$("$UV_BIN" python find 3.13)"
fi

echo "Using Python: $PYTHON_BIN"
cd "$ROOT_DIR"

if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
  "$UV_BIN" venv --python "$PYTHON_BIN" .venv
else
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/.venv/bin/activate"
python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts/install_lwrclpy.py

if command -v powershell.exe >/dev/null 2>&1; then
  echo "Windows file picker integration: available"
else
  echo "WARNING: powershell.exe is not available from WSL."
  echo "Enable WSL interoperability to use Windows file selection dialogs."
fi

if python -c "import tkinter" >/dev/null 2>&1; then
  echo "Linux tkinter fallback: available"
else
  echo "Linux tkinter fallback: unavailable (Windows file picker integration will be used)."
fi

if [[ "$WITH_CPP" -eq 1 ]]; then
  echo "Building FastDDS and lwrcl for C++ nodes..."
  bash scripts/setup_lwrcl_cpp_env.sh \
    --prefix "$ROOT_DIR/.local/fast-dds-libs" \
    --dds-prefix "$ROOT_DIR/.local/fast-dds"
fi

cat <<EOF

WSL setup complete.

Run the editor:
  cd "$ROOT_DIR"
  source .venv/bin/activate
EOF

if [[ "$WITH_CPP" -eq 1 ]]; then
  cat <<EOF
  export LWRCL_PREFIX="$ROOT_DIR/.local/fast-dds-libs"
  export DDS_PREFIX="$ROOT_DIR/.local/fast-dds"
EOF
fi

cat <<'EOF'
  python main.py --host 127.0.0.1 --port 8765
EOF
