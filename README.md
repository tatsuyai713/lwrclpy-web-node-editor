# lwrclpy Web Node Editor

`lwrclpy Web Node Editor` は、ブラウザ上で画像処理・動画処理・数値処理のノードグラフを作り、`lwrclpy` のAPIに近い書き方で各ノードのPythonコードを編集・実行・保存・エクスポートできるWebアプリです。

ROS 2本体のインストールは不要です。`lwrclpy` が提供する `rclpy` 互換APIを使い、ノードコードは `msg` を受け取って `publish(...)` で出力するcallbackスタイルで書けます。

## できること

- ブラウザでノードを作成し、入力・処理・表示・グラフ化・topic出力を接続できます。
- `Image File Input` で画像を読み込み、`sensor_msgs/msg/Image` として処理できます。
- `Video File Input` で動画ファイルを選択し、Run中に `sensor_msgs/msg/Image` として流せます。
- `Image Viewer` で画像を表示できます。
- `String Viewer` で `std_msgs/msg/String` の最新内容を表示したり、LLMのストリーミング断片のような文字列を追記表示できます。
- `Graph Viewer` で `std_msgs/msg/Float32` などの数値や、メッセージ内の数値フィールドをプロットできます。
- `LLM Text` で `std_msgs/msg/String` のpromptをOllama/OpenAI/OpenAI互換API/LM Studioへ渡し、responseをtopicとして出力できます。
- `Topic Input` / `Topic Output` で、グラフ外部とのtopic境界を表現できます。
- カスタムノードごとに `requirements.txt` を持たせ、実行前に `.node_envs/<node-id>` のvenvへ依存をセットアップできます。
- プロジェクト全体をJSONとしてSave/Loadできます。
- カスタムノードを個別のPythonファイルとしてExport/Importできます。
- カスタムノード群をROS 2 Python package形式のzipとしてExportできます。中には各ノードのPythonファイルとlaunchファイルが含まれます。

## 実行方法

このREADMEがある `lwrclpy_web_node_editor` ディレクトリで実行します。

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/install_lwrclpy.py
.venv/bin/python main.py --host 127.0.0.1 --port 8765
```

ローカルでビルドした `lwrclpy` wheel を使う場合は、サーバー起動時に指定できます。指定した wheel はサーバー本体と各ノード専用 `.node_envs/<node-id>` の両方で使用されます。

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8765 \
  --lwrclpy-wheel /Users/tatsuyai/repos/lwrclpy/dist/lwrclpy-0.5.1-cp313-cp313-macosx_26_0_arm64.whl
```

## Linux向けスタンドアロンアプリ化

LinuxではPyInstallerで `onedir` 形式のスタンドアロン実行ファイルを作成できます。

1. 通常どおりvenvを準備し、依存を入れます。

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

1. ビルドスクリプトを実行します。

```bash
scripts/build_linux_standalone.sh
```

1. 生成物を起動します（外部ブラウザ不要、アプリ内にWebUIを表示）。

```bash
dist/lwrclpy-web-node-editor/lwrclpy-web-node-editor
```

従来どおりHTTPサーバーとして起動したい場合は次を使います。

```bash
dist/lwrclpy-web-node-editor/lwrclpy-web-node-editor --server --host 127.0.0.1 --port 8765
```

デスクトップ起動時は内部サーバーを別プロセスでlocalhost起動し、アプリ内WebViewから接続します。

```text
lwrclpy Web Node Editor Desktop: http://127.0.0.1:<auto-port>
```

スタンドアロン実行時の作業ディレクトリは既定で次になります。

```text
~/.local/share/lwrclpy-web-node-editor
```

変更したい場合は起動前に次を設定してください。

```bash
export LWRCLPY_WEB_NODE_EDITOR_HOME=/path/to/workdir
```

サーバーモードで起動すると次のように表示されます。

```text
lwrclpy Web Node Editor: http://127.0.0.1:8765
```

サーバーモードではブラウザで `http://127.0.0.1:8765` を開いてください。

同じ作業ディレクトリではサーバー単一起動ロックが有効です。すでに起動中のサーバーがある状態で再起動すると、次のようなエラーで拒否されます。

```text
Another lwrclpy Web Node Editor server is already running (lock: .../server.lock). Stop it first before starting a new instance.
```

## macOS向けスタンドアロンアプリ化

