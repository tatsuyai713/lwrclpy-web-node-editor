# Sample Projects

This directory contains loadable `lwrclpy Web Node Editor` project JSON files.

## How to run

1. Start the editor from the repository root directory:

   ```bash
   .venv/bin/python main.py --host 127.0.0.1 --port 8765
   ```

2. Open `http://127.0.0.1:8765`.
3. Click `Load` and select one of the JSON files under the genre folders in this directory.
4. Click `Run` or `Run For`.

The continuous run tick rate is fixed at 60 Hz in the current UI.

Image samples contain an embedded image. Video samples contain an embedded base frame that is animated during `Run` and published at each Video File Input node's `Publish Hz`. Custom-node samples create one worker process and one `.node_envs/<node-id>` virtual environment per custom node.

## Quick Start

- `signals/06_function_generator_signal_view.json`: built-in Function Generator publishes a sine wave over `/sample6/function_signal` to Graph Viewer and a Topic Output boundary.
- `image_video/02_video_motion_topic_graph.json`: embedded video-frame input, custom motion detection, image display, graph display, and Topic Output.
- `deep_learning/13_ultralytics_yolo_detection_segmentation.json`: platform-neutral Ultralytics YOLO detection and instance segmentation overlays.
- `deep_learning/17_sam_midas_segmentation_depth.json`: Segment Anything automatic masks and MiDaS depth overlay.
- `llm/18_ollama_llm_string_view.json`: one-shot prompt publisher into built-in LLM Text, with the response shown in String Viewer.
- `llm/20_interactive_llm_chat.json`: run-time prompt entry through Interactive Text Input into built-in LLM Text, with responses shown in Chat String Viewer.
- `signals/07_function_generator_wave_suite.json`: four built-in Function Generator nodes for sine, step, chirp, and white-noise signals.
- `cpp/21_cpp_low_pass_filter.json`: C++ custom node that low-pass filters a noisy `Float32` stream in the Subscribe Callback and publishes the result to Graph Viewer.

## Image And Video Processing

- `image_video/01_image_edge_topic_graph.json`: embedded image input, grayscale conversion, edge-strength filter, image display, edge-strength graph, and `/sample1/edge_strength` Topic Output.
- `image_video/02_video_motion_topic_graph.json`: embedded video-frame input, frame-difference motion mask, motion overlay display, motion-score graph, and `/sample2/motion_score` Topic Output.
- `image_video/03_image_color_balance_topic_graph.json`: embedded image input, contrast stretch, red-balance processing, image display, red-index graph, and `/sample3/red_index` Topic Output.
- `image_video/04_video_low_light_colormap_topic_graph.json`: embedded low-light video-frame input, gamma brightening, pseudo thermal colormap display, luminance graph, and `/sample4/luminance` Topic Output.
- `image_video/05_image_crop_mosaic_topic_graph.json`: embedded image input, center crop, mosaic privacy filter display, mean-intensity graph, and `/sample5/mosaic_mean` Topic Output.
- `image_video/12_image_view_save_topic_output.json`: embedded image input connected to Image Viewer, Image File Save, and a Topic Output boundary on `/sample12/save_input_image`.

## Signals And Graphs

- `signals/06_function_generator_signal_view.json`: built-in Function Generator sine signal at 100 Hz to Graph Viewer with a 5-second graph window and Topic Output.
- `signals/07_function_generator_wave_suite.json`: sine, step, chirp, and white-noise Function Generator nodes with fixed-axis Graph Viewer settings.

## External Topic Boundaries

- `external_topics/08_external_float_topic_graph.json`: Topic Input boundary for `/sample8/external_float` connected to Graph Viewer and Topic Output. Use this with an external `std_msgs/msg/Float32` publisher.
- `external_topics/09_external_image_topic_view_save.json`: Topic Input boundary for `/sample9/external_image` connected to Image Viewer and Image File Save. Use this with an external `sensor_msgs/msg/Image` publisher.

## Custom Node Runtime

- `custom_runtime/10_multi_timer_counter_graph.json`: one custom node with two Timer callbacks. The fast timer publishes `/sample10/fast_count`, and the slow timer publishes `/sample10/slow_count`.
- `custom_runtime/11_manual_subscriber_timer_sampler.json`: Function Generator publishes a square wave. A custom node has Subscriber callback disabled, reads the latest input from a Timer callback, and publishes raw and filtered signals.

## C++ Custom Nodes

These samples require `lwrcl` with the FastDDS backend plus `cmake` and a C++ compiler. During Web `Run`, each C++ node is generated and built under `.node_workers/cpp/<node-id>/`, then launched as a worker process.

In C++ custom nodes, `Header` is inserted after generated includes and can contain extra includes, helper functions, class definitions, structs, and static objects. `Initialize Code` runs once before Loop/Timer callbacks start, while Callback/Loop/Timer code runs in generated methods.

- `cpp/21_cpp_low_pass_filter.json`: Function Generator publishes white noise into a C++ low-pass filter node whose Subscribe Callback publishes the filtered value.
- `cpp/22_cpp_signal_mixer.json`: two Function Generator sources feed a C++ mixer node that publishes sum and difference streams.
- `cpp/23_cpp_image_threshold.json`: embedded image input feeds a C++ threshold node that publishes an image mask and a bright-pixel ratio.

## Deep Learning

- `deep_learning/13_ultralytics_yolo_detection_segmentation.json`: Video File Input fans out to platform-neutral Ultralytics YOLO object detection and instance segmentation nodes. The nodes select CUDA, MPS, or CPU automatically.
- `deep_learning/14_ultralytics_yolo_pose_depth_anything.json`: Video File Input fans out to Ultralytics YOLO Pose and Depth Anything V2 depth overlay nodes.
- `deep_learning/15_cuda_ultralytics_yolo_detection_segmentation.json`: CUDA-intended Ultralytics YOLO detection and instance segmentation. These nodes request CUDA first and use CPU only when CUDA is unavailable.
- `deep_learning/16_tensorrt_ultralytics_yolo_engine_detection.json`: TensorRT engine-oriented Ultralytics YOLO detection. Set the custom node `weights` parameter to a local `.engine` file.
- `deep_learning/17_sam_midas_segmentation_depth.json`: Video File Input fans out directly to Segment Anything automatic masks and MiDaS depth overlay.

## TF

- `tf/19_urdf_xacro_tf_static_merge_custom_tf.json`: URDF/Xacro static TF publisher and a custom node's TF output are combined through TF Merge and displayed in 3D Viewer. `simple_mobile_robot.urdf` in the same directory can be used as the URDF input.

## LLM

- `llm/18_ollama_llm_string_view.json`: a custom prompt node publishes one `std_msgs/msg/String` prompt to the built-in `LLM Text` node. The response is displayed by `String Viewer` and exposed through a Topic Output on `/sample18/llm_response`. Start Ollama locally and pull the configured model, for example `ollama pull llama3.2`, before running.
- `llm/20_interactive_llm_chat.json`: `Interactive Text Input` lets you edit prompts while running, publishes each submitted prompt as `std_msgs/msg/String` to `LLM Text`, clears the edit box after send, and `Chat String Viewer` displays each response as a chat message. Start Ollama locally and pull the configured model, for example `ollama pull llama3.2`, before running.

## Regenerate

The non-deep-learning JSON files are generated by `generate_sample_projects.py`:

```bash
.venv/bin/python samples/generate_sample_projects.py
```
