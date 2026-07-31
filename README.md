# lwrclpy Web Node Editor

[日本語版 README](README_JA.md)

A browser-based node editor for processing images, video, numeric data, and ROS 2-compatible messages. Python / `lwrclpy` and C++ / `lwrcl` nodes can run in the same graph.

ROS 2 itself is not required.

<img width="1407" height="902" alt="lwrclpy Web Node Editor" src="https://github.com/user-attachments/assets/a8949bbb-3566-4616-be8b-50e65d4073a4" />

## Quick Start

Start with Python nodes. C++ dependencies are not needed for the initial setup.

### 1. Install Python and Git

Required:

- 64-bit Python 3.13
- Git

#### Windows

1. Install [Python 3.13 for Windows](https://www.python.org/downloads/windows/).
2. Enable **Python Launcher** in the installer.
3. Install [Git for Windows](https://git-scm.com/download/win).
4. Open a new PowerShell window and verify:

```powershell
py -3.13 --version
git --version
```

#### macOS

Install [Homebrew](https://brew.sh/), then run:

```bash
brew install python@3.13 git
python3.13 --version
git --version
```

#### Ubuntu Linux

Install Git and the base packages:

When using WSL, install Git, clone the repository, and then continue with the dedicated WSL section below.

```bash
sudo apt update
sudo apt install -y git curl python3-venv
```

If your Ubuntu release provides Python 3.13:

```bash
sudo apt install -y python3.13 python3.13-venv
```

Otherwise, install Python 3.13 with `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
"$HOME/.local/bin/uv" python install 3.13
"$HOME/.local/bin/uv" python update-shell
```

Open a new terminal and verify:

```bash
python3.13 --version
git --version
```

### 2. Clone the Repository

Skip this step if the repository is already on your computer.

```bash
git clone https://github.com/tatsuyai713/lwrclpy-web-node-editor.git
cd lwrclpy-web-node-editor
```

### 3. WSL Setup

From the repository root in Ubuntu WSL:

```bash
bash scripts/setup_wsl.sh
source .venv/bin/activate
python main.py --host 127.0.0.1 --port 8765
```

The script automatically:

- verifies that it is running under WSL;
- installs Ubuntu packages including Git, `venv`, and `tkinter`;
- finds Python 3.13 or installs it with `uv`;
- creates `.venv`;
- installs `requirements.txt` and `lwrclpy`;
- checks Windows file-picker integration.

To use C++ nodes, include `--with-cpp`. This also builds FastDDS and `lwrcl` and takes longer:

```bash
bash scripts/setup_wsl.sh --with-cpp
source .venv/bin/activate
export LWRCL_PREFIX="$PWD/.local/fast-dds-libs"
export DDS_PREFIX="$PWD/.local/fast-dds"
python main.py --host 127.0.0.1 --port 8765
```

If you are not using WSL, continue below.

### 4. Install and Run

#### Windows

Run from this repository directory in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\install_lwrclpy.py
python main.py --host 127.0.0.1 --port 8765
```

If PowerShell reports that `Activate.ps1` cannot be run, allow it only in the current PowerShell process and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

#### macOS / Linux

Run from this repository directory:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/install_lwrclpy.py
python main.py --host 127.0.0.1 --port 8765
```

When this appears, the server is ready:

```text
lwrclpy Web Node Editor: http://127.0.0.1:8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765).

### 5. Run a Sample

1. Click `Load`.
2. Select `samples/image_video/02_video_motion_topic_graph.json`.
3. Click `Run`.
4. Confirm that the video, processed image, and graph update.
5. Click `Stop` when finished.

Press `Ctrl+C` in the server terminal to stop the server.

### Later Runs

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py --host 127.0.0.1 --port 8765
```

macOS / Linux:

```bash
source .venv/bin/activate
python main.py --host 127.0.0.1 --port 8765
```

## Using C++ Nodes

Skip this section when using only Python nodes.

C++ nodes require CMake, a C++ compiler, FastDDS, and `lwrcl`. Check the [`lwrcl` repository](https://github.com/tatsuyai713/lwrcl) for its current supported platforms and build instructions.

### Linux / macOS

Install build tools first.

Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential cmake automake autoconf libtool \
  bison flex curl wget unzip tar pkg-config libssl-dev libasio-dev
```

macOS:

```bash
xcode-select --install
brew install cmake automake autoconf libtool bison flex openssl@3
```

Build FastDDS, message types, and `lwrcl`:

```bash
scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"
```

The script fetches the [`lwrcl` repository](https://github.com/tatsuyai713/lwrcl) and builds the required components. Start the server from the same terminal:

```bash
export LWRCL_PREFIX="$PWD/.local/fast-dds-libs"
export DDS_PREFIX="$PWD/.local/fast-dds"
source .venv/bin/activate
python main.py --host 127.0.0.1 --port 8765
```

C++ samples are under `samples/cpp/`.

### Windows

1. Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).
2. Select **Desktop development with C++**.
3. Include MSVC, a Windows SDK, and CMake tools.
4. Open **Developer PowerShell for VS** and verify `cl` and `cmake --version`.

A Windows-native `lwrcl` + FastDDS install prefix is also required. The current upstream [`lwrcl` build instructions](https://github.com/tatsuyai713/lwrcl) cover Linux, macOS, and QNX, and this repository's setup script uses Bash. Libraries built on Linux or macOS cannot be used on Windows.

If a Windows prefix is available:

```powershell
$env:LWRCL_PREFIX = "C:\lwrcl\fast-dds-libs"
$env:DDS_PREFIX = "C:\lwrcl\fast-dds"
$env:CPP_DEP_PREFIXES = "$env:LWRCL_PREFIX;$env:DDS_PREFIX"

Test-Path "$env:LWRCL_PREFIX\include\lwrcl.hpp"
Get-ChildItem "$env:LWRCL_PREFIX\lib\*lwrcl*"

.\.venv\Scripts\Activate.ps1
python main.py --host 127.0.0.1 --port 8765
```

The first check must print `True`, and the second must list the `lwrcl` library.

## Main Features

- Connect and run image, video, and numeric processing nodes
- Python / `lwrclpy` and C++ / `lwrcl` custom nodes
- Image, graph, string, TF, PointCloud, and Robot Model viewers
- MCAP / rosbag playback and recording
- External DDS topic input and output
- Project save/load and CLI or ROS 2 package export

Runnable examples are under `samples/`. See [samples/README.md](samples/README.md) for the full list.

## Basic Controls

- `Create Node`: create a custom node
- `Load`: open a project or sample
- `Save`: save the current project
- `Run`: run the graph continuously
- `Run For`: run for a specified duration
- `Stop`: stop execution and worker processes
- `Export CLI Package`: create a ZIP that runs without the Web UI

Packages listed in a Python custom node's `requirements.txt` are installed automatically into `.node_envs/<node-id>`.

## Export and Run a CLI Package

CLI Export runs a graph without the Web UI. The exported package can also be moved to another computer.

### 1. Export the CLI Package

1. Open the project in Web Node Editor.
2. Click `Export CLI Package`.
3. Download `<project-name>_cli_package.zip`.
4. Extract the complete ZIP. Do not run files from inside the ZIP.
5. Open a terminal in the extracted directory containing `run_project.py`.

Main exported files:

```text
<project>_cli/
  run_project.py
  project.json
  requirements.txt
  README.md
  lwrclpy_web_node_editor/
```

### 2. Create the Runtime Environment Once

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS / Linux / WSL:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run these commands from the directory containing `requirements.txt` and `run_project.py`. If no `lwrclpy` wheel was bundled during export, the first run needs internet access to download matching wheels for node environments.

### 3. Run the Project

Run until stopped:

```bash
python run_project.py
```

Run for ten seconds:

```bash
python run_project.py --duration 10
```

Node status is printed in the terminal. Press `Ctrl+C` to stop.

For later runs, enter the extracted directory, activate `.venv`, and run only `python run_project.py`.

### Run on Another Computer

- The target needs 64-bit Python 3.13.
- Move external video, MCAP, and URDF files and update paths stored in `project.json`.
- For external DDS communication, match `ROS_DOMAIN_ID`, network settings, and QoS.
- Runtime environments and logs are created under `.node_envs/` and `.node_workers/`.

### Projects with C++ Nodes

First install [lwrcl](https://github.com/tatsuyai713/lwrcl) and FastDDS as described in Using C++ Nodes. Follow the exported `README.md` and build the C++ nodes with `build_cpp_nodes.sh`. The Python runner and C++ executables communicate over the same DDS topic names.

## Troubleshooting

### Python Is Not Found

Windows:

```powershell
py -3.13 --version
```

macOS / Linux:

```bash
python3.13 --version
```

If no version is displayed, repeat the Python installation and open a new terminal.

### lwrclpy Installation Fails

The wheel must match the Python version, OS, and CPU architecture:

```bash
python3.13 -c "import platform; print(platform.platform(), platform.machine())"
```

To use a local wheel:

```bash
python main.py --lwrclpy-wheel /path/to/lwrclpy.whl
```

Activate `.venv` using the command for your OS before running it.

### File Selection Does Not Open in WSL

WSL does not use `tkinter` for file selection. It opens a Windows PowerShell file dialog and converts the selected path to a WSL path. Verify both commands:

```bash
powershell.exe -NoProfile -Command '$PSVersionTable.PSVersion'
wslpath -u 'C:\Windows'
```

If `powershell.exe` is unavailable, Windows executable interoperability is disabled in WSL. `tkinter` is not a pip package, so adding it to `requirements.txt` does not solve this problem.

### Port 8765 Is Unavailable

Use another port:

```bash
python main.py --host 127.0.0.1 --port 8766
```

### C++ Node Build Fails

- `cmake not found`: verify `cmake --version`.
- Compiler missing on Windows: start the server from Developer PowerShell for VS.
- Missing `lwrcl.hpp` or `fastrtps`: verify `LWRCL_PREFIX` and `DDS_PREFIX`.
- Full log: `.node_workers/<node-id>.cpp.log`

## Developer Information

Everything below is for developing or packaging the editor itself. Regular users do not need it.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete process, communication, worker, and packaging architecture.

### Runtime Layout

- Server: `main.py` and `lwrclpy_web_node_editor/server.py`
- Graph runtime: `lwrclpy_web_node_editor/graph.py`
- Web UI: `lwrclpy_web_node_editor/static/`
- Python workers: `.node_envs/<node-id>`
- C++ workers: `.node_workers/cpp/<node-id>`

Graph connections run as DDS topics. Worker processes started by the application are stopped when the server exits.

### Using `scripts/`

Run every command from the repository root. Activate `.venv` before running a Python script.

| File | When to use it |
| --- | --- |
| `setup_wsl.sh` | First-time WSL environment setup |
| `install_lwrclpy.py` | Install the matching `lwrclpy` wheel |
| `setup_lwrcl_cpp_env.sh` | Build the C++ node environment on Linux/macOS/WSL |
| `build_linux_standalone.sh` | Build the Linux distributable app |
| `build_macos_standalone.sh` | Build the macOS distributable app |
| `build_windows_standalone.ps1` | Build the Windows distributable app |
| `repair_lwrclpy_windows_wheel.py` | Repair a wheel for Windows App Control |
| `verify_standalone_bundle.py` | Validate a built distributable app |
| `relocate_cpp_prefix.py` | Repair absolute paths in a copied C++ prefix |
| `bundle_linux_cpp_runtime.py` | GCC runtime helper called by the Linux build |

#### Set Up WSL

For Python nodes:

```bash
bash scripts/setup_wsl.sh
```

To include the C++ node environment:

```bash
bash scripts/setup_wsl.sh --with-cpp
```

#### Install `lwrclpy`

Install the latest wheel matching the current OS, Python, and CPU:

```bash
source .venv/bin/activate
python scripts/install_lwrclpy.py
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\install_lwrclpy.py
```

To install a local wheel:

```bash
LWRCLPY_LOCAL_WHEEL=/path/to/lwrclpy.whl python scripts/install_lwrclpy.py
```

```powershell
$env:LWRCLPY_LOCAL_WHEEL = "C:\path\to\lwrclpy.whl"
python scripts\install_lwrclpy.py
```

#### Build the C++ Node Environment

This script supports Linux, macOS, and WSL. By default, it builds FastDDS, Fast-DDS-Gen, message types, and `lwrcl` under `/opt`.

```bash
bash scripts/setup_lwrcl_cpp_env.sh
```

To install inside the repository without administrator access:

```bash
bash scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"
```

List all options:

```bash
bash scripts/setup_lwrcl_cpp_env.sh --help
```

#### Build Standalone Apps

Install Node.js/npm and activate `.venv` first.

```text
node --version
npm --version
```

```bash
# Linux
PYTHON_BIN="$(command -v python)" bash scripts/build_linux_standalone.sh

# macOS
PYTHON_BIN="$(command -v python)" bash scripts/build_macos_standalone.sh
```

Windows:

```powershell
$env:PYTHON_BIN = (Get-Command python).Source
powershell -ExecutionPolicy Bypass -File scripts\build_windows_standalone.ps1
```

Outputs are created under `dist/`. Build each target on its own OS rather than cross-building.

#### Verify a Standalone Bundle

```bash
python scripts/verify_standalone_bundle.py dist/lwrclpy-web-node-editor
```

Require a bundled C++ prefix:

```bash
python scripts/verify_standalone_bundle.py \
  dist/lwrclpy-web-node-editor \
  --require-cpp-prefix
```

On macOS, the first argument may be the `.app`; on Windows, pass the generated application directory.

#### Make a C++ Prefix Relocatable

This repairs broken absolute symlinks and stale CMake/pkg-config paths after a prefix is copied. Standalone build scripts normally call it automatically.

```bash
python scripts/relocate_cpp_prefix.py /path/to/copied/lwrcl_cpp
```

Suppress normal output with:

```bash
python scripts/relocate_cpp_prefix.py /path/to/copied/lwrcl_cpp --quiet
```

#### Linux C++ Runtime Helper

`bundle_linux_cpp_runtime.py` is an internal Linux packaging helper. It downloads the `libstdc++` and `libgcc` runtimes from conda-forge and places them under PyInstaller's `_internal` directory. Normally, let `build_linux_standalone.sh` invoke it.

Use it directly only when testing the packaging step:

```bash
python scripts/bundle_linux_cpp_runtime.py \
  dist/lwrclpy-web-node-editor/resources/lwrclpy-web-node-editor-server/_internal
```

### Windows lwrclpy Wheels

For App Control / Code Integrity environments:

```powershell
python scripts\repair_lwrclpy_windows_wheel.py path\to\lwrclpy.whl `
  -o path\to\lwrclpy.repaired.whl
```

This replaces the OpenSSL DLLs with signed copies from the official Python runtime and updates the wheel `RECORD`.

### Export

See Export and Run a CLI Package in the regular user documentation for the CLI workflow.

`Export ROS 2 Package` creates a regular ROS 2 `rclpy` package. Web-only viewer nodes are excluded from the exported runtime.

## Important Files

- [samples/README.md](samples/README.md): sample list
- [ARCHITECTURE.md](ARCHITECTURE.md): process, communication, worker, and packaging architecture
- `scripts/`: see "Using `scripts/`" above
