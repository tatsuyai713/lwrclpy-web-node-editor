$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$AppName = "lwrclpy-web-node-editor"
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { Join-Path $RootDir "venv\Scripts\python.exe" }

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$Arguments
  )
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$FilePath failed with exit code $LASTEXITCODE"
  }
}

if (-not (Test-Path $PythonBin)) {
  Write-Error "Python executable not found: $PythonBin`nSet PYTHON_BIN to your python.exe path and retry."
}

Invoke-Checked -FilePath $PythonBin -Arguments @("-m", "pip", "install", "--upgrade", "pip")

$Machine = (& $PythonBin -c "import platform; print(platform.machine().lower())").Trim()
$IsArm64 = @("arm64", "aarch64") -contains $Machine
$RequirementsPath = "requirements.txt"
if ($IsArm64) {
  Write-Warning "opencv-python-headless has no Windows arm64 wheel; building the Windows arm64 app without bundled OpenCV."
  $TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
  $RequirementsPath = Join-Path $TempRoot "requirements-windows-arm64.txt"
  Get-Content requirements.txt |
    Where-Object { $_ -notmatch "^\s*opencv-python-headless\b" } |
    Set-Content -Encoding utf8 $RequirementsPath
}

Invoke-Checked -FilePath $PythonBin -Arguments @(
  "-m", "pip", "install",
  "--prefer-binary",
  "--progress-bar", "off",
  "-r", $RequirementsPath,
  "pyinstaller",
  "PySide6"
)

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
  "--hidden-import", "yaml",
  "--hidden-import", "PySide6.QtCore",
  "--hidden-import", "PySide6.QtGui",
  "--hidden-import", "PySide6.QtNetwork",
  "--hidden-import", "PySide6.QtWidgets",
  "--exclude-module", "PyQt5",
  "--exclude-module", "PyQt6",
  "--exclude-module", "PySide2",
  "--exclude-module", "PySide6.QtQml",
  "--exclude-module", "PySide6.QtQuick",
  "--exclude-module", "PySide6.QtQuickWidgets",
  "--exclude-module", "PySide6.QtDesigner",
  "--exclude-module", "PySide6.QtDBus",
  "--add-data", "lwrclpy_web_node_editor/static;lwrclpy_web_node_editor/static",
  "--add-data", "lwrclpy_web_node_editor/node_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/video_dds_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/dds_tap_worker.py;lwrclpy_web_node_editor",
  "--add-data", "lwrclpy_web_node_editor/builtin_source_worker.py;lwrclpy_web_node_editor",
  "--add-data", "scripts/install_lwrclpy.py;scripts",
  "--add-data", "resources/fastdds.xml;.",
  "--add-data", ".app_settings/custom_nodes;custom_nodes",
  "--add-data", "samples;samples",
  "main.py"
)

if (-not $IsArm64) {
  $Args = @(
    "--hidden-import", "cv2",
    "--hidden-import", "PySide6.QtWebEngineCore",
    "--hidden-import", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "webview",
    "--exclude-module", "pythonnet",
    "--exclude-module", "clr"
  ) + $Args
} else {
  $Args = @(
    "--exclude-module", "webview",
    "--exclude-module", "pythonnet",
    "--exclude-module", "clr",
    "--exclude-module", "clr_loader"
  ) + $Args
}

if ($UvBin) {
  $Args = @("--add-binary", "$UvBin;.") + $Args
}

Invoke-Checked -FilePath $PythonBin -Arguments (@("-m", "PyInstaller") + $Args)

if ($IsArm64) {
  $AppDir = Join-Path $RootDir "dist\$AppName"
  $BackendExe = Join-Path $AppDir "$AppName-backend.exe"
  Move-Item -Path (Join-Path $AppDir "$AppName.exe") -Destination $BackendExe -Force

  $Dotnet = (Get-Command dotnet -ErrorAction Stop).Source
  $LauncherPublishDir = Join-Path $RootDir "build\windows-launcher-publish"
  if (Test-Path $LauncherPublishDir) { Remove-Item $LauncherPublishDir -Recurse -Force }
  Invoke-Checked -FilePath $Dotnet -Arguments @(
    "publish",
    "desktop_windows_launcher\LwrclpyWebNodeEditor.Launcher.csproj",
    "-c", "Release",
    "-r", "win-arm64",
    "--self-contained", "true",
    "-p:PublishSingleFile=true",
    "-p:IncludeNativeLibrariesForSelfExtract=true",
    "-p:PublishDir=$LauncherPublishDir"
  )
  Copy-Item -Path (Join-Path $LauncherPublishDir "$AppName.exe") -Destination (Join-Path $AppDir "$AppName.exe") -Force
  $WebViewLoader = Join-Path $LauncherPublishDir "WebView2Loader.dll"
  if (Test-Path $WebViewLoader) {
    Copy-Item -Path $WebViewLoader -Destination $AppDir -Force
  }
}

Invoke-Checked -FilePath (Join-Path $RootDir "dist\$AppName\$AppName.exe") -Arguments @("--desktop-import-check")

Write-Host ""
Write-Host "Build complete: $RootDir\dist\$AppName"
Write-Host "Run desktop app: $RootDir\dist\$AppName\$AppName.exe"
Write-Host "Run server mode: $RootDir\dist\$AppName\$AppName.exe --server --host 127.0.0.1 --port 8765"
