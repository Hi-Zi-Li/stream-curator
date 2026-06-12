const path = require("path");
const fs = require("fs");
const os = require("os");
const { execFile, spawn } = require("child_process");
const { app, BrowserWindow, clipboard, ipcMain, shell, session } = require("electron");

app.commandLine.appendSwitch("lang", "zh-CN");
app.setAppUserModelId("stream-curator");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const FRONTEND_ENTRY = path.join(PROJECT_ROOT, "frontend", "index.html");
const PRELOAD_ENTRY = path.join(__dirname, "preload.js");
const APP_ICON_PATH = resolveAppIconPath();
const RELEASE_MARKER_PATH = path.join(PROJECT_ROOT, ".portable-release");
const BUNDLED_ENV_ROOT = path.join(PROJECT_ROOT, "runtime", "streamcurator-env");
const BUNDLED_BIN_ROOT = path.join(PROJECT_ROOT, "runtime", "bin");
const APP_SETTINGS_FILE_NAME = "app-settings.json";
const DEFAULT_LLM_CHAT_COMPLETIONS_URL = "https://opencode.ai/zen/go/v1/chat/completions";
const DEFAULT_LLM_MODEL = "deepseek-v4-flash";
const PYTHON_EXECUTABLE = resolveBundledExecutable({
  explicitPath: process.env.STREAM_CURATOR_PYTHON_EXECUTABLE,
  bundledPath: path.join(BUNDLED_ENV_ROOT, "python.exe"),
  fallbackPath: "E:\\Anaconda3\\envs\\streamcurator\\python.exe",
});
const WEBVIEW_PARTITION = "persist:stream-curator";
const BILIBILI_CREDENTIAL_PATH = path.join(os.homedir(), ".bilibili-cli", "credential.json");
const ZHIHU_COOKIE_PATH = path.join(os.homedir(), ".zhihu-cli", "cookies.json");
const XIAOHONGSHU_COOKIE_PATH = path.join(os.homedir(), ".xiaohongshu-cli", "cookies.json");
const SOURCE_EXECUTABLES = {
  bilibili: resolveBundledExecutable({
    explicitPath: process.env.STREAM_CURATOR_BILIBILI_EXECUTABLE,
    bundledPath: path.join(BUNDLED_BIN_ROOT, "bili.cmd"),
    fallbackPath: "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\bili.exe",
  }),
  zhihu: resolveBundledExecutable({
    explicitPath: process.env.STREAM_CURATOR_ZHIHU_EXECUTABLE,
    bundledPath: path.join(BUNDLED_BIN_ROOT, "zhihu.cmd"),
    fallbackPath: "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\zhihu.exe",
  }),
  xiaohongshu: resolveBundledExecutable({
    explicitPath: process.env.STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE,
    bundledPath: path.join(BUNDLED_BIN_ROOT, "xhs.cmd"),
    fallbackPath: "E:\\Anaconda3\\envs\\streamcurator\\Scripts\\xhs.exe",
  }),
};
const SOURCE_LABELS = {
  bilibili: "Bilibili",
  zhihu: "Zhihu",
  xiaohongshu: "Xiaohongshu",
};
const SOURCE_LOGIN_SPECS = {
  bilibili: {
    method: "应用内登录页",
    url: "https://passport.bilibili.com/login",
    partition: "persist:stream-curator-login-bilibili",
  },
  zhihu: {
    method: "应用内登录页",
    url: "https://www.zhihu.com/signin",
    partition: "persist:stream-curator-login-zhihu",
  },
  xiaohongshu: {
    method: "应用内登录页",
    url: "https://www.xiaohongshu.com/login",
    partition: "persist:stream-curator-login-xiaohongshu",
  },
};
const ZHIHU_COOKIE_NAMES = ["z_c0", "_xsrf", "d_c0"];
const XIAOHONGSHU_COOKIE_NAMES = [
  "a1",
  "webId",
  "web_session",
  "web_session_sec",
  "id_token",
  "websectiga",
  "sec_poison_id",
  "xsecappid",
  "gid",
  "abRequestId",
  "webBuild",
  "loadts",
  "acw_tc",
];
let workerStarted = false;
let workerStartPromise = null;
const inFlightSearchReviews = new Set();

