// electron-main.js
const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

let mainWindow;
let engineServerPort = null;
let engineServerProcess = null;
let engineServerReady = null;

function createWindow() {
  const isDev = process.env.NODE_ENV === 'development';
  const iconPath = isDev
    ? path.join(process.cwd(), 'build', 'favicon.png')
    : path.join(process.resourcesPath, 'build', 'favicon.png');
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js')
    },
    icon: iconPath,
    title: 'PDF 英译中助手'
  });

  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(app.getAppPath(), 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  try {
    await startEngineServer();
  } catch (err) {
    console.error(err);
  }
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  app.isQuitting = true;
  if (engineServerProcess) {
    engineServerProcess.kill();
  }
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});

/**
 * 让渲染进程选择 PDF
 */
ipcMain.handle('select-pdf', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择需要翻译的 PDF 文献',
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
    properties: ['openFile']
  });

  if (result.canceled || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

function isCommandAvailable(command, args) {
  const result = spawnSync(command, args, { stdio: 'ignore' });
  if (!result.error) return true;
  return result.error.code !== 'ENOENT';
}

function resolvePythonCommand() {
  if (process.platform === 'win32') {
    return isCommandAvailable('python', ['-V']) ? 'python' : null;
  }
  if (isCommandAvailable('python3', ['-V'])) return 'python3';
  if (isCommandAvailable('python', ['-V'])) return 'python';
  return null;
}

function resolveEngineServerCommand() {
  const attempts = [];
  const appPath = app.getAppPath();
  const isDev = process.env.NODE_ENV === 'development';

  if (!isDev) {
    const serverExe = path.join(process.resourcesPath, 'engine', 'pdf2zh-engine-server.exe');
    attempts.push(serverExe);
    if (fs.existsSync(serverExe)) {
      return { command: serverExe, args: [], attempts };
    }
    throw new Error(`未找到可用的 pdf2zh 服务命令。已尝试：${attempts.join(', ')}`);
  }

  const venvPython = process.platform === 'win32'
    ? path.join(appPath, 'engine', '.venv', 'Scripts', 'python.exe')
    : path.join(appPath, 'engine', '.venv', 'bin', 'python');
  const devPythonCmd = resolvePythonCommand();
  const devCandidates = [
    { path: venvPython, args: ['-m', 'pdf2zh_engine.server', '--port', '0'] },
    { path: devPythonCmd, args: ['-m', 'pdf2zh_engine.server', '--port', '0'] }
  ];

  for (const candidate of devCandidates) {
    if (!candidate.path) continue;
    attempts.push(candidate.path);
    if (fs.existsSync(candidate.path)) {
      return { command: candidate.path, args: candidate.args, attempts };
    }
  }

  throw new Error(`未找到可用的 pdf2zh 服务命令。已尝试：${attempts.join(', ')}`);
}

function startEngineServer() {
  if (engineServerReady) return engineServerReady;

  engineServerReady = new Promise((resolve, reject) => {
    let resolved = false;
    let stdoutBuffer = '';

    let command;
    let args;
    let stderrBuffer = '';
    try {
      const resolvedCommand = resolveEngineServerCommand();
      command = resolvedCommand.command;
      args = resolvedCommand.args;
    } catch (err) {
      reject(err);
      return;
    }

    engineServerProcess = spawn(command, args, { shell: false });

    const timeout = setTimeout(() => {
      if (!resolved) {
        const message = `引擎服务启动超时（30s）。stderr: ${stderrBuffer.trim().slice(0, 2000) || '(empty)'}`;
        if (engineServerProcess) {
          engineServerProcess.kill();
        }
        reject(new Error(message));
      }
    }, 30000);

    engineServerProcess.stdout.on('data', (buf) => {
      stdoutBuffer += buf.toString();
      let index;
      while ((index = stdoutBuffer.indexOf('\n')) >= 0) {
        const line = stdoutBuffer.slice(0, index).trim();
        stdoutBuffer = stdoutBuffer.slice(index + 1);
        if (!line) continue;
        try {
          const payload = JSON.parse(line);
          if (payload.type === 'ready') {
            engineServerPort = payload.port;
            resolved = true;
            clearTimeout(timeout);
            console.log(`[engine] ready on port ${payload.port}`);
            resolve(payload.port);
          }
        } catch {
          // ignore
        }
      }
    });

    engineServerProcess.stderr.on('data', (buf) => {
      const text = buf.toString();
      stderrBuffer += text;
      console.error(text);
    });

    engineServerProcess.on('error', (err) => {
      clearTimeout(timeout);
      reject(err);
    });

    engineServerProcess.on('close', () => {
      engineServerPort = null;
      engineServerReady = null;
      if (!app.isQuitting) {
        startEngineServer().catch((err) => console.error(err));
      }
    });
  });

  return engineServerReady;
}

function fetchJson(url, options) {
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options && options.headers ? options.headers : {})
    }
  }).then(async (res) => {
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  });
}

