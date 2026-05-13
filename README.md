# lwrclpy Web Node Editor

A ComfyUI-style web GUI for creating, connecting, editing, exporting, and importing user-defined `lwrclpy` nodes. It is designed to run with a Python venv and the `lwrclpy` wheel, without a full ROS 2 installation.

## Run

The macOS `lwrclpy` wheels target Python 3.10 through 3.13. This workspace uses Python 3.13.

```bash
cd web_base_new
python3.13 -m venv .venv
.venv/bin/python scripts/install_lwrclpy.py
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in a browser.

If `Port 8765 is already in use` is shown, stop the existing server or run on another port.

```bash
.venv/bin/python main.py --port 8766
```

## Workflow

- Click `Create Node` to open the node configuration window.
- Set the node name, input count, and output count.
- Configure each input with a port name, message package, `msg/srv` kind, message/service name, and receive mode.
- Configure each output with a port name, message package, `msg/srv` kind, and message/service name.
- Message packages, `msg/srv` kinds, and message/service names are discovered automatically from the type packages installed in the venv through `lwrclpy`.
- Created nodes expose `Configure`, `Callback`, `Main Loop Code`, and `Export Python` actions.
- The `Tool Nodes` palette provides ready-made nodes for image input, video input, basic image processing, image/video display, generic data input, counter data, graph plotting, and data display.
- `Callback Code` runs for each input subscription/service callback when its receive mode is `Callback`.
- `Main Loop Code` runs on each `Run Once` or `Auto Spin` tick.
- Drag from an output port to an input port to connect nodes. Only ports with the same message type can be connected.
- The edge name becomes the `lwrclpy` topic/service name. Topic/service names are not configured on the node itself.
- Select an edge to edit its topic/service name in the Inspector. Double-click an edge to delete it.
- Drag empty canvas space to pan, and use the mouse wheel to zoom.
- `Export Graph` / `Import Graph` saves and restores the full graph as JSON.
- `Export Python` saves a selected node as a runnable `lwrclpy` Python file.
- `Import Python Node` creates a GUI node from a Python file exported by this editor.

## Tool Nodes

Tool nodes use internal graph-only data types such as `tool/image`, `tool/video`, and `tool/data`. These ports can be connected inside the web graph, but they do not create `lwrclpy` publishers, subscribers, services, or clients. ROS-compatible ports still use normal `package/msg/Name` or `package/srv/Name` types and edge names as topic/service names.

Available built-in tool nodes:

- `Image File Input`: loads an image file in the browser and outputs `tool/image`.
- `Video File Input`: loads a video file in the browser and outputs `tool/video`.
- `Image Display`: displays a connected `tool/image` value inside the node.
- `Grayscale`, `Resize Image`, `Blur Image`, `Brightness`, and `Contrast`: process `tool/image` values with Pillow and output `tool/image`.
- `Video Display`: displays a connected `tool/video` value inside the node.
- `Data Source`: outputs editable text/numeric data as `tool/data`.
- `Counter Data`: outputs an incrementing counter as `tool/data`.
- `Data Plot`: plots numeric `tool/data` values as a compact line graph.
- `Data Display`: displays generic `tool/data` values as text.

## Python Node Export

The exported Python file contains:

- a standalone `lwrclpy` node runner
- the node input/output definitions
- callback code and main loop code
- edge-derived topic/service names for connected ports
- embedded metadata used by `Import Python Node`

The Python import path intentionally reads the embedded metadata. It is reliable for files exported by this editor. Arbitrary handwritten Python files cannot always be converted back into GUI nodes because Python code does not have a single unambiguous node graph representation.

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
# show_image(value, title): display an image data URL inside the node
# show_video(value, title): display a video data URL inside the node
# show_plot(series, title): display a compact graph inside the node
# show_text(value, title): display generic data inside the node
# image_grayscale(value), image_resize(value, width, height)
# image_blur(value, radius), image_brightness(value, factor), image_contrast(value, factor)
# log(...): write to the node log
outputs["out1"] = msg.data
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
# show_image(value, title): display an image data URL inside the node
# show_video(value, title): display a video data URL inside the node
# show_plot(series, title): display a compact graph inside the node
# show_text(value, title): display generic data inside the node
# image_grayscale(value), image_resize(value, width, height)
# image_blur(value, radius), image_brightness(value, factor), image_contrast(value, factor)
# log(...): write to the node log
while has_input("in1"):
    msg = take("in1")
    outputs["out1"] = msg.data
```

## lwrclpy Integration

`scripts/install_lwrclpy.py` installs the matching `lwrclpy` wheel for the current OS and Python version from GitHub Releases. The app uses the `rclpy`-compatible API provided by `lwrclpy` and subscribes/publishes using the topic/service names stored on graph edges.
