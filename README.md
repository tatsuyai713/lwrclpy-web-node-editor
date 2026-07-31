# lwrclpy Web Node Editor

[日本語版 README](README_JA.md)

`lwrclpy Web Node Editor` is a browser-based node graph editor for building, editing, running, saving, and exporting image-processing, video-processing, numeric-processing, TF, MCAP, LLM, and custom runtime workflows.

The app does not require a full ROS 2 installation. Python nodes use the `rclpy`-compatible API provided by `lwrclpy`, and C++ custom nodes can use `lwrcl` + FastDDS.

<img width="1407" height="902" alt="Screenshot 2026-07-21 at 13 48 16" src="https://github.com/user-attachments/assets/a8949bbb-3566-4616-be8b-50e65d4073a4" />


## Features

- Create node graphs in the browser and connect inputs, processing nodes, viewers, graph plots, and topic outputs.
- Load images with `Image File Input` and process them as `sensor_msgs/msg/Image`.
- Select video files with `Video File Input`; frames are published while the graph is running.
- Play and record ROS 2 MCAP/rosbag data with `MCAP File Input` and `MCAP Record`.
- View images, strings, chat messages, numeric plots, TF, PointCloud2, OccupancyGrid, and URDF/Xacro robot models.
- Use `Interactive Text Input` while the graph is running.
- Call Ollama, OpenAI, OpenAI-compatible APIs, or LM Studio with `LLM Text`.
- Represent external DDS/lwrclpy topics with `Topic Input` and `Topic Output`.
- Create Python custom nodes with per-node `requirements.txt` and isolated `.node_envs/<node-id>` virtual environments.
- Create C++ custom nodes with `lwrcl` + FastDDS. C++ nodes are generated, built, and launched automatically during Web execution.
- Save and load complete projects as JSON.
- Export custom nodes as Python files.
- Export projects as ROS 2 Python packages or CLI runner packages. Exports containing C++ nodes include `cpp_nodes/` and `build_cpp_nodes.sh`.

## Run From Source

Run these commands from this directory:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/install_lwrclpy.py
.venv/bin/python main.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in your browser.

To use a locally built `lwrclpy` wheel, pass it when starting the server. The same wheel is used by the server and each custom-node environment.

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8765 \
  --lwrclpy-wheel /Users/tatsuyai/repos/lwrclpy/dist/lwrclpy-0.5.1-cp313-cp313-macosx_26_0_arm64.whl
```

When producing a Windows `lwrclpy` wheel for App Control / Code Integrity environments, repair the wheel before publishing it. This replaces `lwrclpy/_vendor/lib/libcrypto-3-x64.dll` and `libssl-3-x64.dll` with the signed DLLs from the official Python runtime and updates the wheel `RECORD`.

```powershell
python scripts\repair_lwrclpy_windows_wheel.py path\to\lwrclpy-...-win_amd64.whl -o path\to\lwrclpy-...-win_amd64.repaired.whl
```

The wheel still uses the bundled `lwrclpy` / FastDDS DLLs; only the OpenSSL DLLs that commonly trip App Control are replaced.

Only one server can run in the same working context. If another server is already running, startup fails with:

```text
Another lwrclpy Web Node Editor server is already running (lock: .../server.lock). Stop it first before starting a new instance.
```

Use another port when needed:

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8766
```

## Standalone Builds

Each OS must build its own standalone package on that OS. By default the build scripts use `venv`; set `PYTHON_BIN` to use a different Python executable. Standalone desktop packages use an Electron native window and bundle the Python server under the app resources, so Node.js/npm must be available during the build.

### Linux

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
scripts/build_linux_standalone.sh
```

Run the desktop app:

```bash
dist/lwrclpy-web-node-editor/lwrclpy-web-node-editor
```

Run server mode:

```bash
dist/lwrclpy-web-node-editor/resources/lwrclpy-web-node-editor-server/lwrclpy-web-node-editor-server --server --host 127.0.0.1 --port 8765
```

Standalone mode uses this default working directory:

```text
~/.local/share/lwrclpy-web-node-editor
```

Override it with:

```bash
export LWRCLPY_WEB_NODE_EDITOR_HOME=/path/to/workdir
```

### macOS

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
scripts/build_macos_standalone.sh
```

The macOS build bundles:

- `samples/`
- the Python `lwrclpy` runtime
- `cmake` for C++ worker builds
- C++ `lwrcl` + FastDDS dependencies under `lwrcl_cpp`

C++ dependency prefixes are copied in this order:

- `LWRCL_PREFIX`
- `CPP_DEP_PREFIXES` (`:` separated)
- `/opt/fast-dds-libs`
- `/opt/fast-dds`

To update and install the C++ environment before building the app:

```bash
scripts/setup_lwrcl_cpp_env.sh
scripts/build_macos_standalone.sh
```

To install the C++ environment into a local prefix and bundle that prefix:

```bash
scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"

LWRCL_PREFIX="$PWD/.local/fast-dds-libs" \
DDS_PREFIX="$PWD/.local/fast-dds" \
scripts/build_macos_standalone.sh
```

Run the generated app:

```bash
open dist/lwrclpy-web-node-editor.app
dist/lwrclpy-web-node-editor.app/Contents/Resources/lwrclpy-web-node-editor-server/lwrclpy-web-node-editor-server --server --host 127.0.0.1 --port 8765
```

For a signed build:

```bash
export MAC_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
scripts/build_macos_standalone.sh
```

Set `MAC_CODESIGN_ENTITLEMENTS=/path/to/entitlements.plist` when needed.

For distribution to other Macs, sign with a Developer ID Application certificate and notarize with Apple. CI releases can use these secrets:

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_KEYCHAIN_PASSWORD`
- `MACOS_NOTARY_KEY_ID`
- `MACOS_NOTARY_ISSUER_ID`
- `MACOS_NOTARY_KEY`

For local testing of an unsigned or non-notarized zip:

```bash
xattr -dr com.apple.quarantine dist/lwrclpy-web-node-editor.app
```

### Windows

```powershell
py -3.13 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows_standalone.ps1
```

Run the generated app:

```powershell
dist\lwrclpy-web-node-editor\lwrclpy-web-node-editor.exe
dist\lwrclpy-web-node-editor\resources\lwrclpy-web-node-editor-server\lwrclpy-web-node-editor-server.exe --server --host 127.0.0.1 --port 8765
```

## First Run

1. Start the server.
2. Open `http://127.0.0.1:8765`.
3. Click `Load` and select `samples/image_video/02_video_motion_topic_graph.json`.
4. Click `Run`.
5. Confirm that the video input, processed image, and motion-score graph update.
6. Click `Stop` to stop execution.

The continuous run loop ticks at 60 Hz. `Run For` executes for the specified duration and then stops automatically.

## Keyboard Shortcuts

- `Ctrl+S` / `Cmd+S`: save.
- `Ctrl+Shift+S` / `Cmd+Shift+S`: save as.
- `Ctrl+Z` / `Cmd+Z`: undo.
- `Ctrl+Shift+Z` / `Cmd+Shift+Z` or `Ctrl+Y`: redo.

When the browser does not support the File System Access API, saving falls back to downloading a JSON file.

## Sample Projects

The `samples/` directory contains runnable project JSON files organized by category: `image_video/`, `signals/`, `external_topics/`, `custom_runtime/`, `cpp/`, `deep_learning/`, `tf/`, and `llm/`.

Representative samples:

- `image_video/01_image_edge_topic_graph.json`: image input, grayscale, edge extraction, image view, edge-intensity graph, and topic output.
- `image_video/02_video_motion_topic_graph.json`: video frames, motion mask, overlay view, motion-score graph, and topic output.
- `signals/06_function_generator_signal_view.json`: function generator connected to a graph viewer and topic output.
- `external_topics/08_external_float_topic_graph.json`: external `std_msgs/msg/Float32` topic displayed in a graph viewer.
- `custom_runtime/10_multi_timer_counter_graph.json`: one custom node with multiple timers.
- `custom_runtime/11_manual_subscriber_timer_sampler.json`: manual `latest()` access from a timer callback.
- `cpp/21_cpp_low_pass_filter.json`: C++ custom node low-pass filter using a subscribe callback.
- `cpp/22_cpp_signal_mixer.json`: C++ custom node that adds and subtracts two signals.
- `cpp/23_cpp_image_threshold.json`: C++ custom node that thresholds an image and outputs a mask plus ratio.
- `deep_learning/13_ultralytics_yolo_detection_segmentation.json`: Ultralytics YOLO detection and segmentation.
- `deep_learning/17_sam_midas_segmentation_depth.json`: Segment Anything masks and MiDaS depth.
- `llm/18_ollama_llm_string_view.json`: prompt topic to Ollama, displayed with string viewers.
- `tf/19_urdf_xacro_tf_static_merge_custom_tf.json`: URDF static TF, custom TF output, TF merge, and 3D viewer.
- `llm/20_interactive_llm_chat.json`: interactive prompt input and chat-style LLM response display.