function fetchResultWithRetry(jobId, retries = 10, delayMs = 500) {
  return fetchJson(
    `http://127.0.0.1:${engineServerPort}/result?jobId=${encodeURIComponent(jobId)}`
  ).then((result) => {
    console.log(`[engine] result payload for ${jobId}`, result);
    if (result && result.ok) return result;
    if (result && result.error === 'job not finished' && retries > 0) {
      return new Promise((resolve) =>
        setTimeout(() => resolve(fetchResultWithRetry(jobId, retries - 1, delayMs)), delayMs)
      );
    }
    return result;
  });
}

function openProgressStream(jobId) {
  if (!engineServerPort) return;
  const req = http.request({
    hostname: '127.0.0.1',
    port: engineServerPort,
    path: `/events?jobId=${encodeURIComponent(jobId)}`,
    headers: { Accept: 'text/event-stream' }
  });

  let buffer = '';
  req.on('response', (res) => {
    res.on('data', (chunk) => {
      buffer += chunk.toString();
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (!line.startsWith('data:')) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;
        try {
          const payload = JSON.parse(jsonStr);
          console.log(`[engine] event ${payload.type || 'unknown'}`, payload);
          if (payload.type === 'progress') {
            mainWindow.webContents.send('pdf2zh:progress', { jobId, ...payload });
          }
          if (payload.type === 'done') {
            fetchResultWithRetry(jobId)
              .then((result) => {
                console.log(`[engine] result for ${jobId}`, result && result.ok);
                if (result && result.ok) {
                  mainWindow.webContents.send('pdf2zh:done', { jobId, ok: true, result });
                } else {
                  mainWindow.webContents.send('pdf2zh:error', {
                    jobId,
                    message: result?.error || '引擎执行失败',
                    detail: result?.detail || ''
                  });
                  mainWindow.webContents.send('pdf2zh:done', { jobId, ok: false });
                }
              })
              .catch((err) => {
                mainWindow.webContents.send('pdf2zh:error', {
                  jobId,
                  message: '获取结果失败',
                  detail: err.message
                });
                mainWindow.webContents.send('pdf2zh:done', { jobId, ok: false });
              });
          }
          if (payload.type === 'error') {
            mainWindow.webContents.send('pdf2zh:error', { jobId, ...payload });
          }
        } catch {
          // ignore
        }
      }
    });
  });

  req.on('error', (err) => {
    mainWindow.webContents.send('pdf2zh:error', { jobId, message: err.message });
  });

  req.end();
}

ipcMain.handle('start-translate', async (_event, params) => {
  const { filePath, service } = params;

  if (!filePath) {
    mainWindow.webContents.send('pdf2zh:error', {
      jobId: null,
      message: '未选择 PDF 文件'
    });
    return { jobId: null };
  }

  await startEngineServer();
  if (service !== 'google' && service !== 'bing') {
    const message = `不支持的翻译服务: ${service}`;
    mainWindow.webContents.send('pdf2zh:error', { jobId: null, message });
    throw new Error(message);
  }
  const payload = {
    source_path: filePath,
    source_filename: path.basename(filePath),
    service,
    threads: 4
  };
  const response = await fetchJson(`http://127.0.0.1:${engineServerPort}/translate`, {
    method: 'POST',
    body: JSON.stringify(payload)
  });
  const jobId = response.jobId;
  if (jobId) {
    openProgressStream(jobId);
  }
  return { jobId };
});

ipcMain.handle('download-result', async (_event, jobId) => {
  if (!jobId || !engineServerPort) {
    return null;
  }
  const result = await fetchJson(`http://127.0.0.1:${engineServerPort}/result?jobId=${encodeURIComponent(jobId)}`);
  return result;
});
