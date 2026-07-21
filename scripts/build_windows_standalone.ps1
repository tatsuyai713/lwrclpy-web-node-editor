$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$AppName = "lwrclpy-web-node-editor"
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { Join-Path $RootDir "venv\Scripts\python.exe" }

if (-not (Test-Path $PythonBin)) {
  Write-Error "Python executable not found: $PythonBin`nSet PYTHON_BIN to your python.exe path and retry."
}

& $PythonBin -m pip install --upgrade pip
& $PythonBin -m pip install --prefer-binary --progress-bar off -r requirements.txt pyinstaller PySide6

$UvCandidates = @(
  (Join-Path (Split-Path $PythonBin -Parent) "uv.exe"),
  (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
  (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
)

$UvBin = $null
foreach ($candidate in $UvCandidates) {
  if ($candidate -and (Test-Path $candidate)) {
    $UvBin = $candidate
    break
  }
}

if (-not $UvBin) {
  try {
    $command = Get-Command uv.exe -ErrorAction Stop
    $UvBin = $command.Source
  } catch {
  }
}

if (-not $UvBin) {
  Write-Warning "uv not found - lwrclpy auto-update will require uv to be on PATH at runtime."
} else {
  Write-Host "Bundling uv from: $UvBin"
}

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

$Args = @(
  "--noconfirm",
  "--clean",
  "--name", $AppName,
  "--onedir",
  "--collect-all", "lwrclpy",
  "--collect-all", "rclpy",
  "--collect-all", "fastdds",
  "--collect-all", "mcap",
  "--collect-all", "mcap_ros2",
  "--hidden-import", "cv2",
  "--hidden-import", "yaml",
  "--add-data", "lwrclpy_web_node_editor/static;lwrclpy_web_node_editor/static",
  "--add-data", "lwrclpy_web_node_editor/node_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/video_dds_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/dds_tap_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/builtin_source_worker.py;lwrclpy_web_node_editor",
  "--add-data", "scripts/install_lwrclpy.py;scripts",
  "--add-data", "resources/fastdds.xml;.",
  "--add-data", ".app_settings/custom_nodes;custom_nodes",
  "main.py"
)

if ($UvBin) {
  $Args = @("--add-binary", "$UvBin;.") + $Args
}

& $PythonBin -m PyInstaller @Args

Write-Host ""
Write-Host "Build complete: $RootDir\dist\$AppName"
Write-Host "Run desktop app: $RootDir\dist\$AppName\$AppName.exe"
Write-Host "Run server mode: $RootDir\dist\$AppName\$AppName.exe --server --host 127.0.0.1 --port 8765"