macOSでは、macOS上で次のスクリプトを実行して `onedir` 形式を作成します。

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
scripts/build_macos_standalone.sh
```

署名付きでビルドする場合は、`MAC_CODESIGN_IDENTITY` を指定して実行します。

```bash
export MAC_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
scripts/build_macos_standalone.sh
```

必要な場合は `MAC_CODESIGN_ENTITLEMENTS=/path/to/entitlements.plist` も指定できます。

生成物の起動例:

```bash
dist/lwrclpy-web-node-editor/lwrclpy-web-node-editor
dist/lwrclpy-web-node-editor/lwrclpy-web-node-editor --server --host 127.0.0.1 --port 8765
```

## Windows向けスタンドアロンアプリ化

Windowsでは、Windows上でPowerShellから次を実行します。

```powershell
py -3.13 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows_standalone.ps1
```

生成物の起動例:

```powershell
dist\lwrclpy-web-node-editor\lwrclpy-web-node-editor.exe
dist\lwrclpy-web-node-editor\lwrclpy-web-node-editor.exe --server --host 127.0.0.1 --port 8765
```

補足:

- Linux/macOS/WindowsはそれぞれのOS上で個別にビルドしてください。
- 既定のPythonは `venv` 配下を参照します。別のPythonを使う場合は `PYTHON_BIN` を設定してください。

すでに8765番ポートを使っているサーバーがある場合は、止めるか別ポートで起動します。

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8766
```

## 最初に試す手順

1. サーバーを起動します。
2. ブラウザで `http://127.0.0.1:8765` を開きます。
3. `Load` を押して `samples/image_video/02_video_motion_topic_graph.json` を選びます。
4. `Run` を押します。
5. Video入力、処理後画像、motion scoreのグラフが動くことを確認します。
6. 止めるときは `Stop` を押します。

`Run Hz` で連続実行時のtick周波数を設定できます。デフォルトは30Hzです。`Run` の連続実行ループはサーバー側で動くため、ブラウザタブがフォーカスを失ってもTopic出力は継続します。`Duration sec` に秒数を入力して `Run For` を使うと、指定秒数だけ実行して自動停止できます。

## ショートカット

- `Ctrl+S` / `Cmd+S`: 上書き保存します。まだ保存先がない場合は保存先を選びます。
- `Ctrl+Shift+S` / `Cmd+Shift+S`: 名前を付けて保存します。
- `Ctrl+Z` / `Cmd+Z`: 元に戻します。
- `Ctrl+Shift+Z` / `Cmd+Shift+Z` または `Ctrl+Y`: やり直します。

ブラウザがFile System Access APIに対応していない場合、保存は従来通りJSONファイルのダウンロードになります。

## サンプルプロジェクト

`samples/` には、そのまま読み込んで実行できるサンプルJSONがあります。サンプルは `image_video/`, `signals/`, `external_topics/`, `custom_runtime/`, `deep_learning/`, `llm/` にジャンル別で整理しています。画像サンプルは埋め込み画像を持ち、動画サンプルは埋め込みフレームから簡易的な動画入力を生成するので、追加ファイルなしで動作確認できます。

- `image_video/01_image_edge_topic_graph.json`: 画像入力、グレースケール化、エッジ抽出、画像表示、エッジ強度グラフ、topic出力。
- `image_video/02_video_motion_topic_graph.json`: 動画フレーム入力、フレーム差分によるmotion mask、overlay表示、motion scoreグラフ、topic出力。
- `image_video/03_image_color_balance_topic_graph.json`: 画像入力、コントラスト補正、赤バランス処理、画像表示、red indexグラフ、topic出力。
- `image_video/04_video_low_light_colormap_topic_graph.json`: 暗い動画フレーム入力、ガンマ補正、疑似カラーマップ表示、輝度グラフ、topic出力。
- `image_video/05_image_crop_mosaic_topic_graph.json`: 画像入力、中央クロップ、モザイク処理、画像表示、平均輝度グラフ、topic出力。
- `signals/06_function_generator_signal_view.json`: Function Generatorからサイン波をGraph Viewerとtopic出力へ接続。
- `signals/07_function_generator_wave_suite.json`: サイン、ステップ、チャープ、ホワイトノイズのFunction Generatorを並べて表示。
- `external_topics/08_external_float_topic_graph.json`: 外部 `std_msgs/msg/Float32` topicをGraph Viewerで表示。
- `external_topics/09_external_image_topic_view_save.json`: 外部 `sensor_msgs/msg/Image` topicをImage ViewerとImage File Saveへ接続。
- `custom_runtime/10_multi_timer_counter_graph.json`: 1つのカスタムノードに複数Timerを持たせ、別々の周期で値をpublish。
- `custom_runtime/11_manual_subscriber_timer_sampler.json`: Subscriber callbackをOFFにし、Timer callbackから `latest()` で入力を読む例。
- `image_video/12_image_view_save_topic_output.json`: 埋め込み画像を表示、保存、topic出力境界へ接続。
- `deep_learning/13_mac_yolo_mps_detection_segmentation.json`: Video File Inputを2つのYOLOノードへ分岐し、検出とセグメンテーションの結果を表示。
- `deep_learning/13_ultralytics_yolo_detection_segmentation.json`: Video File InputをUltralytics YOLOの検出・インスタンスセグメンテーションノードへ分岐して表示。
- `deep_learning/14_ultralytics_yolo_pose_depth_anything.json`: Ultralytics YOLO PoseとDepth Anything V2の深度推定を表示。
- `deep_learning/15_cuda_ultralytics_yolo_detection_segmentation.json`: CUDA環境向けのUltralytics YOLO検出・インスタンスセグメンテーション。
- `deep_learning/16_tensorrt_ultralytics_yolo_engine_detection.json`: TensorRT engine向けのUltralytics YOLO検出。ノードの `weights` に `.engine` ファイルを指定します。
- `deep_learning/17_sam_midas_segmentation_depth.json`: Segment Anythingの自動マスクとMiDaS深度推定を表示。標準SAM checkpointは未配置の場合に自動取得します。
- `llm/18_ollama_llm_string_view.json`: promptを一度publishし、OllamaのLLM応答をString Viewerとtopic outputへ表示・出力します。Ollamaと `llama3.2` などのローカルモデルが必要です。

