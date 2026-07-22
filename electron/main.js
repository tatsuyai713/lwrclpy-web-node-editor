const { app, BrowserWindow, dialog, protocol } = require('electron');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { Readable } = require('stream');

const APP_NAME = 'lwrclpy-web-node-editor';
const BACKEND_NAME = 'lwrclpy-web-node-editor-server';
const BACKEND_EXE_NAME = process.platform === 'win32' ? `${BACKEND_NAME}.exe` : BACKEND_NAME;
const USE_BACKEND_PROXY = process.platform === 'win32' || process.env.LWRCLPY_FORCE_BACKEND_PROXY === '1';
const PROXY_SCHEME = 'lwrclpy';
const PROXY_HOST = 'app';
let backendProcess = null;
let appUrl = null;
let extractedBackendDir = null;
let backendExitError = null;
let backendLog = '';

if (USE_BACKEND_PROXY) {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: PROXY_SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: true,
        stream: true,
      },
    },
  ]);
}

function configurePlatformRendering() {
  if (process.platform !== 'win32' && process.env.LWRCLPY_FORCE_SOFTWARE_RENDERING !== '1') {
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
    ['disable-features', 'UseSkiaRenderer,VizDisplayCompositor,CanvasOopRasterization,VaapiVideoDecoder,D3D11VideoDecoder'],
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

function requestOk(url, options = {}) {
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

const requestJson = requestOk;

function rendererUrl() {
  return USE_BACKEND_PROXY ? `${PROXY_SCHEME}://${PROXY_HOST}/` : appUrl;
}

function startupLogPath() {
  try {
    const directory = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(directory, { recursive: true });
    return path.join(directory, 'startup.log');
  } catch (_) {
    return null;
  }
}

function appendStartupLog(message) {
  const file = startupLogPath();
  if (!file) {
    return;
  }
  try {
    fs.appendFileSync(file, `[${new Date().toISOString()}] ${message}\n`, 'utf8');
  } catch (_) {
  }
}

async function requestBodyBuffer(request) {
  if (!request.body) {
    return null;
  }
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    const chunk = Buffer.from(value);
    chunks.push(chunk);
    total += chunk.length;
  }
  return Buffer.concat(chunks, total);
}

async function proxyBackendRequest(request) {
  if (!appUrl) {
    return new Response('Backend is not ready', { status: 503 });
  }
  const requested = new URL(request.url);
  if (requested.hostname !== PROXY_HOST) {
    return new Response('Unknown app host', { status: 404 });
  }

  const target = new URL(`${requested.pathname}${requested.search}`, appUrl);
  const headers = {};
  for (const [key, value] of request.headers.entries()) {
    const normalized = key.toLowerCase();
    if (!['host', 'content-length', 'connection', 'origin', 'referer'].includes(normalized)) {
      headers[key] = value;
    }
  }

  const method = request.method || 'GET';
  const body = method === 'GET' || method === 'HEAD' ? null : await requestBodyBuffer(request);
  return new Promise((resolve) => {
    const backendRequest = http.request(
      target,
      {
        method,
        headers,
      },
      (backendResponse) => {
        const responseHeaders = new Headers();
        for (const [key, value] of Object.entries(backendResponse.headers)) {
          if (['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'].includes(key.toLowerCase())) {
            continue;
          }
          if (value === undefined) {
            continue;
          }
          if (Array.isArray(value)) {
            for (const item of value) {
              responseHeaders.append(key, item);
            }
          } else {
            responseHeaders.set(key, String(value));
          }
        }
        resolve(new Response(Readable.toWeb(backendResponse), {
          status: backendResponse.statusCode || 500,
          headers: responseHeaders,
        }));
      },
    );
    backendRequest.on('error', (error) => {
      resolve(new Response(`Backend request failed: ${error.message}`, { status: 502 }));
    });
    if (body) {
      backendRequest.write(body);
    }
    backendRequest.end();
  });
}

function registerBackendProxy() {
  if (!USE_BACKEND_PROXY) {
    return;
  }
  appendStartupLog(`Registering ${PROXY_SCHEME}:// backend proxy for ${process.platform}-${process.arch}`);
  protocol.handle(PROXY_SCHEME, proxyBackendRequest);
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

async function waitForPage(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backendExitError) {
      throw backendExitError;
    }
    try {
      await requestOk(url);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  const details = backendLog.trim();
  throw new Error(
    `Backend page did not become ready: ${lastError ? lastError.message : 'timeout'}`
    + (details ? `\n\nBackend log:\n${details.slice(-4000)}` : ''),
  );
}

async function loadUrlWithRetry(window, url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backendExitError) {
      throw backendExitError;
    }
    try {
      await window.loadURL(url);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  const details = backendLog.trim();
  throw new Error(
    `Electron failed to load ${url}: ${lastError ? lastError.message : 'timeout'}`
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
  appendStartupLog(`Starting backend: ${backend} --server --host ${host} --port ${port}`);
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
    const text = String(chunk);
    backendLog += text;
    if (backendLog.length > 12000) {
      backendLog = backendLog.slice(-12000);
    }
    appendStartupLog(text.trimEnd());
  };
  if (backendProcess.stdout) {
    backendProcess.stdout.on('data', appendBackendLog);
  }
  if (backendProcess.stderr) {
    backendProcess.stderr.on('data', appendBackendLog);
  }
  backendProcess.on('error', (error) => {
    appendStartupLog(`Backend failed to start: ${error.message}`);
    backendExitError = new Error(`Backend failed to start: ${error.message}`);
    backendProcess = null;
  });
  backendProcess.on('exit', (code, signal) => {
    appendStartupLog(`Backend exited: code=${code} signal=${signal}`);
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
  appendStartupLog(`Backend health OK: ${appUrl}/api/health`);
  await waitForPage(appUrl, 90000);
  appendStartupLog(`Backend page OK: ${appUrl}/`);

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
  const url = rendererUrl();
  appendStartupLog(`Loading renderer URL: ${url}`);
  await loadUrlWithRetry(window, url, 30000);
  appendStartupLog(`Renderer loaded: ${url}`);
}

app.whenReady().then(() => {
  registerBackendProxy();
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
