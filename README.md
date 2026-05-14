# lwrclpy Web Node Editor

A ComfyUI-style web GUI for creating, connecting, editing, exporting, and importing user-defined `lwrclpy` nodes. It runs in a Python venv with `lwrclpy`; a full ROS 2 installation is not required.

## Run

```bash
cd lwrclpy_web_node_editor
python3.13 -m venv .venv
.venv/bin/python scripts/install_lwrclpy.py
.venv/bin/python main.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in a browser.

If `Port 8765 is already in use` is shown, stop the existing server or run on another port.

```bash
.venv/bin/python main.py --port 8766
```

## Workflow

- Click `Create Node` to create a custom lwrclpy node with configurable input and output ports.
- Enable `Timer Callback` when creating or editing a custom node to run periodic code at the configured interval.
- Each custom node has its own venv under `.node_envs/<node-id>`.
- Write node-specific Python imports in `Import Code`, for example `import cv2` or `import numpy as np`.
- Write node-specific dependencies in `requirements.txt`; `uv` creates the node venv and installs those dependencies before execution starts.
- Use only message/service types discovered from the installed `lwrclpy` message packages, such as `sensor_msgs/msg/Image`.
- No internal fake data types are used.
- Edge names are the ROS-compatible topic/service names.
- Ports can connect only when their data type matches.
- `Topic Input` is a terminal entrance node: it has no data type settings, no code editor, and no Python export. Its output port subscribes to the edge topic using the data type of the connected node.
- `Topic Output` is a terminal exit node: it has no data type settings, no code editor, and no Python export. Its input port publishes to the edge topic using the data type of the connected node.
- `Image File Input` loads an image in the browser and outputs it as `sensor_msgs/msg/Image`.
- `Video File Input` loads a video in the browser and outputs the current frame as `sensor_msgs/msg/Image`.
- `Image Viewer` displays a connected `sensor_msgs/msg/Image`.
- `Image File Save` saves a connected image as a PPM/PGM file under `saved_images/`.
- `Graph Viewer` accepts any connected data type and plots the numeric value selected by its field path, such as `data`, `pose.position.x`, or `ranges.0`.
- For image or video data, use real lwrclpy message types such as `sensor_msgs/msg/Image`.
- Custom nodes can still use callback code and main loop code when processing is needed.
- `Save` / `Load` saves and restores the full project as JSON.
- `Run` starts continuous graph execution, `Stop` stops it, and `Run For` runs for the specified duration in seconds.
- `Export lwrclpy Code` exports the custom-node part of the project as a runnable Python file.
- `Export ROS2 Launch` exports a ROS 2-compatible launch Python file that starts the exported project Python.

## Code Scopes

Subscribe/service callback code:

```python
# input_id: input port ID that received the message/request
# msg/request: lwrclpy/rclpy-compatible message or service request
# response: service response object when the input type is srv, otherwise None
# state: persistent per-node dictionary
# params: node parameter dictionary
# outputs: dictionary for output values
# publish(output_id, value): publish through an output port
# log(...): write to the node log
outputs["out1"] = msg
```

Main loop code:

```python
# inputs: values received from connected edges or lwrclpy subscriptions
# outputs: dictionary for output values
# state: persistent per-node dictionary
# params: node parameter dictionary
# now: time.time()
# latest(input_id): get the latest received value
# take(input_id): pop one item from the input queue
# has_input(input_id): return whether queued input exists
# publish(output_id, value): publish through an output port
# log(...): write to the node log
while has_input("in1"):
    outputs["out1"] = take("in1")
```

Timer callback code:

```python
# Runs when the configured timer period elapses during Run / Run For ticks
# inputs/outputs/state/params are the same as the main loop scope
# period: configured timer period in seconds
state["count"] = state.get("count", 0) + 1
outputs["out1"] = str(state["count"])
```

## lwrclpy Integration

`scripts/install_lwrclpy.py` installs the matching `lwrclpy` wheel for the current OS and Python version from GitHub Releases. The app uses the `rclpy`-compatible API provided by `lwrclpy` and subscribes/publishes using the topic/service names stored on graph edges.
