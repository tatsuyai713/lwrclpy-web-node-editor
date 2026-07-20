#!/usr/bin/env bash
set -euo pipefail

LWRCL_REPO_URL="${LWRCL_REPO_URL:-https://github.com/tatsuyai713/lwrcl.git}"
LWRCL_SOURCE_DIR="${LWRCL_SOURCE_DIR:-$HOME/.cache/lwrclpy-web-node-editor/lwrcl}"
LWRCL_REF="${LWRCL_REF:-latest}"
LWRCL_BACKEND="${LWRCL_BACKEND:-fastdds}"
LWRCL_PREFIX="${LWRCL_PREFIX:-}"
DDS_PREFIX="${DDS_PREFIX:-}"

INSTALL_FASTDDS=1
BUILD_LIBRARIES=1
BUILD_DATA_TYPES=1
BUILD_LWRCL=1
INSTALL_ACTION="install"

usage() {
  cat <<'EOF'
Usage: scripts/setup_lwrcl_cpp_env.sh [options]

Fetch, build, and install the C++ lwrcl environment used by C++ custom nodes.

Options:
  --repo URL             lwrcl git repository URL.
  --source-dir PATH      Checkout/build directory. Default: ~/.cache/lwrclpy-web-node-editor/lwrcl
  --ref REF              Git ref to build. Default: latest
                         "latest" means GitHub latest release tag, then newest git tag, then default branch.
  --backend NAME         DDS backend. Default: fastdds
  --prefix PATH          lwrcl install prefix. Default depends on backend.
  --dds-prefix PATH      DDS install prefix. Default depends on backend.
  --no-install           Build only, do not run install targets.
  --skip-fastdds         Do not run scripts/install_fast_dds.sh.
  --skip-libraries       Do not run build_libraries.sh.
  --skip-data-types      Do not run build_data_types.sh.
  --skip-lwrcl           Do not run build_lwrcl.sh.
  -h, --help             Show this help.

Environment variables with the same names are also supported:
  LWRCL_REPO_URL, LWRCL_SOURCE_DIR, LWRCL_REF, LWRCL_BACKEND, LWRCL_PREFIX, DDS_PREFIX
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      LWRCL_REPO_URL="${2:?missing value for --repo}"
      shift 2
      ;;
    --source-dir)
      LWRCL_SOURCE_DIR="${2:?missing value for --source-dir}"
      shift 2
      ;;
    --ref)
      LWRCL_REF="${2:?missing value for --ref}"
      shift 2
      ;;
    --backend)
      LWRCL_BACKEND="${2:?missing value for --backend}"
      shift 2
      ;;
    --prefix)
      LWRCL_PREFIX="${2:?missing value for --prefix}"
      shift 2
      ;;
    --dds-prefix)
      DDS_PREFIX="${2:?missing value for --dds-prefix}"
      shift 2
      ;;
    --no-install)
      INSTALL_ACTION=""
      shift
      ;;
    --skip-fastdds)
      INSTALL_FASTDDS=0
      shift
      ;;
    --skip-libraries)
      BUILD_LIBRARIES=0
      shift
      ;;
    --skip-data-types)
      BUILD_DATA_TYPES=0
      shift
      ;;
    --skip-lwrcl)
      BUILD_LWRCL=0
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

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 127
  fi
}

default_lwrcl_prefix() {
  case "$LWRCL_BACKEND" in
    fastdds) echo "/opt/fast-dds-libs" ;;
    cyclonedds) echo "/opt/cyclonedds-libs" ;;
    vsomeip) echo "/opt/vsomeip-libs" ;;
    adaptive-autosar) echo "/opt/autosar-ap-libs" ;;
    *)
      echo "Unknown backend: $LWRCL_BACKEND" >&2
      exit 2
      ;;
  esac
}

default_dds_prefix() {
  case "$LWRCL_BACKEND" in
    fastdds) echo "/opt/fast-dds" ;;
    cyclonedds) echo "/opt/cyclonedds" ;;
    vsomeip) echo "/opt/vsomeip" ;;
    adaptive-autosar) echo "/opt/cyclonedds" ;;
    *)
      echo "Unknown backend: $LWRCL_BACKEND" >&2
      exit 2
      ;;
  esac
}

github_repo_slug() {
  local url="$1"
  url="${url#https://github.com/}"
  url="${url#git@github.com:}"
  url="${url%.git}"
  printf '%s\n' "$url"
}

