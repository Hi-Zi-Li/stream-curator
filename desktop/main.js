const path = require("path");
const { execFile } = require("child_process");
const { app, BrowserWindow, clipboard, ipcMain, shell, session } = require("electron");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const FRONTEND_ENTRY = path.join(PROJECT_ROOT, "frontend", "index.html");
const PRELOAD_ENTRY = path.join(__dirname, "preload.js");
const PYTHON_EXECUTABLE =
  process.env.STREAM_CURATOR_PYTHON_EXECUTABLE || "E:\\Anaconda3\\envs\\streamcurator\\python.exe";

function buildPythonEnv() {
  const env = { ...process.env };
  const srcDir = path.join(PROJECT_ROOT, "src");
  env.PYTHONPATH = env.PYTHONPATH ? `${srcDir}${path.delimiter}${env.PYTHONPATH}` : srcDir;
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUTF8 = "1";
  return env;
}

function runJsonCli(args, { timeoutMs }) {
  return new Promise((resolve, reject) => {
    execFile(
      PYTHON_EXECUTABLE,
      ["-X", "utf8", "-m", "stream_curator.cli", ...args],
      {
        cwd: PROJECT_ROOT,
        env: buildPythonEnv(),
        windowsHide: true,
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
      },
      (error, stdout, stderr) => {
        if (error) {
          const detail = String(stderr || stdout || error.message || "stream_curator_cli_failed").trim();
          reject(new Error(detail || "stream_curator_cli_failed"));
          return;
        }

        const text = String(stdout || "").trim();
        if (!text) {
          resolve({});
          return;
        }
        try {
          resolve(JSON.parse(text));
        } catch (parseError) {
          reject(new Error(`invalid_cli_payload: ${parseError.message}`));
        }
      }
    );
  });
}

function runPushRead({ ensureCurrent = false } = {}) {
  const args = ["client", "push"];
  args.push(ensureCurrent ? "--ensure-current" : "--no-ensure-current");
  return runJsonCli(args, { timeoutMs: ensureCurrent ? 240_000 : 15_000 });
}

function runPushRefresh() {
  return runJsonCli(["client", "push", "--refresh"], { timeoutMs: 15_000 });
}

function ensureWorkerStarted() {
  return runJsonCli(["worker", "start"], { timeoutMs: 20_000 })
    .then((result) => Boolean(result?.started || result?.already_running || result?.status))
    .catch(() => false);
}

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1540,
    height: 980,
    minWidth: 1200,
    minHeight: 760,
    autoHideMenuBar: true,
    backgroundColor: "#f3efe6",
    title: "stream-curator",
    webPreferences: {
      preload: PRELOAD_ENTRY,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.loadFile(FRONTEND_ENTRY);
}

function registerIpc() {
  ipcMain.handle("push:get", async (_event, options = {}) => {
    const ensureCurrent = Boolean(options.ensureCurrent);
    const workerStarted = await ensureWorkerStarted();
    try {
      const payload = await runPushRead({ ensureCurrent });
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "push_get_failed", workerStarted };
    }
  });

  ipcMain.handle("push:refresh", async () => {
    const workerStarted = await ensureWorkerStarted();
    try {
      const payload = await runPushRefresh();
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "push_refresh_failed", workerStarted };
    }
  });

  ipcMain.handle("desktop:open-external", async (_event, url) => {
    if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
      return { ok: false, error: "invalid_url" };
    }
    await shell.openExternal(url);
    return { ok: true };
  });

  ipcMain.handle("desktop:copy-text", (_event, text) => {
    clipboard.writeText(String(text ?? ""));
    return { ok: true };
  });
}

app.whenReady().then(() => {
  registerIpc();
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