See `samples/README.md` for the full sample list.

Regenerate samples with:

```bash
.venv/bin/python samples/generate_sample_projects.py
```

## Execution Model

Web preview sends graph data over lwrclpy topics. Internal graph edges are not direct in-process calls; they are DDS/lwrclpy topic connections.

- Custom node code runs in a scope close to a ROS 2 callback.
- `Topic Input` and `Topic Output` are graph boundary markers. Actual publish/subscribe work happens in the connected processing, viewer, source, or tap worker.
- Image inputs can use embedded image data.
- Video inputs are decoded by `video_dds_worker.py` and published as `sensor_msgs/msg/Image`.
- C++ / `lwrcl` nodes are generated under `.node_workers/cpp/<node-id>/`, built with CMake, and launched as worker processes.

## C++ / lwrcl + FastDDS

For Web execution and CLI export, install `lwrcl` with the FastDDS backend first. Web execution also needs CMake and a C++ compiler.

Recommended setup:

```bash
scripts/setup_lwrcl_cpp_env.sh
```

By default, the script resolves `tatsuyai713/lwrcl` as follows:

1. GitHub latest release tag.
2. Newest git tag.
3. Default branch.

It then updates submodules and runs the FastDDS, libraries, data-types, and lwrcl build/install steps.

Use a local prefix:

```bash
scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"
```

Manual setup:

```bash
git clone --recursive https://github.com/tatsuyai713/lwrcl.git
cd lwrcl
./scripts/install_fast_dds.sh
./build_libraries.sh fastdds install
./build_data_types.sh fastdds install
./build_lwrcl.sh fastdds install
```

When a C++ node is used, the editor generates a CMake project and rebuilds it whenever the C++ code, ports, or topic connections change. The generated executable is launched as a worker process.

CLI exports containing C++ nodes include:

- `cpp_nodes/`
- `build_cpp_nodes.sh`

Build exported C++ nodes with:

```bash
bash build_cpp_nodes.sh
```

Python and C++ nodes can be mixed. The exported runner uses the same graph edge names as DDS topic names, so Python nodes and C++ executables communicate through the same DDS domain.

## Custom Nodes

Create custom nodes with `Create Node`.

Important settings:

- `Implementation`: Python / `lwrclpy` or C++ / `lwrcl` + FastDDS.
- `Inputs`: input ports. `Use Callback` is enabled by default. Disable it to read inputs manually with `latest()` / `take()`.
- `Outputs`: output ports.
- `Callback Code`: code executed when an input receives data.
- `Timer Callback`: periodic callback code. Multiple timers are supported.
- `TF Input` / `TF Output`: enable TF features with `tf2_ros`.
- Python `Import Code`: per-node imports.
- Python `requirements.txt`: per-node Python dependencies.
- C++ `Header`: inserted after generated includes. Use it for `#include`, helpers, constants, classes, structs, and static objects.
- C++ `C++ Link Libraries`: extra link items such as `-lm`, `-lmy_library`, or `/path/to/libfoo.a`.
- C++ `Initialize Code`: generated constructor code executed once after publisher/subscriber setup and before loop/timer execution.

C++ custom nodes can use generated helpers such as `has_in1()`, `latest_in1()`, and `publish_out1(msg)`. Keep persistent state in classes or objects defined in the Header.

Loop Code is this node's own loop, exactly as in a hand-written `rclcpp` node. You write the loop, and `main()` calls Loop Code once:

```cpp
while (rclcpp::ok()) {
  rclcpp::spin_some(node);   // dispatch ready input callbacks and timers
  loop_rate.sleep();         // hold Run Hz
}
```

For a callback-only node, replace that with:

```cpp
rclcpp::spin(node);
```

Loop Code counts as self-driving when it contains its own `while` / `for`, or calls `rclcpp::spin(...)`; commented-out examples are ignored. Loop Code that has none of those is treated as a single cycle body and `main()` wraps it in `while (rclcpp::ok())`, which is what C++ projects saved before this convention contain, so they keep working unchanged.

## Code Scope Reference

Every code slot is opened from the node inspector, and the editor shows this same reference — name, type, and contents — under the text area.

Python `Callback Code` runs once per received message. `msg` is the message **as a dict of its fields**, not a ROS message object:

| Name | Type | Contents |
| --- | --- | --- |
| `msg` | `dict` | Received message fields. `std_msgs/Float64` arrives as `{"data": 1.0}`; `sensor_msgs/Image` as `{"height", "width", "encoding", "step", "is_bigendian", "data"}` where `data` is bytes-like, so `np.frombuffer(msg["data"], dtype=np.uint8)` works. |
| `input_id` | `str` | Id of the input port that received the message. |
| `request` | `dict` | Same object as `msg`; use this name for a service input. |
| `response` | `srv Response` / `None` | For a service input, fill its fields to answer the caller. `None` for topic inputs. |
| `node` | `lwrclpy.Node` | This node, for APIs such as `node.get_clock()`. |
| `state` | `dict` | Empty at startup, kept for the node's lifetime. Use it for anything that must survive between calls. |
| `params` | `dict` | The Parameters JSON. Every key is also a plain variable of the same name. |
| `publish(output_id, value)` | function | Sends `value` on an output port. `value` may be a dict of message fields. |
| `log(*values)` | function | Writes to the node's log panel; `print(...)` is equivalent. |

Python `Main Loop` is this node's own loop, written the way a hand-written `rclpy` node is written. The default is `rclpy.spin(node)`, which dispatches input callbacks and Timer Callbacks until shutdown; write `while rclpy.ok(): ...` yourself when you want an explicit tick:

| Name | Type | Contents |
| --- | --- | --- |
| `rclpy` | module | The `lwrclpy` module: `rclpy.ok()`, `rclpy.spin(node)`, `rclpy.spin_once(node, timeout_sec=...)`. |
| `rate` | `Rate` | `rate.sleep()` waits out the remainder of the current period, so your work is not added on top of it. |
| `run_hz` / `loop_period` | `float` | Configured Run Hz, and `1.0 / run_hz` in seconds. |
| `now` | `float` | `time.time()` sampled when the call started. |
| `has_input(input_id)` | function → `bool` | True while unread values are queued on that input. |
| `latest(input_id, default=None)` | function → `dict` / `None` | Most recent value, without consuming it. |
| `take(input_id, default=None)` | function → `dict` / `None` | Removes and returns the oldest queued value. |
| `inputs` | `dict` | Snapshot of the last value per input id. |

Python `Timer Callback` runs every `periodSec` seconds regardless of input traffic, and — like ROS 2 `create_timer` — first fires one full period after start. It sees `timer_id` (`str`), `timer_name` (`str`), `period` (`float`), `now` (`float`), the `has_input` / `latest` / `take` / `inputs` group, and the shared `node` / `state` / `params` / `publish` / `log` group.

Python `Import Code` runs once after the node's venv is ready and before any callback. Names defined there are visible from Callback, Main Loop, and Timer code.

C++ `Callback Code`, `Loop Code`, and `Timer Callback` see `msg` as `const pkg::msg::Type&`, `now` as `std::chrono::steady_clock::time_point`, `has_<input_id>()` returning `bool`, `latest_<input_id>()` returning `const pkg::msg::Type*` (`nullptr` until the first message), and `publish_<output_id>(msg)`. Loop Code additionally sees `node` and `loop_rate`.

## Python Callback Code

Example:

```python
node.get_logger().info(f"received {input_id}")
publish("out1", msg)
```

For multiple inputs, store the latest values in `state`:

```python
state[input_id] = msg
frame = state.get("frame")
mask = state.get("mask")

if frame and mask:
    publish("out1", frame)
```

For multiple outputs, call `publish(...)` once per output port.

## Timers And Main Loop

Timer callbacks are registered with `node.create_timer()`, so they fire from the executor whenever the node is spinning. Like ROS 2 `create_timer`, the first callback runs after one full period rather than immediately at Run start.

```python
state["count"] = state.get("count", 0) + 1
publish("out1", str(state["count"]))
```

Main Loop is the node's own loop. The default is the `rclpy` idiom:

```python
rclpy.spin(node)
```

That dispatches input callbacks and Timer Callbacks until shutdown, so periodic work belongs in a Timer Callback. Write the loop yourself when you want an explicit tick, and it is used as-is:

```python
while rclpy.ok():
    rclpy.spin_once(node, timeout_sec=0.0)
    if state.get("enabled", True):
        node.get_logger().info("tick")
    rate.sleep()
```

Main Loop counts as self-driving when it contains a top-level `while`, or calls `spin(...)` / `spin_until_future_complete(...)`; it is then executed once. The code is parsed to decide this, so a commented-out example or a spin call inside a string does not count. Anything else is treated as a single tick body and is called repeatedly at Run Hz — which is what projects saved before this convention contain, so they keep working unchanged.