function resolveBundledExecutable({ explicitPath, bundledPath, fallbackPath }) {
  const preferred = String(explicitPath || "").trim();
  if (preferred) {
    return preferred;
  }
  if (bundledPath && fs.existsSync(bundledPath)) {
    return bundledPath;
  }
  return fallbackPath;
}

function resolveAppIconPath() {
  const candidates = process.platform === "win32"
    ? [path.join(__dirname, "assets", "app-icon.ico"), path.join(__dirname, "assets", "app-icon.png")]
    : [path.join(__dirname, "assets", "app-icon.png"), path.join(__dirname, "assets", "app-icon.ico")];
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function isReleaseBuild() {
  return app.isPackaged || fs.existsSync(RELEASE_MARKER_PATH);
}

function getRuntimeRoot() {
  if (!isReleaseBuild()) {
    return PROJECT_ROOT;
  }
  const explicitRoot = String(process.env.STREAM_CURATOR_RUNTIME_ROOT || "").trim();
  if (explicitRoot) {
    return explicitRoot;
  }
  return path.join(app.getPath("userData"), "runtime");
}

function getAppSettingsPath() {
  return path.join(getRuntimeRoot(), "data", APP_SETTINGS_FILE_NAME);
}

function buildPythonEnv() {
  const env = { ...process.env };
  const srcDir = path.join(PROJECT_ROOT, "src");
  const pythonHome = path.dirname(PYTHON_EXECUTABLE);
  const usingBundledPython = path.resolve(pythonHome) === path.resolve(BUNDLED_ENV_ROOT);
  const runtimePathEntries = [pythonHome, path.join(pythonHome, "Library", "bin"), path.join(pythonHome, "Scripts")]
    .filter((entry) => fs.existsSync(entry));
  env.PYTHONPATH = env.PYTHONPATH ? `${srcDir}${path.delimiter}${env.PYTHONPATH}` : srcDir;
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUTF8 = "1";
  if (runtimePathEntries.length > 0) {
    env.PATH = env.PATH
      ? `${runtimePathEntries.join(path.delimiter)}${path.delimiter}${env.PATH}`
      : runtimePathEntries.join(path.delimiter);
  }
  if (usingBundledPython && fs.existsSync(path.join(pythonHome, "Lib"))) {
    env.PYTHONHOME = pythonHome;
  }
  env.STREAM_CURATOR_PYTHON_EXECUTABLE = PYTHON_EXECUTABLE;
  env.STREAM_CURATOR_BILIBILI_EXECUTABLE = SOURCE_EXECUTABLES.bilibili;
  env.STREAM_CURATOR_ZHIHU_EXECUTABLE = SOURCE_EXECUTABLES.zhihu;
  env.STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE = SOURCE_EXECUTABLES.xiaohongshu;
  env.STREAM_CURATOR_SOURCE_ROOT = PROJECT_ROOT;
  env.STREAM_CURATOR_PROJECT_ROOT = getRuntimeRoot();
  env.STREAM_CURATOR_APP_SETTINGS_PATH = getAppSettingsPath();
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

function runHotRead() {
  return runJsonCli(["client", "hot"], { timeoutMs: 60_000 });
}

function runHotRefresh() {
  return runJsonCli(["client", "hot", "--refresh"], { timeoutMs: 60_000 });
}

function runSearchRead(query, { force = false } = {}) {
  const keyword = String(query || "").trim();
  const args = ["client", "search", keyword];
  if (force) {
    args.push("--refresh");
  }
  return runJsonCli(args, { timeoutMs: 120_000 });
}

function triggerSearchReview(query, { force = false } = {}) {
  const keyword = String(query || "").trim();
  if (!keyword || inFlightSearchReviews.has(keyword)) {
    return false;
  }
  inFlightSearchReviews.add(keyword);
  const args = ["-X", "utf8", "-m", "stream_curator.cli", "client", "search-review", keyword];
  if (force) {
    args.push("--force");
  }
  const child = spawn(PYTHON_EXECUTABLE, args, {
    cwd: PROJECT_ROOT,
    env: buildPythonEnv(),
    windowsHide: true,
    detached: true,
    stdio: "ignore",
  });
  child.on("exit", () => {
    inFlightSearchReviews.delete(keyword);
  });
  child.on("error", () => {
    inFlightSearchReviews.delete(keyword);
  });
  child.unref();
  return true;
}

function runReaderComments(options = {}) {
  const source = String(options.source || "").trim();
  const entityType = String(options.entityType || "").trim();
  const sourceItemId = String(options.sourceItemId || "").trim();
  const canonicalUrl = String(options.canonicalUrl || "").trim();
  const cursor = String(options.cursor || "");
  const limit = Number(options.limit || 10);
  const args = [
    "client",
    "comments",
    "--source",
    source,
    "--entity-type",
    entityType,
    "--source-item-id",
    sourceItemId,
    "--canonical-url",
    canonicalUrl,
    "--cursor",
    cursor,
    "--limit",
    String(Number.isFinite(limit) && limit > 0 ? Math.round(limit) : 10),
  ];
  return runJsonCli(args, { timeoutMs: 45_000 });
}

function runSourceCliText(source, args, { timeoutMs = 20_000 } = {}) {
  const executable = SOURCE_EXECUTABLES[source];
  if (!executable) {
    return Promise.reject(new Error(`unsupported_source: ${source}`));
  }
  if (!fs.existsSync(executable)) {
    return Promise.reject(new Error(`source_executable_missing: ${executable}`));
  }
  return new Promise((resolve, reject) => {
    execFile(
      executable,
      args,
      {
        cwd: PROJECT_ROOT,
        env: buildPythonEnv(),
        windowsHide: true,
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
      },
      (error, stdout, stderr) => {
        const output = String(stdout || stderr || error?.message || "").trim();
        if (error) {
          reject(new Error(output || `${source}_cli_failed`));
          return;
        }
        resolve(output);
      }
    );
  });
}

async function getAuthStatusPayload() {
  const entries = await Promise.all(
    Object.keys(SOURCE_EXECUTABLES).map(async (source) => [source, await getSourceAuthStatus(source)])
  );
  return {
    updatedAtIso: new Date().toISOString(),
    sources: Object.fromEntries(entries),
  };
}

async function getSourceAuthStatus(source) {
  const executable = SOURCE_EXECUTABLES[source];
  const label = SOURCE_LABELS[source] || source;
  const loginMethod = SOURCE_LOGIN_SPECS[source]?.method || "登录";
  if (!executable || !fs.existsSync(executable)) {
    return {
      source,
      label,
      available: false,
      authenticated: false,
      displayName: "",
      statusText: "CLI 未安装",
      loginMethod,
      executable: executable || "",
    };
  }

  try {
    if (source === "zhihu") {
      const statusOutput = await runSourceCliText(source, ["status"], { timeoutMs: 12_000 });
      const authenticated =
        /\bauthenticated\b/i.test(statusOutput) && !/\bnot authenticated\b/i.test(statusOutput);
      let displayName = "";
      if (authenticated) {
        try {
          const profileOutput = await runSourceCliText(source, ["whoami"], { timeoutMs: 12_000 });
          displayName = parseZhihuProfileName(profileOutput);
        } catch {
          displayName = "";
        }
      }
      return {
        source,
        label,
        available: true,
        authenticated,
        displayName,
        statusText: authenticated ? (displayName ? `已登录 · ${displayName}` : "已登录") : "未登录",
        loginMethod,
        executable,
      };
    }

    const statusOutput = await runSourceCliText(source, ["status"], { timeoutMs: 12_000 });
    const authenticated = parseBooleanField(statusOutput, "authenticated");
    const displayName = parseScalarField(statusOutput, "name");
    return {
      source,
      label,
      available: true,
      authenticated: authenticated === true,
      displayName,
      statusText: authenticated ? (displayName ? `已登录 · ${displayName}` : "已登录") : "未登录",
      loginMethod,
      executable,
    };
  } catch (error) {
    return {
      source,
      label,
      available: true,
      authenticated: false,
      displayName: "",
      statusText: "状态读取失败",
      loginMethod,
      executable,
      error: String(error?.message || error || ""),
    };
  }
}

function readJsonFile(filePath, fallback = {}) {
  try {
    if (!fs.existsSync(filePath)) {
      return fallback;
    }
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJsonFile(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), "utf8");
}

function normalizeText(value) {
  return String(value || "").trim();
}

function loadAppSettings() {
  const payload = readJsonFile(getAppSettingsPath(), {});
  return payload && typeof payload === "object" ? payload : {};
}

function saveAppSettings(payload) {
  writeJsonFile(getAppSettingsPath(), payload);
}

function getEffectiveLlmSettings() {
  const stored = loadAppSettings();
  const envUrl = normalizeText(process.env.STREAM_CURATOR_LLM_CHAT_COMPLETIONS_URL);
  const storedUrl = normalizeText(stored.llm_chat_completions_url);
  const envModel = normalizeText(process.env.STREAM_CURATOR_LLM_MODEL);
  const storedModel = normalizeText(stored.llm_model);
  const explicitApiKey = normalizeText(process.env.STREAM_CURATOR_LLM_API_KEY);
  const storedApiKey = normalizeText(stored.llm_api_key);
  const fallbackApiKey = normalizeText(process.env.OPENCODE_API_KEY);

  let apiKeySource = "none";
  if (explicitApiKey) {
    apiKeySource = "env";
  } else if (storedApiKey) {
    apiKeySource = "saved";
  } else if (fallbackApiKey) {
    apiKeySource = "env";
  }

  return {
    apiUrl: envUrl || storedUrl || DEFAULT_LLM_CHAT_COMPLETIONS_URL,
    model: envModel || storedModel || DEFAULT_LLM_MODEL,
    apiKeyPresent: Boolean(explicitApiKey || storedApiKey || fallbackApiKey),
    apiKeySource,
    hasStoredApiKey: Boolean(storedApiKey),
    settingsPath: getAppSettingsPath(),
  };
}

function saveLlmSettings(payload) {
  const current = loadAppSettings();
  const next = { ...current };
  const apiUrl = normalizeText(payload?.apiUrl);
  const model = normalizeText(payload?.model);
  const apiKey = normalizeText(payload?.apiKey);
  const clearApiKey = payload?.clearApiKey === true;

  if (apiUrl && apiUrl !== DEFAULT_LLM_CHAT_COMPLETIONS_URL) {
    next.llm_chat_completions_url = apiUrl;
  } else {
    delete next.llm_chat_completions_url;
  }

  if (model && model !== DEFAULT_LLM_MODEL) {
    next.llm_model = model;
  } else {
    delete next.llm_model;
  }

  if (clearApiKey) {
    delete next.llm_api_key;
  } else if (apiKey) {
    next.llm_api_key = apiKey;
  }

  saveAppSettings(next);
  return getEffectiveLlmSettings();
}

async function restartWorkerForSettingsChange() {
  try {
    await runJsonCli(["worker", "stop"], { timeoutMs: 20_000 });
  } catch {
    // Ignore stale state or not-running cases and try to start again below.
  }
  workerStarted = false;
  return ensureWorkerStarted();
}

async function getSourceLoginCookies(source) {
  const loginSpec = SOURCE_LOGIN_SPECS[source];
  if (!loginSpec?.partition) {
    throw new Error(`unsupported_source: ${source}`);
  }
  const partitionSession = session.fromPartition(loginSpec.partition);
  const rawCookies = await partitionSession.cookies.get({});
  const domainNeedle =
    source === "bilibili" ? "bilibili.com" : source === "zhihu" ? "zhihu.com" : "xiaohongshu.com";
  const cookies = {};
  for (const entry of rawCookies) {
    if (!entry?.name || !entry?.value) {
      continue;
    }
    if (domainNeedle && !String(entry.domain || "").includes(domainNeedle)) {
      continue;
    }
    cookies[String(entry.name)] = String(entry.value);
  }
  return cookies;
}

async function commitSourceLogin(source) {
  if (source === "bilibili") {
    return commitBilibiliLogin();
  }
  if (source === "zhihu") {
    return commitZhihuLogin();
  }
  if (source === "xiaohongshu") {
    return commitXiaohongshuLogin();
  }
  throw new Error(`unsupported_source: ${source}`);
}

async function commitBilibiliLogin() {
  const exported = await getSourceLoginCookies("bilibili");
  const previous = readJsonFile(BILIBILI_CREDENTIAL_PATH, {});
  if (!exported.SESSDATA) {
    throw new Error("未检测到 Bilibili 登录 cookie，请先在页面内完成登录。");
  }
  const payload = {
    ...previous,
    sessdata: exported.SESSDATA,
    bili_jct: exported.bili_jct || previous.bili_jct || "",
    ac_time_value: exported.ac_time_value || previous.ac_time_value || "",
    buvid3: exported.buvid3 || previous.buvid3 || "",
    buvid4: exported.buvid4 || previous.buvid4 || "",
    dedeuserid: exported.DedeUserID || previous.dedeuserid || "",
    saved_at: Date.now() / 1000,
  };
  writeJsonFile(BILIBILI_CREDENTIAL_PATH, payload);
  await syncBilibiliSessionCookies(session.fromPartition(WEBVIEW_PARTITION)).catch(() => false);
  return getSourceAuthStatus("bilibili");
}

async function commitZhihuLogin() {
  const exported = await getSourceLoginCookies("zhihu");
  const previous = readJsonFile(ZHIHU_COOKIE_PATH, {});
  const previousCookies = previous && typeof previous === "object" && previous.cookies ? previous.cookies : {};
  if (!exported.z_c0) {
    throw new Error("未检测到知乎登录 cookie，请先在页面内完成登录。");
  }
  const cookies = {};
  for (const name of ZHIHU_COOKIE_NAMES) {
    const value = name === "z_c0" ? exported.z_c0 : exported[name] || previousCookies[name] || "";
    if (value) {
      cookies[name] = value;
    }
  }
  writeJsonFile(ZHIHU_COOKIE_PATH, { cookies });
  return getSourceAuthStatus("zhihu");
}

async function commitXiaohongshuLogin() {
  const exported = await getSourceLoginCookies("xiaohongshu");
  const previous = readJsonFile(XIAOHONGSHU_COOKIE_PATH, {});
  if (!exported.a1) {
    throw new Error("未检测到小红书登录 cookie，请先在页面内完成登录。");
  }
  const payload = { ...previous };
  for (const name of XIAOHONGSHU_COOKIE_NAMES) {
    const value = name === "a1" ? exported.a1 : exported[name] || previous[name] || "";
    if (value) {
      payload[name] = value;
    }
  }
  if (!payload.xsecappid) {
    payload.xsecappid = "xhs-pc-web";
  }
  payload.saved_at = Date.now() / 1000;
  writeJsonFile(XIAOHONGSHU_COOKIE_PATH, payload);
  return getSourceAuthStatus("xiaohongshu");
}

function parseBooleanField(text, fieldName) {
  const raw = parseScalarField(text, fieldName).toLowerCase();
  if (raw === "true") {
    return true;
  }
  if (raw === "false") {
    return false;
  }
  return null;
}

function parseScalarField(text, fieldName) {
  const pattern = new RegExp(`^\\s*${escapeRegex(fieldName)}\\s*:\\s*(.+?)\\s*$`, "im");
  const match = String(text || "").match(pattern);
  if (!match) {
    return "";
  }
  return String(match[1] || "").trim().replace(/^['"]|['"]$/g, "");
}

function parseZhihuProfileName(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines) {
    if (!line.startsWith("+") && !line.startsWith("|")) {
      return line;
    }
  }
  return "";
}

function escapeRegex(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function ensureWorkerStarted() {
  if (workerStarted) {
    return Promise.resolve(true);
  }
  if (workerStartPromise) {
    return workerStartPromise;
  }
  workerStartPromise = runJsonCli(["worker", "start"], { timeoutMs: 20_000 })
    .then((result) => {
      workerStarted = Boolean(result?.started || result?.already_running || result?.status);
      return workerStarted;
    })
    .catch(() => false)
    .finally(() => {
      workerStartPromise = null;
    });
  return workerStartPromise;
}

function loadBilibiliCredential() {
  try {
    if (!fs.existsSync(BILIBILI_CREDENTIAL_PATH)) {
      return null;
    }
    const payload = JSON.parse(fs.readFileSync(BILIBILI_CREDENTIAL_PATH, "utf8"));
    if (!payload?.sessdata) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

async function syncBilibiliSessionCookies(targetSession) {
  const credential = loadBilibiliCredential();
  if (!credential) {
    return false;
  }

  const cookieSpecs = [
    ["SESSDATA", credential.sessdata],
    ["bili_jct", credential.bili_jct],
    ["DedeUserID", credential.dedeuserid],
    ["ac_time_value", credential.ac_time_value],
    ["buvid3", credential.buvid3],
    ["buvid4", credential.buvid4],
  ].filter((entry) => Boolean(entry[1]));

  if (cookieSpecs.length === 0) {
    return false;
  }

  const cookieTargets = ["https://www.bilibili.com", "https://player.bilibili.com"];
  await Promise.all(
    cookieTargets.flatMap((url) =>
      cookieSpecs.map(([name, value]) =>
        targetSession.cookies.set({
          url,
          name,
          value: String(value),
          domain: ".bilibili.com",
          path: "/",
          secure: true,
          httpOnly: false,
        })
      )
    )
  );
  return true;
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
    ...(APP_ICON_PATH ? { icon: APP_ICON_PATH } : {}),
    webPreferences: {
      preload: PRELOAD_ENTRY,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
      webviewTag: true,
      partition: WEBVIEW_PARTITION,
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
    try {
      const payload = await runPushRead({ ensureCurrent });
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "push_get_failed", workerStarted };
    }
  });

  ipcMain.handle("push:refresh", async () => {
    try {
      const payload = await runPushRefresh();
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "push_refresh_failed", workerStarted };
    }
  });

  ipcMain.handle("hot:get", async () => {
    try {
      const payload = await runHotRead();
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "hot_get_failed", workerStarted };
    }
  });

  ipcMain.handle("hot:refresh", async () => {
    try {
      const payload = await runHotRefresh();
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "hot_refresh_failed", workerStarted };
    }
  });

  ipcMain.handle("search:get", async (_event, input) => {
    const query = typeof input === "string" ? input : input?.query;
    const force = Boolean(typeof input === "object" && input?.force);
    try {
      const payload = await runSearchRead(query, { force });
      const reviewStatus = String(payload?.review?.status || "").trim();
      if (
        String(query || "").trim() &&
        reviewStatus !== "completed" &&
        reviewStatus !== "disabled" &&
        reviewStatus !== "failed"
      ) {
        triggerSearchReview(query, { force: false });
      }
      return { ok: true, payload, workerStarted };
    } catch (error) {
      return { ok: false, error: error.message || "search_get_failed", workerStarted };
    }
  });

  ipcMain.handle("reader:comments", async (_event, options = {}) => {
    try {
      const payload = await runReaderComments(options);
      return { ok: true, payload };
    } catch (error) {
      return { ok: false, error: error.message || "reader_comments_failed" };
    }
  });

  ipcMain.handle("settings:auth-status", async () => {
    try {
      const payload = await getAuthStatusPayload();
      return { ok: true, payload };
    } catch (error) {
      return { ok: false, error: error.message || "settings_auth_status_failed" };
    }
  });

  ipcMain.handle("settings:llm-config:get", async () => {
    try {
      return { ok: true, payload: getEffectiveLlmSettings() };
    } catch (error) {
      return { ok: false, error: error.message || "settings_llm_config_get_failed" };
    }
  });

  ipcMain.handle("settings:llm-config:save", async (_event, input = {}) => {
    try {
      const payload = saveLlmSettings(input);
      await restartWorkerForSettingsChange().catch(() => false);
      return { ok: true, payload: { ...payload, workerRunning: workerStarted } };
    } catch (error) {
      return { ok: false, error: error.message || "settings_llm_config_save_failed" };
    }
  });

  ipcMain.handle("settings:login-spec", async (_event, source) => {
    try {
      const key = String(source || "").trim();
      const loginSpec = SOURCE_LOGIN_SPECS[key];
      if (!loginSpec) {
        throw new Error(`unsupported_source: ${key}`);
      }
      const payload = {
        source: key,
        label: SOURCE_LABELS[key] || key,
        method: loginSpec.method,
        url: loginSpec.url,
        partition: loginSpec.partition,
      };
      return { ok: true, payload };
    } catch (error) {
      return { ok: false, error: error.message || "settings_login_spec_failed" };
    }
  });

  ipcMain.handle("settings:commit-login", async (_event, source) => {
    try {
      const payload = await commitSourceLogin(String(source || "").trim());
      return { ok: true, payload };
    } catch (error) {
      return { ok: false, error: error.message || "settings_commit_login_failed" };
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

app.whenReady().then(async () => {
  registerIpc();
  const appSession = session.fromPartition(WEBVIEW_PARTITION);
  appSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  await syncBilibiliSessionCookies(appSession).catch(() => false);
  void ensureWorkerStarted();
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
