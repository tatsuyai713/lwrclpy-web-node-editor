const { app, BrowserWindow, dialog } = require('electron');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');

const APP_NAME = 'lwrclpy-web-node-editor';
const BACKEND_NAME = 'lwrclpy-web-node-editor-server';
const BACKEND_EXE_NAME = process.platform === 'win32' ? `${BACKEND_NAME}.exe` : BACKEND_NAME;
let backendProcess = null;
let appUrl = null;
let extractedBackendDir = null;
let backendExitError = null;
let backendLog = '';

function configurePlatformRendering() {
  if (process.platform !== 'win32' || process.arch !== 'arm64') {
    return;
  }

  app.disableHardwareAcceleration();

  const switches = [
    ['disable-gpu'],
    ['disable-gpu-compositing'],
    ['disable-gpu-sandbox'],
    ['disable-gpu-watchdog'],
    ['disable-accelerated-2d-canvas'],
    ['disable-accelerated-video-decode'],
    ['enable-unsafe-swiftshader'],
    ['disable-features', 'UseSkiaRenderer,VizDisplayCompositor,CanvasOopRasterization,VaapiVideoDecoder'],
    ['in-process-gpu'],
    ['use-angle', 'warp'],
  ];
  for (const [name, value] of switches) {
    if (value === undefined) {
      app.commandLine.appendSwitch(name);
    } else {
      app.commandLine.appendSwitch(name, value);
    }
  }
}

configurePlatformRendering();

function findFreePort(host) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = address && typeof address === 'object' ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function extractBackendZip(zipPath) {
  const extractZip = require('extract-zip');
  const targetDir = fs.mkdtempSync(path.join(os.tmpdir(), `${BACKEND_NAME}-`));
  await extractZip(zipPath, { dir: targetDir });
  extractedBackendDir = targetDir;
  const executable = path.join(targetDir, BACKEND_NAME, BACKEND_EXE_NAME);
  if (!fs.existsSync(executable)) {
    throw new Error(`Backend executable not found after extracting ${zipPath}`);
  }
  if (process.platform !== 'win32') {
    fs.chmodSync(executable, 0o755);
  }
  return executable;
}

async function backendExecutable() {
  const candidates = [
    path.join(process.resourcesPath, BACKEND_NAME, BACKEND_EXE_NAME),
    path.join(__dirname, '..', 'dist', BACKEND_NAME, BACKEND_EXE_NAME),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  const zipCandidates = [
    path.join(process.resourcesPath, `${BACKEND_NAME}.zip`),
    path.join(__dirname, '..', 'dist', `${BACKEND_NAME}.zip`),
  ];
  for (const candidate of zipCandidates) {
    if (fs.existsSync(candidate)) {
      return extractBackendZip(candidate);
    }
  }
  throw new Error(`Backend executable not found: ${BACKEND_EXE_NAME}`);
}

function requestJson(url, options = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, options, (res) => {
      res.resume();
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve();
        } else {
          reject(new Error(`HTTP ${res.statusCode}`));
        }
      });
    });
    req.on('error', reject);
    req.setTimeout(1500, () => req.destroy(new Error('timeout')));
    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backendExitError) {
      throw backendExitError;
    }
    try {
      await requestJson(`${url}/api/health`);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  const details = backendLog.trim();
  throw new Error(
    `Backend did not become ready: ${lastError ? lastError.message : 'timeout'}`
    + (details ? `\n\nBackend log:\n${details.slice(-4000)}` : ''),
  );
}

async function stopBackend() {
  if (!backendProcess) {
    return;
  }
  if (appUrl) {
    try {
      await requestJson(`${appUrl}/api/stop`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{"force":true}',
      });
    } catch (_) {
    }
  }
  try {
    backendProcess.kill();
  } catch (_) {
  }
  backendProcess = null;
  if (extractedBackendDir) {
    try {
      fs.rmSync(extractedBackendDir, { recursive: true, force: true });
    } catch (_) {
    }
    extractedBackendDir = null;
  }
}

async function createWindow() {
  const host = '127.0.0.1';
  const port = await findFreePort(host);
  const backend = await backendExecutable();
  backendExitError = null;
  backendLog = '';
  appUrl = `http://${host}:${port}`;
  backendProcess = childProcess.spawn(
    backend,
    ['--server', '--host', host, '--port', String(port)],
    {
      cwd: path.dirname(backend),
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  const appendBackendLog = (chunk) => {
    backendLog += String(chunk);
    if (backendLog.length > 12000) {
      backendLog = backendLog.slice(-12000);
    }
  };
  if (backendProcess.stdout) {
    backendProcess.stdout.on('data', appendBackendLog);
  }
  if (backendProcess.stderr) {
    backendProcess.stderr.on('data', appendBackendLog);
  }
  backendProcess.on('error', (error) => {
    backendExitError = new Error(`Backend failed to start: ${error.message}`);
    backendProcess = null;
  });
  backendProcess.on('exit', (code, signal) => {
    if (appUrl) {
      const details = backendLog.trim();
      backendExitError = new Error(
        `Backend exited before the UI was ready: code=${code} signal=${signal}`
        + (details ? `\n\nBackend log:\n${details.slice(-4000)}` : ''),
      );
    }
    backendProcess = null;
  });

  await waitForServer(appUrl, 90000);

  const window = new BrowserWindow({
    title: 'lwrclpy Web Node Editor',
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.on('closed', () => {
    stopBackend();
  });
  await window.loadURL(appUrl);
}

app.whenReady().then(() => {
  createWindow().catch(async (error) => {
    await stopBackend();
    dialog.showErrorBox(APP_NAME, error && error.stack ? error.stack : String(error));
    app.quit();
  });
});

app.on('before-quit', () => {
  stopBackend();
});

app.on('window-all-closed', () => {
  app.quit();
});