Prefer Callback Code for data-dependent processing: it runs as soon as a message arrives instead of waiting for the next tick. Note that `spin_once(node, timeout_sec=0.0)` dispatches **one** callback per call, exactly as in ROS 2, so a hand-written tick loop processes one callback per period. Use `rclpy.spin(node)` when a node must keep up with a high-rate topic.

## Built-In Nodes

### Image And Video

`Image File Input` publishes selected images as `sensor_msgs/msg/Image`.

- `One Shot`: publish the same image for a few ticks.
- `Rate`: repeatedly publish at the configured rate.

`Video File Input` uses `Select Video` to choose a server-side file. During Run, `video_dds_worker.py` decodes frames with OpenCV and publishes them at the source FPS. Sample video projects can generate simple embedded frames without an external file.

`Image File Save` saves connected images to `saved_images/` as BMP files.

### MCAP / rosbag

`MCAP File Input` plays `.mcap` files or ROS 2 bag directories. Use `Rate` for playback speed and `Loop` to replay.

`MCAP Record` records multiple input topics in ROS 2 bag format. Set `Split MB` to split output files by size.

### Signals

`Function Generator` publishes `std_msgs/msg/Float32` values. Supported waveforms include Step, Sine, Square, Ramp, Chirp, and White Noise.

### TF / 3D Viewer

`URDF Static TF` publishes fixed joints from URDF/Xacro to `/tf_static`.

`TF Merge` represents multiple TF producers connected to one TF consumer.

`3D Viewer` can display TF, grids, PointCloud2, OccupancyGrid, and robot models. Mouse controls rotate, pan, and zoom the 3D view.

### LLM / Chat

`Interactive Text Input` publishes text while the graph is running and keeps a prompt history.

`LLM Text` consumes prompt topics and publishes string responses from Ollama, OpenAI, OpenAI-compatible APIs, or LM Studio. `Chat String Viewer` displays accumulated chat messages.

### Topic Input / Topic Output

`Topic Input` and `Topic Output` are graph boundary nodes for external lwrclpy topics.

- `Topic Input`: external data enters the graph; the connected downstream node subscribes.
- `Topic Output`: graph data leaves the graph; the connected upstream node publishes.
- Edge names are editable topic names.
- Multiple edges from the same output port share the same topic name.

## Export / Import

`Export Python Node` saves the selected custom node as a Python file. Use `Import Python Node` to load it later.

`Export ROS 2 Package` creates a ROS 2 Python package zip containing generated runner code, project JSON, launch files, and dynamic `package.xml` dependencies.

`Export CLI Package` creates a standalone CLI runner zip containing `run_project.py`, `project.json`, runtime files, and any configured local `lwrclpy` wheel.

## Dependencies And Environments

The app itself runs in `.venv`. Each Python custom node gets its own `.node_envs/<node-id>` environment.

When a node has `requirements.txt`, `uv` creates the node environment and installs its dependencies. `lwrclpy` is selected automatically from the GitHub Releases `latest` tag for the current Python ABI, OS, and CPU architecture.

`Stop` stops the active run and cleans up worker processes created by this framework. Startup and shutdown also remove stale worker processes.

## Troubleshooting

### Port Already In Use

Start on another port or stop the old server:

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8766
```

### Duplicate Server Error

If `Another lwrclpy Web Node Editor server is already running` appears, another server is active for the same working context. Stop it first or use another working directory.

### Video Does Not Move

- Use `Run`; one tick is usually not enough for video playback.
- For real video files, use `Select Video`, wait for `Ready`, then click `Run`.
- Try `samples/image_video/02_video_motion_topic_graph.json` or `samples/image_video/04_video_low_light_colormap_topic_graph.json`.

### Node Output Is Missing

- Check that connected input and output port types match.
- In Callback Code, use `publish("out1", value)` rather than `outputs["out1"] = ...`.
- Image outputs should include `width`, `height`, `encoding`, `step`, and `data`.

### Python Import Fails

Add the dependency to the custom node `requirements.txt` and run again. The editor creates `.node_envs/<node-id>` and installs dependencies there.

## Important Files

- `main.py`: integrated entry point for server, desktop, and workers.
- `lwrclpy_web_node_editor/server.py`: HTTP API and static file server.
- `lwrclpy_web_node_editor/graph.py`: graph execution, image conversion, and lwrclpy topic integration.
- `lwrclpy_web_node_editor/static/app.js`: browser UI.
- `samples/generate_sample_projects.py`: sample JSON generator.
- `scripts/install_lwrclpy.py`: installs the matching `lwrclpy` wheel.
- `scripts/setup_lwrcl_cpp_env.sh`: fetches, builds, and installs the C++ `lwrcl` environment.