詳しい分類は `samples/README.md` を参照してください。カスタムノードを含むサンプルは、実行時にノードごとのvenvとworker processを使います。

サンプルを再生成する場合は次を実行します。

```bash
.venv/bin/python samples/generate_sample_projects.py
```

## Webプレビューの実行モデル

Webプレビューは、リンクごとのtopic名を使ってlwrclpy topic経由でノード間データを流します。

- グラフ内部の接続は、WebUI/サーバー内の直接データ受け渡しではなくlwrclpy topicで実行されます。
- カスタムノードのコードは `lwrclpy` のcallbackに近いスコープで実行されます。
- `Topic Input` と `Topic Output` は、グラフ外部との境界マーカーです。実際のPub/Subは接続された処理・表示ノードが行います。
- 画像入力は小さな埋め込みデータとして扱えます。動画入力はブラウザからアップロードせず、サーバー側のファイル選択ダイアログで選んだローカルファイルを独立workerがデコードしてDDS publishします。
- 連続実行のtick周波数は `Run Hz` で設定します。Run中のtickループはサーバー側threadで動くため、ブラウザのtimer throttlingには依存しません。設定値が高い場合でも、実効周波数はノード処理時間やlwrclpy/DDS処理時間に制限されます。

つまり、Webプレビューでもノード間通信の意味論はlwrclpy topicに寄せています。

## ノードの作り方

`Create Node` でカスタムノードを作成できます。

主な設定項目は次の通りです。

- `Inputs`: 入力ポートです。型は `sensor_msgs/msg/Image` や `std_msgs/msg/Float32` など、インストール済みlwrclpyメッセージから選びます。`Use Callback` はデフォルトONで、OFFにすると `latest()` / `take()` で読む手動入力になります。
- `Outputs`: 出力ポートです。
- `Callback Code`: 入力を受け取ったときに実行するコードです。通常の画像処理・動画処理はここに書きます。
- `Timer Callback`: 一定周期で実行したいコードです。`Timer Count` で複数のTimerを作成でき、各Timerは常にcallbackとして実行されます。
- `Import Code`: `import cv2` や `import numpy as np` など、ノード専用のimportを書きます。
- `requirements.txt`: ノード専用venvに入れる依存パッケージを書きます。

ポート同士は型が一致すると接続できます。1つの出力ポートから複数のノードへ接続した場合、その出力から出るtopic名は同じ名前に同期されます。

## Callback Codeの書き方

入力ポートの `Use Callback` がONの場合、その入力に値が届いたときにCallback Codeが実行されます。OFFの場合はCallback Codeを使わず、Main LoopやTimer Callbackから `latest()` / `take()` で入力を読みます。

Callback Codeで使える主な変数は次の通りです。