github_latest_release_tag() {
  local slug
  slug="$(github_repo_slug "$LWRCL_REPO_URL")"
  [[ "$slug" == */* ]] || return 1
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "https://api.github.com/repos/${slug}/releases/latest" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$slug" <<'PY'
import json
import sys
import urllib.request

slug = sys.argv[1]
request = urllib.request.Request(
    f"https://api.github.com/repos/{slug}/releases/latest",
    headers={"Accept": "application/vnd.github+json", "User-Agent": "lwrclpy-web-node-editor"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    print(json.load(response).get("tag_name", ""))
PY
  else
    return 1
  fi
}

git_latest_tag() {
  git ls-remote --tags --refs "$LWRCL_REPO_URL" \
    | awk '{print $2}' \
    | sed 's#refs/tags/##' \
    | sort -V \
    | tail -1
}

resolve_lwrcl_ref() {
  if [[ "$LWRCL_REF" != "latest" ]]; then
    printf '%s\n' "$LWRCL_REF"
    return 0
  fi

  local ref=""
  ref="$(github_latest_release_tag 2>/dev/null || true)"
  if [[ -z "$ref" ]]; then
    ref="$(git_latest_tag 2>/dev/null || true)"
  fi
  printf '%s\n' "$ref"
}

run_lwrcl_script() {
  local script="$1"
  shift
  if [[ ! -x "$script" ]]; then
    echo "Required lwrcl script not found or not executable: $script" >&2
    exit 1
  fi
  echo "+ $script $*"
  "$script" "$@"
}

require_command git
require_command cmake
require_command python3

if [[ -z "$LWRCL_PREFIX" ]]; then
  LWRCL_PREFIX="$(default_lwrcl_prefix)"
fi
if [[ -z "$DDS_PREFIX" ]]; then
  DDS_PREFIX="$(default_dds_prefix)"
fi

mkdir -p "$(dirname "$LWRCL_SOURCE_DIR")"

if [[ ! -d "$LWRCL_SOURCE_DIR/.git" ]]; then
  echo "Cloning lwrcl: $LWRCL_REPO_URL"
  git clone --recursive "$LWRCL_REPO_URL" "$LWRCL_SOURCE_DIR"
else
  echo "Updating lwrcl checkout: $LWRCL_SOURCE_DIR"
  git -C "$LWRCL_SOURCE_DIR" remote set-url origin "$LWRCL_REPO_URL"
  git -C "$LWRCL_SOURCE_DIR" fetch --tags --prune origin
fi

RESOLVED_REF="$(resolve_lwrcl_ref)"
if [[ -n "$RESOLVED_REF" ]]; then
  echo "Checking out lwrcl ref: $RESOLVED_REF"
  git -C "$LWRCL_SOURCE_DIR" checkout --detach "$RESOLVED_REF"
else
  echo "No release/tag found. Using origin default branch."
  git -C "$LWRCL_SOURCE_DIR" remote set-head origin -a
  DEFAULT_BRANCH="$(git -C "$LWRCL_SOURCE_DIR" symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##')"
  git -C "$LWRCL_SOURCE_DIR" checkout "$DEFAULT_BRANCH"
  git -C "$LWRCL_SOURCE_DIR" pull --ff-only origin "$DEFAULT_BRANCH"
fi
git -C "$LWRCL_SOURCE_DIR" submodule update --init --recursive

echo "lwrcl source: $LWRCL_SOURCE_DIR"
echo "backend: $LWRCL_BACKEND"
echo "LWRCL_PREFIX: $LWRCL_PREFIX"
echo "DDS_PREFIX: $DDS_PREFIX"

cd "$LWRCL_SOURCE_DIR"

if [[ "$LWRCL_BACKEND" == "fastdds" && "$INSTALL_FASTDDS" -eq 1 ]]; then
  DDS_PREFIX="$DDS_PREFIX" run_lwrcl_script "./scripts/install_fast_dds.sh" --prefix "$DDS_PREFIX"
fi

if [[ "$BUILD_LIBRARIES" -eq 1 ]]; then
  LWRCL_PREFIX="$LWRCL_PREFIX" DDS_PREFIX="$DDS_PREFIX" run_lwrcl_script "./build_libraries.sh" "$LWRCL_BACKEND" ${INSTALL_ACTION:+"$INSTALL_ACTION"}
fi

if [[ "$BUILD_DATA_TYPES" -eq 1 ]]; then
  LWRCL_PREFIX="$LWRCL_PREFIX" DDS_PREFIX="$DDS_PREFIX" run_lwrcl_script "./build_data_types.sh" "$LWRCL_BACKEND" ${INSTALL_ACTION:+"$INSTALL_ACTION"}
fi

if [[ "$BUILD_LWRCL" -eq 1 ]]; then
  LWRCL_PREFIX="$LWRCL_PREFIX" DDS_PREFIX="$DDS_PREFIX" run_lwrcl_script "./build_lwrcl.sh" "$LWRCL_BACKEND" ${INSTALL_ACTION:+"$INSTALL_ACTION"}
fi

echo "lwrcl C++ environment is ready."
echo "Use this prefix for App builds when needed:"
echo "  LWRCL_PREFIX=$LWRCL_PREFIX DDS_PREFIX=$DDS_PREFIX scripts/build_macos_standalone.sh"
