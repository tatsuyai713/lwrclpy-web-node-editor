# lwrclpy Web Node Editor

[English README](README.md)

ブラウザ上でノードをつなぎ、画像・動画・数値・ROS 2互換メッセージを処理できるノードエディタです。Python / `lwrclpy` ノードとC++ / `lwrcl` ノードを同じグラフで実行できます。

ROS 2本体のインストールは不要です。

<img width="1407" height="902" alt="lwrclpy Web Node Editor" src="https://github.com/user-attachments/assets/e570e712-fc23-4d9a-8852-60a61db4d8ca" />

## 動作手順

最初はC++環境を用意せず、Pythonノードだけで起動確認します。次の手順を上から順に実行してください。

### 1. PythonとGitを入れる

必要なものは次の2つです。

- 64-bit版 Python 3.13
- Git

#### Windows

1. [Python 3.13 for Windows](https://www.python.org/downloads/windows/)をインストールします。
2. Pythonインストーラーで **Python Launcher** を有効にします。
3. [Git for Windows](https://git-scm.com/download/win)をインストールします。
4. PowerShellを新しく開き、次を確認します。

```powershell
py -3.13 --version
git --version
```

両方のバージョンが表示されれば準備完了です。

#### macOS

[Homebrew](https://brew.sh/)をインストールした後、ターミナルで実行します。

```bash
brew install python@3.13 git
python3.13 --version
git --version
```

#### Ubuntu Linux

Gitと基本パッケージをインストールします。

WSLを使用している場合は、ここではGitだけを入れてリポジトリを取得し、次の「WSLの場合」に進んでください。

```bash
sudo apt update
sudo apt install -y git curl python3-venv
```

利用中のUbuntuにPython 3.13パッケージがある場合:

```bash
sudo apt install -y python3.13 python3.13-venv
```

`python3.13` パッケージがない場合は、`uv`を使ってPython 3.13をインストールできます。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
"$HOME/.local/bin/uv" python install 3.13
"$HOME/.local/bin/uv" python update-shell
```

ターミナルを開き直し、確認します。

```bash
python3.13 --version
git --version
```

### 2. このリポジトリを取得する

まだ取得していない場合だけ実行します。

```bash
git clone https://github.com/tatsuyai713/lwrclpy-web-node-editor.git
cd lwrclpy-web-node-editor
```

すでにこのREADMEをローカルで開いている場合は、このREADMEがあるディレクトリへ移動するだけで構いません。

### 3. WSLの場合

Ubuntu WSLでは、リポジトリのルートで次を実行します。

```bash
bash scripts/setup_wsl.sh
source .venv/bin/activate
python main.py --host 127.0.0.1 --port 8765
```

このスクリプトは次を自動で行います。

- WSL環境の確認
- Git、`venv`、`tkinter`などのUbuntuパッケージ導入
- Python 3.13の検出または`uv`による導入
- `.venv`の作成
- `requirements.txt`と`lwrclpy`のインストール
- Windowsファイル選択ダイアログ連携の確認

C++ノードも使う場合は、初回セットアップを次のように実行します。FastDDSと`lwrcl`のビルドを含むため時間がかかります。

```bash
bash scripts/setup_wsl.sh --with-cpp
source .venv/bin/activate
export LWRCL_PREFIX="$PWD/.local/fast-dds-libs"
export DDS_PREFIX="$PWD/.local/fast-dds"
python main.py --host 127.0.0.1 --port 8765
```

WSLを使用していない場合は、次へ進みます。

### 4. インストールして起動する

#### Windows

PowerShellで、このREADMEがあるディレクトリから実行します。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\install_lwrclpy.py
python main.py --host 127.0.0.1 --port 8765
```

`Activate.ps1` の実行が無効というエラーが出た場合は、そのPowerShellだけで一時的に許可してからもう一度activateします。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

#### macOS / Linux

ターミナルで、このREADMEがあるディレクトリから実行します。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/install_lwrclpy.py
python main.py --host 127.0.0.1 --port 8765
```

次の表示が出れば起動成功です。

```text
lwrclpy Web Node Editor: http://127.0.0.1:8765
```

ブラウザで [http://127.0.0.1:8765](http://127.0.0.1:8765) を開いてください。

### 5. サンプルを実行する

1. 画面上部の `Load` を押します。
2. `samples/image_video/02_video_motion_topic_graph.json` を選びます。
3. `Run` を押します。
4. 動画、処理画像、グラフが動くことを確認します。
5. 終了するときは `Stop` を押します。

サーバー自体を終了するときは、起動したターミナルで `Ctrl+C` を押します。

### 次回の起動

初回のインストールコマンドを繰り返す必要はありません。

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

## C++ノードを使う

Pythonノードだけを使う場合、この章は不要です。

C++ノードにはCMake、C++コンパイラ、FastDDS、`lwrcl`が必要です。`lwrcl`本体の対応環境と最新情報は、必ず[lwrclリポジトリ](https://github.com/tatsuyai713/lwrcl)を確認してください。

### Linux / macOS

先にC++ビルドツールをインストールします。

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

次に、このリポジトリのルートでセットアップスクリプトを実行します。

```bash
scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"
```

このスクリプトは[lwrcl](https://github.com/tatsuyai713/lwrcl)を取得し、FastDDS、メッセージ型、`lwrcl`を順にビルドします。完了後、同じターミナルで次を設定してサーバーを起動します。

```bash
export LWRCL_PREFIX="$PWD/.local/fast-dds-libs"
export DDS_PREFIX="$PWD/.local/fast-dds"
source .venv/bin/activate
python main.py --host 127.0.0.1 --port 8765
```

C++サンプルは `samples/cpp/` にあります。

### Windows

1. [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)をインストールします。
2. Visual Studio Installerで **Desktop development with C++** を選びます。
3. MSVC、Windows SDK、CMake toolsを含めます。
4. **Developer PowerShell for VS** を開き、`cl` と `cmake --version` を確認します。

さらに、Windows用にビルドされた `lwrcl` + FastDDSのinstall prefixが必要です。現在の[lwrcl公式ビルド手順](https://github.com/tatsuyai713/lwrcl)はLinux、macOS、QNX向けで、このリポジトリのセットアップスクリプトもBash用です。Linux/macOSで作ったライブラリはWindowsでは使用できません。

Windows用prefixを用意済みの場合は、Developer PowerShellで指定します。

```powershell
$env:LWRCL_PREFIX = "C:\lwrcl\fast-dds-libs"
$env:DDS_PREFIX = "C:\lwrcl\fast-dds"
$env:CPP_DEP_PREFIXES = "$env:LWRCL_PREFIX;$env:DDS_PREFIX"

Test-Path "$env:LWRCL_PREFIX\include\lwrcl.hpp"
Get-ChildItem "$env:LWRCL_PREFIX\lib\*lwrcl*"

.\.venv\Scripts\Activate.ps1
python main.py --host 127.0.0.1 --port 8765
```

`Test-Path` が `True` になり、`lwrcl`ライブラリが表示されることを確認してください。

## 主な機能

- 画像・動画・数値処理をノードで接続して実行
- Python / `lwrclpy` とC++ / `lwrcl` のカスタムノード
- 画像、グラフ、文字列、TF、PointCloud、Robot Modelの表示
- MCAP / rosbagの再生と記録
- 外部DDS topicとの入出力
- プロジェクトの保存、読込、CLI/ROS 2 packageへのExport

すぐに試せるプロジェクトは `samples/` にあります。全サンプルの説明は [samples/README.md](samples/README.md) を参照してください。

## 基本操作

- `Create Node`: カスタムノードを作成
- `Load`: 保存済みプロジェクトやサンプルを開く
- `Save`: 現在のプロジェクトを保存
- `Run`: グラフを継続実行
- `Run For`: 指定秒数だけ実行
- `Stop`: 実行とworkerプロセスを停止
- `Export CLI Package`: Web画面なしで実行できるZIPを作成

Pythonカスタムノードの `requirements.txt` にパッケージ名を書くと、ノード専用の `.node_envs/<node-id>` へ自動的にインストールされます。

## CLIファイルとしてExportして実行する

CLI Exportを使うと、作成したグラフをWeb画面なしで実行できます。別のPCへ移して実行することもできます。

### 1. CLI PackageをExportする

1. Web Node Editorで実行したいプロジェクトを開きます。
2. `Export CLI Package`を押します。
3. `<プロジェクト名>_cli_package.zip`がダウンロードされます。
4. ZIPをすべて展開します。ZIPの中から直接実行しないでください。
5. ターミナルで、展開後の`run_project.py`があるディレクトリへ移動します。

展開後の主なファイル:

```text
<project>_cli/
  run_project.py
  project.json
  requirements.txt
  README.md
  lwrclpy_web_node_editor/
```

### 2. 初回だけ実行環境を作る

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

コマンドは必ず`requirements.txt`と`run_project.py`があるディレクトリで実行してください。Export時に`lwrclpy` wheelが同梱されていない場合は、初回実行時に各ノード用の対応wheelをダウンロードするため、インターネット接続が必要です。

### 3. 実行する

停止するまで継続実行:

```bash
python run_project.py
```

10秒だけ実行:

```bash
python run_project.py --duration 10
```

実行中はノードの状態がターミナルへ表示されます。停止するときは`Ctrl+C`を押してください。

次回は、展開先へ移動して仮想環境をactivateした後、`python run_project.py`だけで実行できます。

### 別のPCで実行する場合

- 移動先にも64-bit版Python 3.13が必要です。
- `project.json`に動画、MCAP、URDFなどのパスが保存されている場合、そのファイルも移動してパスを修正してください。
- 外部DDS通信では、接続先と`ROS_DOMAIN_ID`、ネットワーク、QoSを合わせてください。
- 実行時のノード環境とログは、展開先の`.node_envs/`と`.node_workers/`に作成されます。

### C++ノードを含む場合

先に「C++ノードを使う」の手順で[lwrcl](https://github.com/tatsuyai713/lwrcl)とFastDDSを用意します。Export ZIPに含まれる`README.md`の手順に従い、`build_cpp_nodes.sh`でC++ノードをビルドしてください。Python runnerとC++実行ファイルは、同じDDS topic名で通信します。

## トラブルシュート

### Pythonが見つからない

Windows:

```powershell
py -3.13 --version
```

macOS / Linux:

```bash
python3.13 --version
```

バージョンが表示されない場合は「PythonとGitを入れる」へ戻り、インストール後に新しいターミナルを開いてください。

### `lwrclpy`をインストールできない

Pythonのバージョン、OS、CPUに一致するwheelが必要です。次を確認してください。

```bash
python3.13 -c "import platform; print(platform.platform(), platform.machine())"
```

ローカルwheelを使う場合:

```bash
python main.py --lwrclpy-wheel /path/to/lwrclpy.whl
```

先に各OSの方法で `.venv` をactivateしてから実行してください。

### WSLでファイル選択が開かない

WSLでは`tkinter`を使用せず、Windows PowerShellのファイルダイアログを開いて、選択結果をWSLパスへ変換します。次の2つが成功することを確認してください。

```bash
powershell.exe -NoProfile -Command '$PSVersionTable.PSVersion'
wslpath -u 'C:\Windows'
```

`powershell.exe`が見つからない場合は、WSLのWindows実行ファイル連携が無効になっています。`tkinter`はpipパッケージではないため、`requirements.txt`への追加では解決しません。

### ポート8765を使用できない

別のアプリが使用している場合は、別ポートで起動します。

```bash
python main.py --host 127.0.0.1 --port 8766
```

### C++ノードがビルドできない

- `cmake not found`: `cmake --version` を確認します。
- Windowsでコンパイラが見つからない: Developer PowerShell for VSから起動します。
- `lwrcl.hpp` / `fastrtps` が見つからない: `LWRCL_PREFIX` と `DDS_PREFIX` を確認します。
- 詳細ログ: `.node_workers/<node-id>.cpp.log`

## 開発者向け

ここから先は、このアプリ自体の開発・配布を行う場合の情報です。通常の利用者は読む必要がありません。

システム全体の構成とプロセス・通信・workerの詳細は [ARCHITECTURE.md](ARCHITECTURE.md) を参照してください。

### 実行構成

- サーバー: `main.py` / `lwrclpy_web_node_editor/server.py`
- グラフ実行: `lwrclpy_web_node_editor/graph.py`
- Web UI: `lwrclpy_web_node_editor/static/`
- Python worker: ノードごとの `.node_envs/<node-id>`
- C++ worker: `.node_workers/cpp/<node-id>`

グラフ内の接続はDDS topicとして実行されます。サーバー終了時には、このアプリが起動したworkerプロセスも終了します。

### `scripts/`の使い方

すべてリポジトリのルートから実行します。Pythonスクリプトを実行する前は `.venv` をactivateしてください。

| ファイル | いつ使うか |
| --- | --- |
| `setup_wsl.sh` | WSLの初回環境構築 |
| `install_lwrclpy.py` | 対応する`lwrclpy` wheelを現在の環境へ入れる |
| `setup_lwrcl_cpp_env.sh` | Linux/macOS/WSLでC++ノード環境を構築 |
| `build_linux_standalone.sh` | Linux配布アプリを作成 |
| `build_macos_standalone.sh` | macOS配布アプリを作成 |
| `build_windows_standalone.ps1` | Windows配布アプリを作成 |
| `repair_lwrclpy_windows_wheel.py` | Windows App Control向けwheelを修復 |
| `verify_standalone_bundle.py` | 作成済み配布アプリの内容を検査 |
| `relocate_cpp_prefix.py` | コピーしたC++ prefix内の絶対パスを修正 |
| `bundle_linux_cpp_runtime.py` | Linuxビルドから呼ばれるGCC runtime同梱helper |

#### WSLをセットアップする

通常のPythonノードだけを使う場合:

```bash
bash scripts/setup_wsl.sh
```

C++ノードも使う場合:

```bash
bash scripts/setup_wsl.sh --with-cpp
```

#### `lwrclpy`をインストールする

OS、Python、CPUに合う最新wheelをGitHub Releasesから選んでインストールします。

```bash
source .venv/bin/activate
python scripts/install_lwrclpy.py
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\install_lwrclpy.py
```

ローカルwheelを入れる場合は環境変数で指定します。

```bash
LWRCLPY_LOCAL_WHEEL=/path/to/lwrclpy.whl python scripts/install_lwrclpy.py
```

```powershell
$env:LWRCLPY_LOCAL_WHEEL = "C:\path\to\lwrclpy.whl"
python scripts\install_lwrclpy.py
```

#### C++ノード環境を構築する

Linux/macOS/WSL用です。既定ではFastDDS、Fast-DDS-Gen、メッセージ型、`lwrcl`を `/opt` 以下へ構築します。

```bash
bash scripts/setup_lwrcl_cpp_env.sh
```

管理者権限なしでリポジトリ内へ構築する場合:

```bash
bash scripts/setup_lwrcl_cpp_env.sh \
  --prefix "$PWD/.local/fast-dds-libs" \
  --dds-prefix "$PWD/.local/fast-dds"
```

利用できる全オプション:

```bash
bash scripts/setup_lwrcl_cpp_env.sh --help
```

#### スタンドアロンアプリを作成する

先にNode.js/npmをインストールし、`.venv`をactivateします。

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

生成物は `dist/` に作られます。別OS向けの成果物はクロスビルドせず、対象OS上で作成します。

#### スタンドアロン成果物を検査する

```bash
python scripts/verify_standalone_bundle.py dist/lwrclpy-web-node-editor
```

C++ prefixの同梱も必須として検査する場合:

```bash
python scripts/verify_standalone_bundle.py \
  dist/lwrclpy-web-node-editor \
  --require-cpp-prefix
```

macOSでは `.app`、Windowsでは生成されたアプリディレクトリを第1引数に指定できます。

#### C++ prefixを再配置可能にする

別の場所へコピーしたprefix内の壊れた絶対symlink、CMakeパス、pkg-configパスを相対参照へ修正します。通常は各スタンドアロンビルドスクリプトから自動実行されます。

```bash
python scripts/relocate_cpp_prefix.py /path/to/copied/lwrcl_cpp
```

表示を抑える場合:

```bash
python scripts/relocate_cpp_prefix.py /path/to/copied/lwrcl_cpp --quiet
```

#### Linux C++ runtime helper

`bundle_linux_cpp_runtime.py`はLinuxスタンドアロンビルド専用の内部helperです。conda-forgeから `libstdc++` と `libgcc` runtimeを取得してPyInstallerの `_internal` へ入れます。通常は直接実行せず、`build_linux_standalone.sh`に任せてください。

手動で検証するときだけ次を使います。

```bash
python scripts/bundle_linux_cpp_runtime.py \
  dist/lwrclpy-web-node-editor/resources/lwrclpy-web-node-editor-server/_internal
```

### Windows向けlwrclpy wheel

App Control / Code Integrity環境向けwheelを配布する場合:

```powershell
python scripts\repair_lwrclpy_windows_wheel.py path\to\lwrclpy.whl `
  -o path\to\lwrclpy.repaired.whl
```

この処理はOpenSSL DLLをPython公式配布の署名済みDLLへ置き換え、wheelの `RECORD` を更新します。

### Export

`Export CLI Package`の利用手順は、通常利用者向けの「CLIファイルとしてExportして実行する」を参照してください。

`Export ROS 2 Package` は通常のROS 2 `rclpy` package形式を生成します。Web表示専用ノードはExport先の実行対象から除外されます。

## 関連ファイル

- [samples/README.md](samples/README.md): サンプル一覧
- [ARCHITECTURE.md](ARCHITECTURE.md): プロセス、通信、worker、配布構成
- `scripts/`: 上記「`scripts/`の使い方」を参照