- `node`: lwrclpy互換のノードオブジェクトです。`node.get_logger().info(...)` が使えます。
- `input_id`: どの入力ポートに届いたかを表すIDです。
- `msg`: 届いたメッセージです。
- `request`: service入力の場合のrequestです。message入力では `msg` と同じ値です。
- `response`: service入力の場合のresponseです。
- `state`: ノードごとに保持される辞書です。前フレームや累積値を保存できます。
- `params`: ノードのパラメータ辞書です。
- GUIで設定したパラメータ名が有効なPython識別子の場合、同名の変数としても参照できます。たとえば `pointsPerSide` は `params.get("pointsPerSide")` でも `pointsPerSide` でも使えます。`msg` や `publish` など実行スコープの予約名と衝突する名前は直接変数化されません。
- `publish(output_id, value)`: 出力ポートへ値を出します。
- `log(...)`: ノードログへ文字列を出します。

例: `std_msgs/msg/String` を受け取って別ポートへ流す場合。

```python
node.get_logger().info(f"received {input_id}")
publish("out1", msg)
```

例: `sensor_msgs/msg/Image` 風の辞書を受け取り、RGBをグレースケール化して出力する場合。

```python
img = msg
data = img.get("data") or []
w = int(img.get("width") or 0)
h = int(img.get("height") or 0)
out = []

for i in range(0, len(data), 3):
    y = int(data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114)
    out.extend([y, y, y])

publish("gray", {
    "width": w,
    "height": h,
    "encoding": "rgb8",
    "is_bigendian": 0,
    "step": w * 3,
    "data": out,
})
```

複数入力を扱う場合は `state` に入力ごとの最新値を保存すると書きやすくなります。

```python
state[input_id] = msg
frame = state.get("frame")
mask = state.get("mask")

if frame and mask:
    publish("out1", frame)
```

複数の出力ポート（複数Publisher相当）を持つ場合は、同じCallback内で `publish(...)` を出力ポートごとに呼びます。必要に応じて、同じ入力から加工結果を分岐して別topicへ同時に出力できます。

```python
# out_raw: 元データ, out_norm: 正規化値, out_alert: しきい値判定結果
value = float(getattr(msg, "data", msg.get("data", 0.0)) if hasattr(msg, "data") or isinstance(msg, dict) else msg)
max_v = float(params.get("max", 100.0))
threshold = float(params.get("threshold", 0.8))

norm = 0.0 if max_v <= 0 else max(0.0, min(1.0, value / max_v))
is_alert = norm >= threshold

publish("out_raw", value)
publish("out_norm", norm)
publish("out_alert", is_alert)
```

各 `output_id` はノード定義のOutputsで追加したIDと一致させてください。未定義の `output_id` へ `publish` しても配線されません。

## Timer CallbackとMain Loop

Timer Callbackは、Run中に設定周期へ到達したときに実行されます。1ノードに複数のTimerを設定でき、Timerごとに `timer_id`, `timer_name`, `period` が渡されます。

```python
state["count"] = state.get("count", 0) + 1
publish("out1", str(state["count"]))
```

Main Loopは各tickで実行される任意処理です。データ入力に反応する処理はCallback Codeを使う方が、lwrclpyへエクスポートしやすくなります。

```python
if state.get("enabled", True):
    node.get_logger().info("tick")
```

## 画像・動画ノード

`Image File Input` はブラウザで選んだ画像を `sensor_msgs/msg/Image` として出力します。

- `One Shot`: 同じ画像を数tickだけ出力します。
- `Rate`: 指定Hzで画像を繰り返し出力します。

`Video File Input` はノード上の `Select Video` で動画ファイルを選択します。Path欄は選択結果の表示専用で、直接入力はできません。Run中は独立した `video_dds_worker.py` プロセスがOpenCVで動画をデコードし、動画ファイルのsource fpsに合わせて `sensor_msgs/msg/Image` としてTopic出力します。サンプルJSONの動画入力は実動画ファイルを持たないため、埋め込みフレームからサーバー側で簡易的な動画フレームを生成します。

`Image File Save` は接続された画像を `saved_images/` にBMPとして保存します。

## 信号生成ノード

`Function Generator` は `std_msgs/msg/Float32` の `data` として信号を出力します。ノード上の設定で `Step`, `Sine`, `Square`, `Ramp`, `Chirp`, `White Noise` を選べます。`Publish Hz` で出力周期を設定し、`Sample Time sec` で信号値のサンプル保持周期を設定できます。`DDS Topic` を設定すると、リンクやTopic Outputとは別に、そのDDS/lwrclpy topicへ直接publishします。`Graph Viewer` に接続し、`Field Path` を `data` にすると波形を確認できます。

`Graph Viewer` の `Graph Settings` では、保持サンプル数、表示する横軸の秒数、縦軸を `Auto` にするか固定範囲にするかを設定できます。保持サンプル数のデフォルトは10000です。

