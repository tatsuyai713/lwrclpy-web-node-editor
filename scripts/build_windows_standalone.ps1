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
$TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$RequirementsPath = Join-Path $TempRoot "requirements-windows-standalone.txt"
Get-Content requirements.txt |
  Where-Object { $_ -notmatch "^\s*pywebview\b" } |
  Set-Content -Encoding utf8 $RequirementsPath
if ($IsArm64) {
  Write-Warning "opencv-python-headless has no Windows arm64 wheel; building the Windows arm64 app without bundled OpenCV."
  $RequirementsPath = Join-Path $TempRoot "requirements-windows-arm64.txt"
  Get-Content (Join-Path $TempRoot "requirements-windows-standalone.txt") |
    Where-Object { $_ -notmatch "^\s*opencv-python-headless\b" } |
    Set-Content -Encoding utf8 $RequirementsPath
}

Invoke-Checked -FilePath $PythonBin -Arguments @(
  "-m", "pip", "install",
  "--prefer-binary",
  "--progress-bar", "off",
  "-r", $RequirementsPath,
  "pyinstaller"
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
if (Test-Path dist-electron) { Remove-Item dist-electron -Recurse -Force }

$BackendName = "$AppName-server"

$Args = @(
  "--noconfirm",
  "--clean",
  "--name", $BackendName,
  "--onedir",
  "--collect-all", "lwrclpy",
  "--collect-all", "rclpy",
  "--collect-all", "fastdds",
  "--collect-all", "mcap",
  "--collect-all", "mcap_ros2",
  "--hidden-import", "yaml",
  "--exclude-module", "PyQt5",
  "--exclude-module", "PyQt6",
  "--exclude-module", "PySide2",
  "--exclude-module", "PySide6",
  "--exclude-module", "webview",
  "--exclude-module", "pythonnet",
  "--exclude-module", "clr",
  "--exclude-module", "clr_loader",
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
    "--hidden-import", "cv2"
  ) + $Args
}

if ($UvBin) {
  $Args = @("--add-binary", "$UvBin;.") + $Args
}

Invoke-Checked -FilePath $PythonBin -Arguments (@("-m", "PyInstaller") + $Args)
Invoke-Checked -FilePath (Join-Path $RootDir "dist\$BackendName\$BackendName.exe") -Arguments @("--server-import-check")

$ElectronArch = if ($IsArm64) { "arm64" } else { "x64" }
Invoke-Checked -FilePath "npm" -Arguments @("install", "--prefix", "electron", "--no-save", "electron@latest", "@electron/packager@latest", "extract-zip@latest")
$ElectronVersion = (& node -p "require('./electron/node_modules/electron/package.json').version").Trim()
Invoke-Checked -FilePath "electron\node_modules\.bin\electron-packager.cmd" -Arguments @(
  "electron",
  $AppName,
  "--platform=win32",
  "--arch=$ElectronArch",
  "--electron-version=$ElectronVersion",
  "--out=dist-electron",
  "--overwrite",
  "--asar=false",
  "--executable-name=$AppName",
  "--extra-resource=dist\$BackendName"
)
Move-Item -Path (Join-Path $RootDir "dist-electron\$AppName-win32-$ElectronArch") -Destination (Join-Path $RootDir "dist\$AppName")
if (-not (Test-Path (Join-Path $RootDir "dist\$AppName\$AppName.exe"))) {
  throw "Electron Windows executable was not created."
}
if (-not (Test-Path (Join-Path $RootDir "dist\$AppName\resources\$BackendName\$BackendName.exe"))) {
  throw "Electron Windows backend executable was not bundled."
}

Write-Host ""
Write-Host "Build complete: $RootDir\dist\$AppName"
Write-Host "Run desktop app: $RootDir\dist\$AppName\$AppName.exe"
Write-Host "Run server mode: $RootDir\dist\$AppName\resources\$BackendName\$BackendName.exe --server --host 127.0.0.1 --port 8765"
