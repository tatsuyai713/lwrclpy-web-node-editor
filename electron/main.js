const { app, BrowserWindow, dialog } = require('electron');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const APP_NAME = 'lwrclpy-web-node-editor';
const BACKEND_NAME = 'lwrclpy-web-node-editor-server';
const BACKEND_EXE_NAME = process.platform === 'win32' ? `${BACKEND_NAME}.exe` : BACKEND_NAME;
let backendProcess = null;
let appUrl = null;

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

function backendExecutable() {
  const candidates = [
    path.join(process.resourcesPath, BACKEND_NAME, BACKEND_EXE_NAME),
    path.join(__dirname, '..', 'dist', BACKEND_NAME, BACKEND_EXE_NAME),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
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
    try {
      await requestJson(`${url}/api/health`);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error(`Backend did not become ready: ${lastError ? lastError.message : 'timeout'}`);
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
}

async function createWindow() {
  const host = '127.0.0.1';
  const port = await findFreePort(host);
  const backend = backendExecutable();
  backendProcess = childProcess.spawn(
    backend,
    ['--server', '--host', host, '--port', String(port)],
    {
      cwd: path.dirname(backend),
      detached: false,
      stdio: 'ignore',
      windowsHide: true,
    },
  );
  backendProcess.on('exit', () => {
    backendProcess = null;
  });

  appUrl = `http://${host}:${port}`;
  await waitForServer(appUrl, 15000);

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