## Topic Input / Topic Output

`Topic Input` と `Topic Output` は、Webグラフの外側とlwrclpy topicで接続するための境界ノードです。これら自体は処理を持たず、実際のpublish/subscribeは接続された処理ノード、source worker、tap workerが行います。

- `Topic Input`: 外部topicから入る信号の入口を表します。接続先ノード側がsubscribeします。
- `Topic Output`: 外部topicへ出る信号の出口を表します。接続元ノード側がpublishします。
- topic名はエッジ名として表示・編集されます。
- 同じ出力ポートから出る複数エッジは、同じtopic名に同期されます。

## Export / Import

`Export Python Node` は、選択したカスタムノードを単体のPythonファイルとして保存します。後から `Import Python Node` で読み戻せます。

`Export ROS 2 Package` は、プロジェクト内のカスタムノードをノードごとのPythonファイルに分け、Python package形式のzipとして出力します。zipには次のファイルが含まれます。

- `package.xml`
- `setup.py`
- `setup.cfg`
- `<package>/<node>.py`
- `<package>/runtime.py`
- `launch/project.launch.py`
- `requirements.txt` が必要な場合は同梱

ブラウザ専用の組み込みツールノード、たとえば `Image File Input` や `Image Viewer` はエクスポート対象外です。エクスポートされるのは、ユーザーが作成したカスタムノードです。

## 依存関係とvenv

アプリ本体は `.venv` で動きます。カスタムノードは、ノードごとに `.node_envs/<node-id>` のvenvを持ちます。

ノードの `requirements.txt` に依存を書くと、実行前に `uv` がそのノード用venvを作成し、必要なパッケージとlwrclpyをインストールします。lwrclpyはGitHub Releasesの `latest` タグから、現在のPython ABIとOS/CPUに合うwheelを自動選択してインストールします。Webプレビューでは、カスタムノードごとに `.node_envs/<node-id>` のPythonで別ワーカープロセスを起動し、ノード間や組み込みツールノードとの接続はlwrclpy topicで橋渡しします。

`Stop` は実行中の全カスタムノードワーカーへ停止指示を送り、通常停止できない場合はサーバー側でforce-stopへエスカレーションします。UI側でもStop要求がタイムアウトした場合は自動で `Force Stop` を再送します。`Force Stop` は全カスタムノードワーカーを強制終了します。サーバー起動時と終了時にも、このフレームワークが起動した残存workerプロセスを検出して強制終了します。

## トラブルシュート

### ポートが使用中と表示される

別のサーバーが残っている可能性があります。別ポートで起動するか、古いプロセスを止めてください。

```bash
.venv/bin/python main.py --host 127.0.0.1 --port 8766
```

### サーバー重複起動エラーが出る

`Another lwrclpy Web Node Editor server is already running` が表示される場合、同じ作業コンテキストに既存サーバーが起動中です。既存プロセスを停止してから再起動するか、別の作業ディレクトリで起動してください。

### 動画が動かない

- `Run` を押して連続実行にしてください。1 tickだけでは動画は進みにくいです。
- 実動画を使う場合は、`Video File Input` ノードの `Select Video` でファイルを選択し、`Ready` 後に `Run` してください。
- サンプル動画は `samples/image_video/02_video_motion_topic_graph.json` または `samples/image_video/04_video_low_light_colormap_topic_graph.json` を読み込み、`Run` で動作確認できます。

### ノードの処理結果が出ない

- 入力ポートの型と出力ポートの型が一致しているか確認してください。
- Callback Codeでは `outputs["out1"] = ...` ではなく `publish("out1", value)` を使ってください。
- 画像処理ノードでは出力辞書に `width`, `height`, `encoding`, `step`, `data` が入っているか確認してください。

### 依存パッケージのimportに失敗する

カスタムノードの `requirements.txt` に依存を書き、Runしてください。`.node_envs/<node-id>` が作られ、依存がインストールされます。

## 関連ファイル

- `main.py`: サーバー/デスクトップ/workerの統合起動入口。
- `lwrclpy_web_node_editor/server.py`: HTTP APIと静的ファイル配信。
- `lwrclpy_web_node_editor/graph.py`: グラフ実行、画像変換、lwrclpy topic連携。
- `lwrclpy_web_node_editor/static/app.js`: ブラウザUI。
- `samples/generate_sample_projects.py`: サンプルJSON生成スクリプト。
- `scripts/install_lwrclpy.py`: 現在のOS/Pythonに合うlwrclpy wheelをインストールするスクリプト。
