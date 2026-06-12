const bridge = globalThis.streamCuratorDesktop ?? null;

const state = {
  pushPayload: null,
  hotPayload: null,
  searchPayload: null,
  selectedIds: {
    push: null,
    hot: null,
    search: null,
  },
  readerPage: "push",
  pollTimer: null,
  searchReviewPollTimer: null,
  isLoading: false,
  isRefreshing: false,
  view: "push",
  searchQuery: "",
  settingsAuth: {
    loading: false,
    updatedAt: "",
    sources: {},
  },
  settingsLlm: {
    loading: false,
    saving: false,
    apiUrl: "",
    model: "",
    apiKey: "",
    apiKeyPresent: false,
    apiKeySource: "none",
    hasStoredApiKey: false,
    settingsPath: "",
  },
  settingsLogin: {
    open: false,
    source: "",
    label: "",
    url: "",
    partition: "",
    loading: false,
    error: "",
    statusText: "",
  },
  readerComments: null,
  readerAnswerSelection: null,
};

const refs = {};
const PUSH_CARD_LIMIT = 6;
const HOT_CARD_LIMIT = 15;
const READER_COMMENT_PAGE_SIZE = 10;
const SETTINGS_SOURCE_ORDER = ["bilibili", "zhihu", "xiaohongshu"];
const SETTINGS_LOGIN_WEBVIEW_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

document.addEventListener("DOMContentLoaded", () => {
  cacheRefs();
  bindEvents();
  render();
  if (!bridge?.isDesktopClient) {
    setStatus("请在桌面客户端内打开。", "");
    return;
  }
  void loadPush({ ensureCurrent: false });
});

function cacheRefs() {
  refs.pushView = document.getElementById("push-view");
  refs.readerView = document.getElementById("reader-view");
  refs.navPush = document.getElementById("nav-push");
  refs.navHot = document.getElementById("nav-hot");
  refs.navSearch = document.getElementById("nav-search");
  refs.navSettings = document.querySelector(".nav-foot");
  refs.statusLine = document.getElementById("status-line");
  refs.metaLine = document.getElementById("meta-line");
  refs.refreshButton = document.getElementById("refresh-button");
  refs.cardGrid = document.getElementById("card-grid");
  refs.searchView = document.getElementById("search-view");
  refs.searchForm = document.getElementById("search-form");
  refs.searchInput = document.getElementById("search-input");
  refs.searchSubmit = document.getElementById("search-submit");
  refs.searchReview = document.getElementById("search-review");
  refs.searchReviewKicker = document.getElementById("search-review-kicker");
  refs.searchReviewMeta = document.getElementById("search-review-meta");
  refs.searchReviewSummary = document.getElementById("search-review-summary");
  refs.searchReviewGroups = document.getElementById("search-review-groups");
  refs.searchResults = document.getElementById("search-results");
  refs.detailEmpty = document.getElementById("detail-empty");
  refs.detailContent = document.getElementById("detail-content");
  refs.detailPanel = document.querySelector(".detail-panel");
  refs.detailSource = document.getElementById("detail-source");
  refs.detailLevel = document.getElementById("detail-level");
  refs.detailAuthor = document.getElementById("detail-author");
  refs.detailTitle = document.getElementById("detail-title");
  refs.detailLabels = document.getElementById("detail-labels");
  refs.detailSummary = document.getElementById("detail-summary");
  refs.detailReason = document.getElementById("detail-reason");
  refs.detailSections = document.getElementById("detail-sections");
  refs.readerButton = document.getElementById("reader-button");
  refs.openButton = document.getElementById("open-button");
  refs.copyButton = document.getElementById("copy-button");
  refs.readerBackButton = document.getElementById("reader-back-button");
  refs.readerSource = document.getElementById("reader-source");
  refs.readerLevel = document.getElementById("reader-level");
  refs.readerAuthor = document.getElementById("reader-author");
  refs.readerTitle = document.getElementById("reader-title");
  refs.readerSummary = document.getElementById("reader-summary");
  refs.readerReason = document.getElementById("reader-reason");
  refs.readerLabels = document.getElementById("reader-labels");
  refs.readerBody = document.getElementById("reader-body");
  refs.readerOpenButton = document.getElementById("reader-open-button");
  refs.readerCopyButton = document.getElementById("reader-copy-button");
  refs.toast = document.getElementById("toast");
  ensureSettingsScaffold();
}

function ensureSettingsScaffold() {
  if (!refs.pushView) {
    return;
  }
  if (refs.navSettings) {
    refs.navSettings.disabled = false;
  }

  let settingsView = document.getElementById("settings-view");
  if (!settingsView) {
    settingsView = document.createElement("section");
    settingsView.id = "settings-view";
    settingsView.className = "settings-view is-hidden";
    settingsView.setAttribute("aria-label", "设置");

    const settingsPanel = document.createElement("section");
    settingsPanel.className = "detail-panel settings-panel";

    const intro = document.createElement("div");
    intro.className = "settings-intro";

    const title = document.createElement("h2");
    title.className = "settings-title";
    title.textContent = "账号与登录";

    const note = document.createElement("p");
    note.className = "settings-note";
    note.textContent = "每个平台都可以直接在应用内完成登录。登录完成后点“保存登录”即可写回当前 CLI 的本地凭证。";

    const llmPanel = document.createElement("section");
    llmPanel.className = "settings-llm-card";

    const llmTitle = document.createElement("h3");
    llmTitle.className = "settings-source-title";
    llmTitle.textContent = "LLM 配置";

    const llmNote = document.createElement("p");
    llmNote.className = "settings-note";
    llmNote.textContent = "API Key 输入框不会回显。留空保存会保留当前已保存的 Key。";

    const llmMeta = document.createElement("p");
    llmMeta.id = "settings-llm-meta";
    llmMeta.className = "settings-source-detail";

    const llmForm = document.createElement("div");
    llmForm.className = "settings-llm-form";

    const urlLabel = document.createElement("label");
    urlLabel.className = "settings-field";
    const urlTitle = document.createElement("span");
    urlTitle.className = "settings-field-label";
    urlTitle.textContent = "API URL";
    const urlInput = document.createElement("input");
    urlInput.id = "settings-llm-url";
    urlInput.className = "settings-input";
    urlInput.type = "url";
    urlInput.placeholder = "https://opencode.ai/zen/go/v1/chat/completions";
    urlLabel.appendChild(urlTitle);
    urlLabel.appendChild(urlInput);

    const modelLabel = document.createElement("label");
    modelLabel.className = "settings-field";
    const modelTitle = document.createElement("span");
    modelTitle.className = "settings-field-label";
    modelTitle.textContent = "Model";
    const modelInput = document.createElement("input");
    modelInput.id = "settings-llm-model";
    modelInput.className = "settings-input";
    modelInput.type = "text";
    modelInput.placeholder = "deepseek-v4-flash";
    modelLabel.appendChild(modelTitle);
    modelLabel.appendChild(modelInput);

    const keyLabel = document.createElement("label");
    keyLabel.className = "settings-field";
    const keyTitle = document.createElement("span");
    keyTitle.className = "settings-field-label";
    keyTitle.textContent = "API Key";
    const keyInput = document.createElement("input");
    keyInput.id = "settings-llm-api-key";
    keyInput.className = "settings-input";
    keyInput.type = "password";
    keyInput.placeholder = "留空则保留当前已保存的 Key";
    keyLabel.appendChild(keyTitle);
    keyLabel.appendChild(keyInput);

    const llmActions = document.createElement("div");
    llmActions.className = "detail-actions";

    const llmSaveButton = document.createElement("button");
    llmSaveButton.id = "settings-llm-save";
    llmSaveButton.className = "action-button action-primary";
    llmSaveButton.type = "button";
    llmSaveButton.textContent = "保存配置";

    const llmClearKeyButton = document.createElement("button");
    llmClearKeyButton.id = "settings-llm-clear-key";
    llmClearKeyButton.className = "action-button";
    llmClearKeyButton.type = "button";
    llmClearKeyButton.textContent = "清空已保存 Key";

    llmActions.appendChild(llmSaveButton);
    llmActions.appendChild(llmClearKeyButton);
    llmForm.appendChild(urlLabel);
    llmForm.appendChild(modelLabel);
    llmForm.appendChild(keyLabel);
    llmPanel.appendChild(llmTitle);
    llmPanel.appendChild(llmNote);
    llmPanel.appendChild(llmMeta);
    llmPanel.appendChild(llmForm);
    llmPanel.appendChild(llmActions);

    const list = document.createElement("div");
    list.id = "settings-auth-list";
    list.className = "settings-auth-list";

    const loginPanel = document.createElement("section");
    loginPanel.id = "settings-login-panel";
    loginPanel.className = "detail-panel settings-login-panel is-hidden";

    const loginTop = document.createElement("div");
    loginTop.className = "settings-login-top";

    const loginHead = document.createElement("div");
    loginHead.className = "settings-login-head";

    const loginTitle = document.createElement("h2");
    loginTitle.id = "settings-login-title";
    loginTitle.className = "settings-title";
    loginTitle.textContent = "登录";

    const loginNote = document.createElement("p");
    loginNote.id = "settings-login-note";
    loginNote.className = "settings-note";
    loginNote.textContent = "在页面内完成登录，然后点击“保存登录”。";

    const loginActions = document.createElement("div");
    loginActions.className = "detail-actions";

    const closeButton = document.createElement("button");
    closeButton.id = "settings-login-close";
    closeButton.className = "action-button";
    closeButton.type = "button";
    closeButton.textContent = "关闭";

    const saveButton = document.createElement("button");
    saveButton.id = "settings-login-save";
    saveButton.className = "action-button action-primary";
    saveButton.type = "button";
    saveButton.textContent = "保存登录";

    const loginStatus = document.createElement("p");
    loginStatus.id = "settings-login-status";
    loginStatus.className = "settings-login-status";

    const loginShell = document.createElement("div");
    loginShell.id = "settings-login-shell";
    loginShell.className = "settings-login-shell";

    loginHead.appendChild(loginTitle);
    loginHead.appendChild(loginNote);
    loginActions.appendChild(closeButton);
    loginActions.appendChild(saveButton);
    loginTop.appendChild(loginHead);
    loginTop.appendChild(loginActions);
    loginPanel.appendChild(loginTop);
    loginPanel.appendChild(loginStatus);
    loginPanel.appendChild(loginShell);

    intro.appendChild(title);
    intro.appendChild(note);
    settingsPanel.appendChild(intro);
    settingsPanel.appendChild(llmPanel);
    settingsPanel.appendChild(list);
    settingsView.appendChild(settingsPanel);
    settingsView.appendChild(loginPanel);

    if (refs.detailPanel?.parentNode === refs.pushView) {
      refs.pushView.insertBefore(settingsView, refs.detailPanel);
    } else {
      refs.pushView.appendChild(settingsView);
    }
  }

  refs.settingsView = settingsView;
  refs.settingsAuthList = settingsView.querySelector("#settings-auth-list");
  refs.settingsLlmMeta = settingsView.querySelector("#settings-llm-meta");
  refs.settingsLlmUrl = settingsView.querySelector("#settings-llm-url");
  refs.settingsLlmModel = settingsView.querySelector("#settings-llm-model");
  refs.settingsLlmApiKey = settingsView.querySelector("#settings-llm-api-key");
  refs.settingsLlmSave = settingsView.querySelector("#settings-llm-save");
  refs.settingsLlmClearKey = settingsView.querySelector("#settings-llm-clear-key");
  refs.settingsLoginPanel = settingsView.querySelector("#settings-login-panel");
  refs.settingsLoginTitle = settingsView.querySelector("#settings-login-title");
  refs.settingsLoginNote = settingsView.querySelector("#settings-login-note");
  refs.settingsLoginStatus = settingsView.querySelector("#settings-login-status");
  refs.settingsLoginShell = settingsView.querySelector("#settings-login-shell");
  refs.settingsLoginClose = settingsView.querySelector("#settings-login-close");
  refs.settingsLoginSave = settingsView.querySelector("#settings-login-save");
}

function bindEvents() {
  refs.navPush?.addEventListener("click", () => {
    activatePage("push");
  });

  refs.navHot?.addEventListener("click", () => {
    activatePage("hot");
  });

  refs.navSearch?.addEventListener("click", () => {
    activatePage("search");
  });

  refs.navSettings?.addEventListener("click", () => {
    activatePage("settings");
  });

  refs.searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void loadSearch(refs.searchInput?.value || "");
  });

  refs.refreshButton.addEventListener("click", () => {
    void refreshCurrentPage();
  });

  refs.openButton.addEventListener("click", async () => {
    await openExternalForItem(selectedItem());
  });

  refs.copyButton.addEventListener("click", async () => {
    await copyLinkForItem(selectedItem());
  });

  refs.readerButton.addEventListener("click", () => {
    openReader(selectedItem()?.id ?? null);
  });

  refs.readerBackButton.addEventListener("click", () => {
    state.view = state.readerPage;
    syncStatusForPage(state.view);
    render();
  });

  refs.readerOpenButton.addEventListener("click", async () => {
    const item = selectedItem(state.readerPage);
    const url = resolveReaderExternalUrl(item);
    if (!url) {
      return;
    }
    await bridge.openExternal(url);
  });

  refs.readerCopyButton.addEventListener("click", async () => {
    const item = selectedItem(state.readerPage);
    const url = resolveReaderExternalUrl(item);
    if (!url) {
      return;
    }
    await bridge.copyText(url);
    showToast("链接已复制");
  });

  refs.settingsLoginClose?.addEventListener("click", () => {
    closeSettingsLogin();
  });

  refs.settingsLoginSave?.addEventListener("click", () => {
    void commitSettingsLogin();
  });

  refs.settingsLlmUrl?.addEventListener("input", (event) => {
    state.settingsLlm.apiUrl = event.target?.value || "";
  });

  refs.settingsLlmModel?.addEventListener("input", (event) => {
    state.settingsLlm.model = event.target?.value || "";
  });

  refs.settingsLlmApiKey?.addEventListener("input", (event) => {
    state.settingsLlm.apiKey = event.target?.value || "";
  });

  refs.settingsLlmSave?.addEventListener("click", () => {
    void saveSettingsLlmConfig({ clearApiKey: false });
  });

  refs.settingsLlmClearKey?.addEventListener("click", () => {
    void saveSettingsLlmConfig({ clearApiKey: true });
  });
}

async function openExternalForItem(item) {
  if (!item?.canonicalUrl) {
    return;
  }
  await bridge.openExternal(item.canonicalUrl);
}

async function copyLinkForItem(item) {
  if (!item?.canonicalUrl) {
    return;
  }
  await bridge.copyText(item.canonicalUrl);
  showToast("链接已复制");
}

function resolveReaderExternalUrl(item) {
  if (!item) {
    return "";
  }
  const reader = item.reader || {};
  if (item.source === "zhihu" && reader.entityType === "question") {
    const readerBlocks = normalizeReaderContentBlocks(reader.contentBlocks || []);
    const answers = normalizeZhihuQuestionAnswers(reader, readerBlocks);
    const rememberedAnswerId = state.readerAnswerSelection?.itemId === item.id
      ? String(state.readerAnswerSelection.answerId || "").trim()
      : "";
    const preferredIds = [
      rememberedAnswerId,
      String(reader.defaultAnswerId || "").trim(),
      String(reader.commentSourceAnswerId || "").trim(),
    ].filter(Boolean);
    for (const answerId of preferredIds) {
      const found = answers.find((answer) => answer.answerId === answerId);
      if (found?.canonicalUrl) {
        return found.canonicalUrl;
      }
    }
  }
  return item.canonicalUrl || reader.canonicalUrl || "";
}

function activatePage(page) {
  if (page !== "push" && page !== "hot" && page !== "search" && page !== "settings") {
    return;
  }
  if (page !== "search") {
    clearSearchReviewPoll();
  }
  state.view = page;
  syncStatusForPage(page);
  render();
  if (page === "search") {
    refs.searchInput?.focus();
    return;
  }
  if (page === "settings") {
    if (
      Object.keys(state.settingsAuth.sources || {}).length > 0 &&
      (state.settingsLlm.apiUrl || state.settingsLlm.model || state.settingsLlm.apiKeyPresent)
    ) {
      return;
    }
    void loadSettingsAuth();
    return;
  }
  if (activePayload(page)) {
    return;
  }
  if (page === "hot") {
    void loadHot();
    return;
  }
  void loadPush({ ensureCurrent: false });
}

async function loadSettingsAuth() {
  if (!bridge?.getSettingsAuthStatus) {
    return;
  }
  state.isLoading = true;
  state.settingsAuth.loading = true;
  state.settingsLlm.loading = true;
  syncStatusForPage("settings");
  render();
  try {
    const [authResult, llmResult] = await Promise.all([
      bridge.getSettingsAuthStatus(),
      bridge?.getSettingsLlmConfig ? bridge.getSettingsLlmConfig() : Promise.resolve(null),
    ]);
    if (!authResult?.ok) {
      throw new Error(authResult?.error || "settings_auth_status_failed");
    }
    const payload = authResult.payload || {};
    state.settingsAuth.updatedAt = String(payload.updatedAtIso || "");
    state.settingsAuth.sources = payload.sources || {};
    if (llmResult?.ok) {
      applySettingsLlmPayload(llmResult.payload || {});
    }
  } catch (error) {
    setStatus("登录状态读取失败。", String(error.message || error));
  } finally {
    state.isLoading = false;
    state.settingsAuth.loading = false;
    state.settingsLlm.loading = false;
    syncStatusForPage(currentListPage());
    render();
  }
}

async function loadPush({ ensureCurrent }) {
  if (!bridge) {
    return;
  }
  state.isLoading = true;
  render();
  try {
    const result = await bridge.getPush({ ensureCurrent });
    if (!result?.ok) {
      throw new Error(result?.error || "push_get_failed");
    }
    applyPayload(result.payload);
  } catch (error) {
    setStatus("推送读取失败。", String(error.message || error));
  } finally {
    state.isLoading = false;
    render();
    schedulePushPollIfNeeded();
  }
}

async function loadHot() {
  if (!bridge) {
    return;
  }
  state.isLoading = true;
  render();
  try {
    const result = await bridge.getHot();
    if (!result?.ok) {
      throw new Error(result?.error || "hot_get_failed");
    }
    applyPayload(result.payload);
  } catch (error) {
    setStatus("热门读取失败。", String(error.message || error));
  } finally {
    state.isLoading = false;
    render();
  }
}

async function loadSearch(query) {
  if (!bridge) {
    return;
  }
  const keyword = normalizeSearchQuery(query);
  state.searchQuery = keyword;
  if (refs.searchInput && refs.searchInput.value !== keyword) {
    refs.searchInput.value = keyword;
  }
  if (!keyword) {
    applyPayload({
      page: "search",
      items: [],
      meta: {
        query: "",
        itemCount: 0,
        sourceCounts: {},
        sourceErrors: {},
        cacheStatus: "empty",
        statusText: "输入关键词开始搜索。",
      },
      review: {
        status: "idle",
        summary: "",
        groups: [],
        keptItemUids: [],
        droppedItemUids: [],
      },
    });
    render();
    return;
  }

  state.isLoading = true;
  setStatus(`正在搜索 “${keyword}”…`, metaText("search"));
  render();
  try {
    const result = await bridge.searchContent({ query: keyword });
    if (!result?.ok) {
      throw new Error(result?.error || "search_get_failed");
    }
    applyPayload(result.payload);
  } catch (error) {
    setStatus(`搜索 “${keyword}” 失败。`, String(error.message || error));
  } finally {
    state.isLoading = false;
    render();
  }
}

async function refreshCurrentPage() {
  const page = currentListPage();
  if (page === "settings") {
    await loadSettingsAuth();
    return;
  }
  if (page === "search") {
    await refreshSearch();
    return;
  }
  if (page === "hot") {
    await refreshHot();
    return;
  }
  await refreshPush();
}

async function refreshSearch() {
  if (!bridge || state.isRefreshing) {
    return;
  }
  const keyword = normalizeSearchQuery(state.searchQuery || refs.searchInput?.value || "");
  if (!keyword) {
    activatePage("search");
    return;
  }

  state.isRefreshing = true;
  setStatus(`正在重新搜索 “${keyword}”…`, metaText("search"));
  render();
  try {
    const result = await bridge.searchContent({ query: keyword, force: true });
    if (!result?.ok) {
      throw new Error(result?.error || "search_get_failed");
    }
    applyPayload(result.payload);
    showToast("搜索结果已更新");
  } catch (error) {
    showToast("搜索刷新失败");
    setStatus(`搜索 “${keyword}” 失败。`, String(error.message || error));
  } finally {
    state.isRefreshing = false;
    render();
  }
}

async function refreshPush() {
  if (!bridge || state.isRefreshing) {
    return;
  }
  state.isRefreshing = true;
  setStatus("正在切换下一组推送...", metaText());
  render();
  try {
    const result = await bridge.refreshPush();
    if (!result?.ok) {
      throw new Error(result?.error || "push_refresh_failed");
    }
    applyPayload(result.payload);
    showToast("已切换下一组推送");
  } catch (error) {
    showToast("刷新失败");
    setStatus("刷新失败。", String(error.message || error));
  } finally {
    state.isRefreshing = false;
    render();
    schedulePushPollIfNeeded();
  }
}

async function refreshHot() {
  if (!bridge || state.isRefreshing) {
    return;
  }
  state.isRefreshing = true;
  setStatus("正在刷新热门内容...", metaText("hot"));
  render();
  try {
    const result = await bridge.refreshHot();
    if (!result?.ok) {
      throw new Error(result?.error || "hot_refresh_failed");
    }
    applyPayload(result.payload);
    showToast("热门已刷新");
  } catch (error) {
    showToast("热门刷新失败");
    setStatus("热门刷新失败。", String(error.message || error));
  } finally {
    state.isRefreshing = false;
    render();
  }
}

function applyPayload(payload) {
  clearPoll();
  const page = payload?.page === "hot" ? "hot" : payload?.page === "search" ? "search" : "push";
  if (page === "hot") {
    state.hotPayload = payload || { page, items: [], meta: {} };
  } else if (page === "search") {
    state.searchPayload = payload || { page, items: [], meta: {} };
    state.searchQuery = normalizeSearchQuery(payload?.meta?.query || state.searchQuery);
    if (refs.searchInput && refs.searchInput.value !== state.searchQuery) {
      refs.searchInput.value = state.searchQuery;
    }
  } else {
    state.pushPayload = payload || { page, items: [], meta: {} };
  }
  const items = normalizedItems(page);
  const selectedId = state.selectedIds[page];
  if (items.length > 0) {
    const stillExists = items.some((item) => item.id === selectedId);
    state.selectedIds[page] = stillExists ? selectedId : items[0].id;
  } else {
    state.selectedIds[page] = null;
  }

  const meta = activePayload(currentListPage())?.meta || {};
  setStatus(meta.statusText || "推送已更新。", metaText());
  if (page === "search") {
    ensureSearchReviewPolling();
  }
}

function currentListPage() {
  return state.view === "reader" ? state.readerPage : state.view;
}

function activePayload(page = currentListPage()) {
  if (page === "settings") {
    return null;
  }
  if (page === "hot") {
    return state.hotPayload;
  }
  if (page === "search") {
    return state.searchPayload;
  }
  return state.pushPayload;
}

function normalizedItems(page = currentListPage()) {
  const items = activePayload(page)?.items;
  return Array.isArray(items) ? items : [];
}

function selectedItem(page = currentListPage()) {
  const selectedId = state.selectedIds[page];
  return normalizedItems(page).find((item) => item.id === selectedId) || null;
}

function openReader(itemId) {
  if (!itemId) {
    return;
  }
  const page = currentListPage();
  state.selectedIds[page] = itemId;
  state.readerPage = page;
  state.view = "reader";
  render();
}

function render() {
  renderNav();
  renderActiveView();
  renderToolbar();
  renderCards();
  renderSearchReview();
  renderSearchResults();
  renderDetail();
  renderSettingsView();
  renderReaderView();
}

function renderNav() {
  const activePage = currentListPage();
  refs.navPush?.classList.toggle("is-active", activePage === "push");
  refs.navHot?.classList.toggle("is-active", activePage === "hot");
  refs.navSearch?.classList.toggle("is-active", activePage === "search");
  refs.navSettings?.classList.toggle("is-active", activePage === "settings");
}

function renderActiveView() {
  const showingReader = state.view === "reader";
  refs.pushView.classList.toggle("is-hidden", showingReader);
  refs.readerView.classList.toggle("is-hidden", !showingReader);
  const showingSearch = currentListPage() === "search" && !showingReader;
  const showingSettings = currentListPage() === "settings" && !showingReader;
  refs.cardGrid.classList.toggle("is-hidden", showingSearch || showingSettings);
  refs.searchView?.classList.toggle("is-hidden", !showingSearch);
  refs.settingsView?.classList.toggle("is-hidden", !showingSettings);
  refs.detailPanel?.classList.toggle("is-hidden", showingSettings);
}

function renderToolbar() {
  const page = currentListPage();
  const hasSearchQuery = normalizeSearchQuery(state.searchQuery || refs.searchInput?.value || "").length > 0;
  refs.refreshButton.disabled =
    state.isRefreshing ||
    state.isLoading ||
    state.view === "reader" ||
    (page === "search" && !hasSearchQuery);
  refs.refreshButton.textContent = state.isRefreshing ? "…" : "↻";
}

function renderCards() {
  const page = currentListPage();
  if (page === "settings") {
    refs.cardGrid.innerHTML = "";
    return;
  }
  const items = normalizedItems(page);
  const cardLimit = page === "hot" ? HOT_CARD_LIMIT : PUSH_CARD_LIMIT;
  refs.cardGrid.innerHTML = "";

  if (state.isLoading && items.length === 0) {
    for (let index = 0; index < cardLimit; index += 1) {
      refs.cardGrid.appendChild(
        createPlaceholderCard(page === "hot" ? "正在准备热门内容..." : "正在准备推送...")
      );
    }
    return;
  }

  const visibleItems = items.slice(0, cardLimit);
  for (const item of visibleItems) {
    refs.cardGrid.appendChild(createCard(item));
  }
  for (let index = visibleItems.length; index < cardLimit; index += 1) {
    refs.cardGrid.appendChild(
      createPlaceholderCard(
        page === "hot" ? "等待热门内容..." : index === 0 ? "等待第一组推送..." : "后台继续补充中..."
      )
    );
  }
}

function createCard(item) {
  const page = currentListPage();
  const button = document.createElement("button");
  button.type = "button";
  button.className = `card ${item.id === state.selectedIds[page] ? "is-selected" : ""}`;
  button.addEventListener("click", () => {
    state.selectedIds[page] = item.id;
    render();
  });
  button.addEventListener("dblclick", () => {
    openReader(item.id);
  });

  const labels = Array.isArray(item.labels) ? item.labels : [];
  button.innerHTML = `
    <div class="card-top">
      <span class="card-source">${escapeHtml(item.sourceLabel || item.source || "")}</span>
      <span class="card-level card-level-${escapeHtml(item.recommendationKey || "worth-reading")}">
        ${escapeHtml(item.recommendationLevel || "值得看")}
      </span>
    </div>
    <h3>${escapeHtml(item.title || "")}</h3>
    <p class="card-summary">${escapeHtml(item.summary || "")}</p>
    <p class="card-reason">${escapeHtml(item.reason || "")}</p>
    <div class="card-foot">
      <div class="card-labels">
        ${labels.map((label) => `<span class="card-chip">${escapeHtml(label)}</span>`).join("")}
      </div>
      <span class="card-time">${escapeHtml(item.updatedAt || "")}</span>
    </div>
  `;
  return button;
}

function createPlaceholderCard(text) {
  const element = document.createElement("div");
  element.className = "card card-placeholder";
  element.innerHTML = `<p>${escapeHtml(text)}</p>`;
  return element;
}

function renderSearchReview() {
  if (!refs.searchReview) {
    return;
  }
  const page = currentListPage();
  const keyword = normalizeSearchQuery(state.searchQuery || activePayload("search")?.meta?.query || "");
  if (page !== "search" || !keyword) {
    refs.searchReview.classList.add("is-hidden");
    refs.searchReviewGroups.innerHTML = "";
    return;
  }

  const payload = activePayload("search") || {};
  const review = payload.review || {};
  const status = String(review.status || "pending").trim();
  refs.searchReview.classList.remove("is-hidden");
  refs.searchReviewGroups.innerHTML = "";

  if (status === "completed") {
    refs.searchReviewKicker.textContent = "AI整理完成";
    refs.searchReviewMeta.textContent = `${Number(review.keptItemCount || 0)} / ${Number(review.rawItemCount || 0)} 条`;
    refs.searchReviewSummary.textContent =
      review.summary || "已完成当前搜索结果整理。";
    for (const group of Array.isArray(review.groups) ? review.groups : []) {
      if (!group || typeof group !== "object") {
        continue;
      }
      refs.searchReviewGroups.appendChild(createSearchReviewGroup(group));
    }
    return;
  }

  if (status === "disabled") {
    refs.searchReviewKicker.textContent = "AI整理未启用";
    refs.searchReviewMeta.textContent = "";
    refs.searchReviewSummary.textContent = "当前没有可用的模型配置，先显示即时搜索结果。";
    return;
  }

  if (status === "failed") {
    refs.searchReviewKicker.textContent = "AI整理失败";
    refs.searchReviewMeta.textContent = "";
    refs.searchReviewSummary.textContent = "这次先显示即时搜索结果。";
    return;
  }

  refs.searchReviewKicker.textContent = "AI整理中";
  refs.searchReviewMeta.textContent = "";
  refs.searchReviewSummary.textContent = "正在整理这次搜索结果，稍后会补上分组和更相关的结果。";
}

function createSearchReviewGroup(group) {
  const element = document.createElement("section");
  element.className = "search-review-group";

  const title = document.createElement("h3");
  title.className = "search-review-group-title";
  title.textContent = String(group.title || "").trim();

  const summary = document.createElement("p");
  summary.className = "search-review-group-summary";
  summary.textContent = String(group.summary || "").trim();

  const count = document.createElement("p");
  count.className = "search-review-group-count";
  count.textContent = `${Array.isArray(group.itemUids) ? group.itemUids.length : 0} 条`;

  element.appendChild(title);
  element.appendChild(summary);
  element.appendChild(count);
  return element;
}

function renderSearchResults() {
  if (!refs.searchResults) {
    return;
  }
  const page = currentListPage();
  if (page !== "search") {
    refs.searchResults.innerHTML = "";
    return;
  }

  const items = normalizedItems("search");
  refs.searchResults.innerHTML = "";
  const keyword = normalizeSearchQuery(state.searchQuery || activePayload("search")?.meta?.query || "");

  if (state.isLoading && items.length === 0) {
    refs.searchResults.appendChild(createSearchEmpty("正在搜索..."));
    return;
  }
  if (!keyword) {
    refs.searchResults.appendChild(createSearchEmpty("输入关键词开始搜索。"));
    return;
  }
  if (items.length === 0) {
    refs.searchResults.appendChild(createSearchEmpty("没有命中结果。"));
    return;
  }

  for (const item of items) {
    refs.searchResults.appendChild(createSearchResultItem(item));
  }
}

function createSearchResultItem(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `search-item ${item.id === state.selectedIds.search ? "is-selected" : ""}`;
  button.addEventListener("click", () => {
    state.selectedIds.search = item.id;
    render();
  });
  button.addEventListener("dblclick", () => {
    openReader(item.id);
  });

  const labels = Array.isArray(item.labels) ? item.labels : [];
  button.innerHTML = `
    <div class="search-item-top">
      <span class="card-source">${escapeHtml(item.sourceLabel || item.source || "")}</span>
      <span class="card-time">${escapeHtml(item.updatedAt || "")}</span>
    </div>
    <h3 class="search-item-title">${escapeHtml(item.title || "")}</h3>
    <p class="search-item-summary">${escapeHtml(item.summary || "")}</p>
    <div class="search-item-foot">
      <p class="search-item-reason">${escapeHtml(item.reason || "")}</p>
      <div class="card-labels">
        ${labels.map((label) => `<span class="card-chip">${escapeHtml(label)}</span>`).join("")}
      </div>
    </div>
  `;
  return button;
}

function createSearchEmpty(text) {
  const element = document.createElement("div");
  element.className = "search-empty";
  element.textContent = text;
  return element;
}

function applySettingsLlmPayload(payload) {
  state.settingsLlm.apiUrl = String(payload.apiUrl || "").trim();
  state.settingsLlm.model = String(payload.model || "").trim();
  state.settingsLlm.apiKey = "";
  state.settingsLlm.apiKeyPresent = Boolean(payload.apiKeyPresent);
  state.settingsLlm.apiKeySource = String(payload.apiKeySource || "none").trim() || "none";
  state.settingsLlm.hasStoredApiKey = Boolean(payload.hasStoredApiKey);
  state.settingsLlm.settingsPath = String(payload.settingsPath || "").trim();
}

async function saveSettingsLlmConfig({ clearApiKey }) {
  if (!bridge?.saveSettingsLlmConfig || state.settingsLlm.saving) {
    return;
  }
  state.settingsLlm.saving = true;
  syncStatusForPage("settings");
  render();
  try {
    const result = await bridge.saveSettingsLlmConfig({
      apiUrl: state.settingsLlm.apiUrl,
      model: state.settingsLlm.model,
      apiKey: state.settingsLlm.apiKey,
      clearApiKey: Boolean(clearApiKey),
    });
    if (!result?.ok) {
      throw new Error(result?.error || "settings_llm_config_save_failed");
    }
    applySettingsLlmPayload(result.payload || {});
    showToast(clearApiKey ? "已清空保存的 Key" : "LLM 配置已保存");
    syncStatusForPage("settings");
  } catch (error) {
    setStatus("LLM 配置保存失败。", String(error.message || error));
  } finally {
    state.settingsLlm.saving = false;
    render();
  }
}

function renderSettingsView() {
  if (!refs.settingsView || !refs.settingsAuthList) {
    return;
  }
  if (currentListPage() !== "settings" || state.view === "reader") {
    return;
  }

  refs.settingsAuthList.innerHTML = "";
  const sources = state.settingsAuth.sources || {};
  for (const source of SETTINGS_SOURCE_ORDER) {
    refs.settingsAuthList.appendChild(createSettingsSourceCard(source, sources[source] || null));
  }

  if (refs.settingsLlmUrl && refs.settingsLlmUrl.value !== state.settingsLlm.apiUrl) {
    refs.settingsLlmUrl.value = state.settingsLlm.apiUrl;
  }
  if (refs.settingsLlmModel && refs.settingsLlmModel.value !== state.settingsLlm.model) {
    refs.settingsLlmModel.value = state.settingsLlm.model;
  }
  if (refs.settingsLlmApiKey && refs.settingsLlmApiKey.value !== state.settingsLlm.apiKey) {
    refs.settingsLlmApiKey.value = state.settingsLlm.apiKey;
  }
  if (refs.settingsLlmMeta) {
    refs.settingsLlmMeta.textContent = settingsLlmMetaText();
  }
  if (refs.settingsLlmSave) {
    refs.settingsLlmSave.disabled = state.settingsLlm.loading || state.settingsLlm.saving;
    refs.settingsLlmSave.textContent = state.settingsLlm.saving ? "保存中..." : "保存配置";
  }
  if (refs.settingsLlmClearKey) {
    refs.settingsLlmClearKey.disabled =
      state.settingsLlm.loading || state.settingsLlm.saving || !state.settingsLlm.hasStoredApiKey;
  }

  const login = state.settingsLogin;
  const isOpen = Boolean(login.open && login.source);
  refs.settingsLoginPanel?.classList.toggle("is-hidden", !isOpen);
  if (!isOpen) {
    if (refs.settingsLoginShell) {
      refs.settingsLoginShell.innerHTML = "";
    }
    if (refs.settingsLoginStatus) {
      refs.settingsLoginStatus.textContent = "";
    }
    return;
  }

  refs.settingsLoginTitle.textContent = `${login.label || login.source} 登录`;
  refs.settingsLoginNote.textContent = "在应用内完成登录，然后点击“保存登录”。";
  refs.settingsLoginStatus.textContent = login.error || login.statusText || "";
  refs.settingsLoginSave.disabled = login.loading;
  refs.settingsLoginSave.textContent = login.loading ? "保存中..." : "保存登录";
  refs.settingsLoginClose.disabled = login.loading;
  ensureSettingsLoginWebview(login);
}

function settingsLlmMetaText() {
  const llm = state.settingsLlm;
  const sourceLabel =
    llm.apiKeySource === "saved" ? "已保存到本地" : llm.apiKeySource === "env" ? "来自环境变量" : "未设置";
  const pathText = llm.settingsPath ? ` / ${llm.settingsPath}` : "";
  return `API Key：${sourceLabel}${pathText}`;
}

function createSettingsSourceCard(source, payload) {
  const auth = payload || {};
  const button = document.createElement("section");
  button.className = "settings-source-card";

  const info = document.createElement("div");
  info.className = "settings-source-info";

  const headline = document.createElement("div");
  headline.className = "settings-source-headline";

  const title = document.createElement("h3");
  title.className = "settings-source-title";
  title.textContent = auth.label || source;

  const statusPill = document.createElement("span");
  statusPill.className = `pill ${auth.authenticated ? "pill-strong" : ""}`;
  statusPill.textContent = auth.authenticated ? "已登录" : auth.available === false ? "未安装" : "未登录";

  const detail = document.createElement("p");
  detail.className = "settings-source-detail";
  detail.textContent = auth.statusText || (auth.available === false ? "未找到 CLI" : "尚未检测到登录状态");

  headline.appendChild(title);
  headline.appendChild(statusPill);
  info.appendChild(headline);
  info.appendChild(detail);

  const action = document.createElement("button");
  action.type = "button";
  action.className = "action-button action-primary";
  action.textContent = auth.authenticated ? "重新登录" : "登录";
  action.disabled = auth.available === false;
  action.addEventListener("click", () => {
    void beginSettingsLogin(source);
  });

  button.appendChild(info);
  button.appendChild(action);
  return button;
}

async function beginSettingsLogin(source) {
  if (!bridge?.getSettingsLoginSpec) {
    return;
  }
  state.settingsLogin.loading = true;
  state.settingsLogin.error = "";
  state.settingsLogin.statusText = "正在打开登录页...";
  state.settingsLogin.source = source;
  render();
  try {
    const result = await bridge.getSettingsLoginSpec(source);
    if (!result?.ok) {
      throw new Error(result?.error || "settings_login_spec_failed");
    }
    const payload = result.payload || {};
    state.settingsLogin = {
      open: true,
      source: payload.source || source,
      label: payload.label || source,
      url: payload.url || "",
      partition: payload.partition || "",
      loading: false,
      error: "",
      statusText: "请在页面内完成登录，然后点击“保存登录”。",
    };
  } catch (error) {
    state.settingsLogin = {
      open: false,
      source: "",
      label: "",
      url: "",
      partition: "",
      loading: false,
      error: "",
      statusText: "",
    };
    showToast("登录页打开失败");
    setStatus("登录页打开失败。", String(error.message || error));
  } finally {
    render();
  }
}

function closeSettingsLogin() {
  state.settingsLogin = {
    open: false,
    source: "",
    label: "",
    url: "",
    partition: "",
    loading: false,
    error: "",
    statusText: "",
  };
  render();
}

function ensureSettingsLoginWebview(login) {
  if (!refs.settingsLoginShell || !login?.url || !login?.partition) {
    return;
  }
  const current = refs.settingsLoginShell.querySelector("webview");
  if (current?.dataset?.source === login.source) {
    return;
  }
  refs.settingsLoginShell.innerHTML = "";
  const webview = document.createElement("webview");
  webview.className = "settings-login-webview";
  webview.dataset.source = login.source;
  webview.partition = login.partition;
  webview.setAttribute("useragent", SETTINGS_LOGIN_WEBVIEW_UA);
  webview.src = login.url;
  refs.settingsLoginShell.appendChild(webview);
}

async function commitSettingsLogin() {
  if (!bridge?.commitSourceLogin || !state.settingsLogin.source || state.settingsLogin.loading) {
    return;
  }
  state.settingsLogin.loading = true;
  state.settingsLogin.error = "";
  state.settingsLogin.statusText = "正在保存登录状态...";
  render();
  try {
    const result = await bridge.commitSourceLogin(state.settingsLogin.source);
    if (!result?.ok) {
      throw new Error(result?.error || "settings_commit_login_failed");
    }
    await loadSettingsAuth();
    closeSettingsLogin();
    showToast("登录状态已保存");
  } catch (error) {
    state.settingsLogin.loading = false;
    state.settingsLogin.error = String(error.message || error);
    state.settingsLogin.statusText = "";
    render();
  }
}

function renderDetail() {
  const page = currentListPage();
  if (page === "settings") {
    return;
  }
  const item = selectedItem(page);
  if (!item) {
    if (page === "search") {
      const keyword = normalizeSearchQuery(state.searchQuery || activePayload("search")?.meta?.query || "");
      refs.detailEmpty.textContent = state.isLoading
        ? `正在搜索 “${keyword || "当前关键词"}”…`
        : keyword
          ? "没有找到可查看的结果。"
          : "输入关键词开始搜索...";
      refs.detailEmpty.classList.remove("is-hidden");
      refs.detailContent.classList.add("is-hidden");
      refs.readerButton.disabled = true;
      return;
    }
    refs.detailEmpty.textContent = state.isLoading
      ? page === "hot"
        ? "正在准备热门内容..."
        : "正在准备第一组推送..."
      : page === "hot"
        ? "等待热门内容..."
        : "等待第一组推送...";
    refs.detailEmpty.classList.remove("is-hidden");
    refs.detailContent.classList.add("is-hidden");
    refs.readerButton.disabled = true;
    return;
  }

  refs.detailEmpty.classList.add("is-hidden");
  refs.detailContent.classList.remove("is-hidden");
  refs.detailSource.textContent = item.sourceLabel || item.source || "";
  refs.detailLevel.textContent = item.recommendationLevel || "值得看";
  refs.detailAuthor.textContent = item.authorName || "未知作者";
  refs.detailTitle.textContent = item.title || "";
  refs.detailSummary.textContent = item.summary || "";
  refs.detailReason.textContent = item.reason || "";
  refs.detailLabels.innerHTML = "";
  refs.detailSections.innerHTML = "";
  refs.readerButton.disabled = !item.reader;

  for (const label of item.labels || []) {
    const chip = document.createElement("span");
    chip.className = "pill";
    chip.textContent = label;
    refs.detailLabels.appendChild(chip);
  }

  const sections = item.structured?.sections || [];
  for (const section of sections) {
    if (!section?.body) {
      continue;
    }
    const block = document.createElement("section");
    block.className = "detail-section";
    block.innerHTML = `
      <h3>${escapeHtml(section.title || "摘录")}</h3>
      <p>${escapeHtml(section.body)}</p>
    `;
    refs.detailSections.appendChild(block);
  }
}

function renderReaderView() {
  const item = selectedItem(state.readerPage);
  if (state.view !== "reader") {
    state.readerComments = null;
    state.readerAnswerSelection = null;
    refs.readerBody.innerHTML = "";
    return;
  }
  if (!item) {
    state.readerComments = null;
    state.readerAnswerSelection = null;
    state.view = state.readerPage;
    render();
    return;
  }

  const reader = item.reader || {};
  const readerBlocks = normalizeReaderContentBlocks(reader.contentBlocks || []);
  const questionAnswers = item.source === "zhihu" && reader.entityType === "question"
    ? normalizeZhihuQuestionAnswers(reader, readerBlocks)
    : [];
  const selectedAnswer = questionAnswers.length > 0
    ? ensureSelectedZhihuQuestionAnswer(item, reader, questionAnswers)
    : null;
  refs.readerSource.textContent = item.sourceLabel || item.source || "";
  refs.readerLevel.textContent = item.recommendationLevel || "值得看";
  refs.readerAuthor.textContent = resolveReaderAuthorText(item, reader, questionAnswers, selectedAnswer);
  refs.readerTitle.textContent = item.title || reader.title || "";
  refs.readerSummary.textContent = item.summary || "暂无摘要";
  refs.readerReason.textContent = item.reason || "暂无推送理由";
  refs.readerLabels.innerHTML = "";
  refs.readerBody.innerHTML = "";

  for (const label of item.labels || []) {
    const chip = document.createElement("span");
    chip.className = "pill";
    chip.textContent = label;
    refs.readerLabels.appendChild(chip);
  }

  appendReaderMeta(reader);
  if (item.source === "bilibili") {
    appendBilibiliReader(item, reader);
  } else if (item.source === "zhihu") {
    if (reader.entityType === "question") {
      appendZhihuQuestionReader(item, reader, readerBlocks, questionAnswers, selectedAnswer);
    } else if (readerBlocks.length > 0) {
      appendContentBlocksSection("正文", readerBlocks, { source: item.source });
    } else {
      appendTextSection("正文", reader.bodyText || reader.excerptText || "暂无正文", { source: item.source });
    }
  } else {
    appendTextSection("正文", reader.bodyText || reader.excerptText || "暂无正文", { source: item.source });
  }

  const readerImages = normalizeReaderImages(reader.media?.images || []);
  if (item.source === "xiaohongshu" && readerImages.length > 0) {
    appendImageGallery("图片", readerImages);
  } else if (item.source === "zhihu" && readerBlocks.length === 0 && readerImages.length > 0) {
    appendImageGallery("图片", readerImages);
  } else if (item.source === "xiaohongshu" && Number(reader.media?.imageCount || 0) > 0) {
    appendInfoSection("图片", `该笔记包含 ${Number(reader.media.imageCount)} 张图片，但当前未抓到可渲染的图片地址。`);
  }

  if (item.source === "zhihu" && Array.isArray(reader.topics) && reader.topics.length > 0) {
    appendChipSection("话题", reader.topics);
  }

  if (item.source === "bilibili" && reader.bodyText) {
    appendTextSection("简介", reader.bodyText, { source: item.source });
  }

  appendDynamicCommentsSection(item, reader, selectedAnswer);
}

function appendReaderMeta(reader) {
  const lines = [];
  if (reader.statsText) {
    lines.push(reader.statsText);
  }
  if (reader.entityType) {
    lines.push(entityTypeLabel(reader.entityType));
  }
  if (reader.publishedAt) {
    lines.push(formatDateTime(reader.publishedAt));
  }
  if (Number(reader.media?.durationSeconds || 0) > 0) {
    lines.push(`时长 ${formatDuration(Number(reader.media.durationSeconds))}`);
  }
  if (lines.length === 0) {
    return;
  }
  appendInfoSection("内容信息", lines.join(" / "));
}

function appendBilibiliReader(item, reader) {
  const section = createReaderSection("播放");
  const shell = document.createElement("div");
  shell.className = "reader-webview-shell";

  const webview = document.createElement("webview");
  webview.className = "reader-webview";
  webview.src = reader?.canonicalUrl || item?.canonicalUrl || buildBilibiliPlayerUrl(item, reader);
  webview.partition = "persist:stream-curator";
  webview.setAttribute("allowpopups", "true");
  wireBilibiliWebview(webview);
  shell.appendChild(webview);

  const note = document.createElement("p");
  note.className = "reader-note";
  note.textContent = "B 站阅读页现在直接加载原始视频页，用来绕过外链播放器的 360p 限制。若加载异常，可点右上角打开原站。";

  section.appendChild(shell);
  section.appendChild(note);
  refs.readerBody.appendChild(section);

  if (!reader.bodyText && !reader.transcriptText) {
    appendInfoSection("内容说明", "如果后端没有抓到简介或字幕，B 站原页依然可以继续浏览视频。");
  }
}

function appendTextSection(title, text, options = {}) {
  if (!text) {
    return;
  }
  const section = createReaderSection(title);
  const block = document.createElement("div");
  block.className = "reader-text";
  block.textContent = formatReaderText(text, options);
  section.appendChild(block);
  refs.readerBody.appendChild(section);
}

function appendContentBlocksSection(title, blocks, options = {}) {
  if (!Array.isArray(blocks) || blocks.length === 0) {
    return;
  }
  const section = createReaderSection(title);
  const flow = document.createElement("div");
  flow.className = "reader-content-flow";

  for (const block of blocks) {
    if (!block || typeof block !== "object") {
      continue;
    }
    if (block.type === "text") {
      const text = formatReaderText(block.text || "", options);
      if (!text) {
        continue;
      }
      const textNode = document.createElement("div");
      textNode.className = "reader-text";
      textNode.textContent = text;
      flow.appendChild(textNode);
      continue;
    }
    if (block.type === "image") {
      const url = String(block.url || "").trim();
      if (!url) {
        continue;
      }
      flow.appendChild(createReaderImageCard(url, title));
    }
  }

  if (!flow.children.length) {
    return;
  }

  section.appendChild(flow);
  refs.readerBody.appendChild(section);
}

function appendZhihuQuestionReader(item, reader, readerBlocks, questionAnswers, selectedAnswer) {
  const questionDetailBlocks = normalizeZhihuQuestionDetailBlocks(reader, readerBlocks);
  if (questionDetailBlocks.length > 0) {
    appendContentBlocksSection("问题", questionDetailBlocks, { source: "zhihu" });
  }

  if (questionAnswers.length === 0) {
    appendTextSection("高赞回答", reader.bodyText || reader.excerptText || "暂无正文", { source: "zhihu" });
    return;
  }

  appendZhihuQuestionAnswerPicker(item, questionAnswers, selectedAnswer);

  if (!selectedAnswer) {
    appendInfoSection("回答", "当前没有可显示的回答。");
    return;
  }

  const stats = [];
  if (Number.isFinite(Number(selectedAnswer.likeCount)) && Number(selectedAnswer.likeCount) > 0) {
    stats.push(`赞同 ${Number(selectedAnswer.likeCount)}`);
  }
  if (Number.isFinite(Number(selectedAnswer.commentCount)) && Number(selectedAnswer.commentCount) > 0) {
    stats.push(`评论 ${Number(selectedAnswer.commentCount)}`);
  }
  if (stats.length > 0) {
    appendInfoSection("当前回答", stats.join(" / "));
  }

  const answerBlocks = normalizeReaderContentBlocks(selectedAnswer.contentBlocks || []);
  const answerTitle = selectedAnswer.heading || "当前回答";
  if (answerBlocks.length > 0) {
    appendContentBlocksSection(answerTitle, answerBlocks, { source: "zhihu" });
  } else {
    appendTextSection(answerTitle, selectedAnswer.bodyText || selectedAnswer.excerptText || "暂无正文", { source: "zhihu" });
  }
}

function appendZhihuQuestionAnswerPicker(item, answers, selectedAnswer) {
  const section = createReaderSection("回答列表");
  const list = document.createElement("div");
  list.className = "reader-answer-list";

  for (const answer of answers) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `reader-answer-item ${selectedAnswer?.answerId === answer.answerId ? "is-selected" : ""}`;
    button.addEventListener("click", () => {
      selectZhihuQuestionAnswer(item, answer.answerId);
    });

    const title = document.createElement("div");
    title.className = "reader-answer-title";
    title.textContent = answer.heading || answer.authorName || "回答";

    const excerpt = document.createElement("div");
    excerpt.className = "reader-answer-excerpt";
    excerpt.textContent = answer.excerptText || answer.bodyText || "暂无摘要";

    button.appendChild(title);
    button.appendChild(excerpt);
    list.appendChild(button);
  }

  section.appendChild(list);
  refs.readerBody.appendChild(section);
}

function normalizeZhihuQuestionDetailBlocks(reader, readerBlocks) {
  const explicitBlocks = normalizeReaderContentBlocks(reader.questionDetailBlocks || []);
  if (explicitBlocks.length > 0) {
    return explicitBlocks;
  }
  return groupZhihuQuestionBlocks(readerBlocks).promptBlocks;
}

function normalizeZhihuQuestionAnswers(reader, readerBlocks) {
  const answers = Array.isArray(reader.questionAnswers) ? reader.questionAnswers : [];
  const normalized = answers
    .map((answer) => normalizeZhihuQuestionAnswer(answer))
    .filter(Boolean);
  if (normalized.length > 0) {
    return normalized;
  }

  return groupZhihuQuestionBlocks(readerBlocks).answerSections.map((section, index) => ({
    answerId: "",
    heading: section.title || `回答 ${index + 1}`,
    authorName: extractZhihuAnswerAuthor(section.title),
    bodyText: section.blocks
      .filter((block) => block.type === "text")
      .map((block) => String(block.text || "").trim())
      .filter(Boolean)
      .join("\n\n"),
    excerptText: section.blocks
      .filter((block) => block.type === "text")
      .map((block) => String(block.text || "").trim())
      .filter(Boolean)
      .join(" ")
      .slice(0, 180),
    contentBlocks: section.blocks,
    commentCount: null,
    likeCount: null,
    canonicalUrl: "",
  }));
}

function normalizeZhihuQuestionAnswer(answer) {
  if (!answer || typeof answer !== "object") {
    return null;
  }
  const answerId = String(answer.answerId || "").trim();
  const heading = String(answer.heading || "").trim();
  const bodyText = String(answer.bodyText || "").trim();
  const excerptText = String(answer.excerptText || "").trim();
  const contentBlocks = normalizeReaderContentBlocks(answer.contentBlocks || []);
  if (!answerId && !heading && !bodyText && contentBlocks.length === 0) {
    return null;
  }
  return {
    answerId,
    heading: heading || "回答",
    authorName: String(answer.authorName || "").trim(),
    bodyText,
    excerptText,
    contentBlocks,
    commentCount: Number.isFinite(Number(answer.commentCount)) ? Number(answer.commentCount) : null,
    likeCount: Number.isFinite(Number(answer.likeCount)) ? Number(answer.likeCount) : null,
    canonicalUrl: String(answer.canonicalUrl || "").trim(),
  };
}

function groupZhihuQuestionBlocks(blocks) {
  const promptBlocks = [];
  const answerSections = [];
  let currentAnswer = null;

  for (const block of blocks) {
    if (!block || typeof block !== "object") {
      continue;
    }
    if (block.type === "text") {
      const text = String(block.text || "").trim();
      if (!text || text === "------") {
        continue;
      }
      if (isZhihuAnswerHeading(text)) {
        currentAnswer = { title: text, blocks: [] };
        answerSections.push(currentAnswer);
        continue;
      }
    }

    if (currentAnswer) {
      currentAnswer.blocks.push(block);
    } else {
      promptBlocks.push(block);
    }
  }

  return { promptBlocks, answerSections };
}

function isZhihuAnswerHeading(text) {
  return /^回答\s+\d+/.test(String(text || "").trim());
}

function extractZhihuAnswerAuthor(heading) {
  const text = String(heading || "").trim();
  const parts = text.split("·");
  return parts.length >= 2 ? parts.slice(1).join("·").trim() : "";
}

function ensureSelectedZhihuQuestionAnswer(item, reader, answers) {
  const remembered = state.readerAnswerSelection;
  if (remembered?.itemId === item.id) {
    const found = answers.find((answer) => answer.answerId && answer.answerId === remembered.answerId);
    if (found) {
      return found;
    }
  }

  const preferredIds = [
    String(reader.commentSourceAnswerId || "").trim(),
    String(reader.defaultAnswerId || "").trim(),
  ].filter(Boolean);
  for (const answerId of preferredIds) {
    const found = answers.find((answer) => answer.answerId === answerId);
    if (found) {
      state.readerAnswerSelection = { itemId: item.id, answerId: found.answerId };
      return found;
    }
  }

  const fallback = answers[0] || null;
  state.readerAnswerSelection = { itemId: item.id, answerId: fallback?.answerId || "" };
  return fallback;
}

function selectZhihuQuestionAnswer(item, answerId) {
  state.readerAnswerSelection = { itemId: item.id, answerId: String(answerId || "").trim() };
  state.readerComments = null;
  render();
}

function resolveReaderAuthorText(item, reader, questionAnswers, selectedAnswer) {
  if (item.source === "zhihu" && reader.entityType === "question") {
    const answerCount = questionAnswers.length;
    if (selectedAnswer?.authorName && answerCount > 0) {
      return `${selectedAnswer.authorName} / ${answerCount} 条回答`;
    }
    if (answerCount > 0) {
      return `${answerCount} 条回答`;
    }
  }
  return item.authorName || reader.authorName || "未知作者";
}

function appendImageGallery(title, images) {
  if (!Array.isArray(images) || images.length === 0) {
    return;
  }
  const section = createReaderSection(title);
  const grid = document.createElement("div");
  grid.className = "reader-image-grid";

  for (const imageUrl of images) {
    const url = String(imageUrl || "").trim();
    if (!url) {
      continue;
    }
    grid.appendChild(createReaderImageCard(url, title));
  }

  if (!grid.children.length) {
    return;
  }

  section.appendChild(grid);
  refs.readerBody.appendChild(section);
}

function createReaderImageCard(url, title) {
  const card = document.createElement("div");
  card.className = "reader-image-card";

  const img = document.createElement("img");
  img.className = "reader-image";
  img.src = url;
  img.alt = title;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";

  card.appendChild(img);
  return card;
}

function appendCommentsSection(comments) {
  if (!Array.isArray(comments) || comments.length === 0) {
    return;
  }

  const section = createReaderSection("评论");
  const list = document.createElement("div");
  list.className = "comment-list";

  for (const comment of comments) {
    if (!comment?.content) {
      continue;
    }
    const item = document.createElement("article");
    item.className = "comment-item";

    const header = document.createElement("div");
    header.className = "comment-header";

    const author = document.createElement("span");
    author.className = "comment-author";
    author.textContent = comment.authorName || "匿名";

    const likes = document.createElement("span");
    likes.className = "comment-like";
    likes.textContent = Number.isFinite(Number(comment.likeCount)) && Number(comment.likeCount) > 0 ? `赞 ${Number(comment.likeCount)}` : "";

    const body = document.createElement("p");
    body.className = "comment-body";
    body.textContent = comment.content;

    header.appendChild(author);
    if (likes.textContent) {
      header.appendChild(likes);
    }
    item.appendChild(header);
    item.appendChild(body);
    list.appendChild(item);
  }

  section.appendChild(list);
  refs.readerBody.appendChild(section);
}

function appendDynamicCommentsSection(item, reader, selectedAnswer = null) {
  const commentsState = ensureReaderCommentsState(item, reader, selectedAnswer);
  const section = createReaderSection("评论");
  const toolbar = document.createElement("div");
  toolbar.className = "comment-toolbar";

  const status = document.createElement("p");
  status.className = "comment-status";

  const actions = document.createElement("div");
  actions.className = "comment-actions";

  const topButton = document.createElement("button");
  topButton.className = "action-button comment-icon-button";
  topButton.type = "button";
  topButton.textContent = "\u2191";
  topButton.title = "回顶部";
  topButton.setAttribute("aria-label", "回顶部");
  topButton.addEventListener("click", () => {
    scrollReaderToTop();
  });

  const firstButton = document.createElement("button");
  firstButton.className = "action-button comment-icon-button";
  firstButton.type = "button";
  firstButton.textContent = "\u21d0";
  firstButton.title = "回到第一页";
  firstButton.setAttribute("aria-label", "回到第一页");
  firstButton.addEventListener("click", () => {
    void loadReaderCommentsPage("first");
  });

  const prevButton = document.createElement("button");
  prevButton.className = "action-button comment-icon-button";
  prevButton.type = "button";
  prevButton.textContent = "\u2190";
  prevButton.title = "上一页";
  prevButton.setAttribute("aria-label", "上一页");
  prevButton.addEventListener("click", () => {
    void loadReaderCommentsPage("prev");
  });

  const nextButton = document.createElement("button");
  nextButton.className = "action-button comment-icon-button";
  nextButton.type = "button";
  nextButton.textContent = "\u2192";
  nextButton.title = "下一页";
  nextButton.setAttribute("aria-label", "下一页");
  nextButton.addEventListener("click", () => {
    void loadReaderCommentsPage("next");
  });

  actions.appendChild(topButton);
  actions.appendChild(firstButton);
  actions.appendChild(prevButton);
  actions.appendChild(nextButton);
  toolbar.appendChild(status);
  toolbar.appendChild(actions);

  const list = document.createElement("div");
  list.className = "comment-list";

  section.appendChild(toolbar);
  section.appendChild(list);
  refs.readerBody.appendChild(section);

  commentsState.dom = {
    status,
    list,
    topButton,
    firstButton,
    prevButton,
    nextButton,
  };
  renderReaderCommentsSection();

  if (shouldAutoLoadReaderComments(commentsState)) {
    void loadReaderCommentsPage("initial");
  }
}

function ensureReaderCommentsState(item, reader, selectedAnswer = null) {
  const targetKey = buildReaderCommentsTargetKey(item, reader, selectedAnswer);
  if (state.readerComments?.targetKey === targetKey) {
    return state.readerComments;
  }

  const seedComments = normalizeReaderSeedComments(reader, selectedAnswer);
  const source = item.source || reader?.source || "";
  const commentTarget = resolveReaderCommentsTarget(item, reader, selectedAnswer);
  state.readerComments = {
    targetKey,
    itemId: item.id,
    source,
    entityType: commentTarget.entityType,
    sourceItemId: commentTarget.sourceItemId,
    canonicalUrl: commentTarget.canonicalUrl,
    pageSize: READER_COMMENT_PAGE_SIZE,
    pageNumber: 1,
    cursor: "",
    nextCursor: "",
    cursorStack: [""],
    comments: seedComments,
    loading: false,
    hasMore: false,
    hasLoadedOnce: false,
    error: "",
    message: bridge?.getReaderComments ? "" : "当前环境不支持分页评论加载。",
    requestId: 0,
    pageCache: {},
    dom: null,
  };
  state.readerComments.message = initialReaderCommentsMessage(source, seedComments.length > 0, commentTarget.entityType);
  return state.readerComments;
}

function buildReaderCommentsTargetKey(item, reader, selectedAnswer = null) {
  const source = item?.source || reader?.source || "";
  if (source === "zhihu" && reader?.entityType === "question") {
    return `${item.id}:${String(selectedAnswer?.answerId || reader?.defaultAnswerId || "").trim()}`;
  }
  return item.id;
}

function resolveReaderCommentsTarget(item, reader, selectedAnswer = null) {
  const source = item?.source || reader?.source || "";
  if (source === "zhihu" && reader?.entityType === "question" && selectedAnswer?.answerId) {
    return {
      entityType: "answer",
      sourceItemId: String(selectedAnswer.answerId || "").trim(),
      canonicalUrl: selectedAnswer.canonicalUrl || reader?.canonicalUrl || item?.canonicalUrl || "",
    };
  }
  return {
    entityType: reader?.entityType || "",
    sourceItemId: String(reader?.sourceItemId || parseSourceItemId(item) || "").trim(),
    canonicalUrl: reader?.canonicalUrl || item?.canonicalUrl || "",
  };
}

function normalizeReaderSeedComments(reader, selectedAnswer = null) {
  const commentSourceAnswerId = String(reader?.commentSourceAnswerId || "").trim();
  if (reader?.source === "zhihu" && reader?.entityType === "question") {
    if (!selectedAnswer) {
      return [];
    }
    if (commentSourceAnswerId && selectedAnswer.answerId !== commentSourceAnswerId) {
      return [];
    }
  }
  return normalizeReaderComments(reader?.comments || []);
}

function initialReaderCommentsMessage(source, hasSeedComments, entityType = "") {
  if (!bridge?.getReaderComments) {
    return "当前环境不支持分页评论加载。";
  }
  if (source === "bilibili") {
    return hasSeedComments
      ? "当前显示采集时缓存的评论，点右箭头加载更多。"
      : "当前未预取评论，点右箭头加载评论。";
  }
  if (source === "zhihu" && entityType === "answer" && hasSeedComments) {
    return "当前显示所选回答的缓��评论。";
  }
  return "";
}

function shouldAutoLoadReaderComments(commentsState) {
  if (!bridge?.getReaderComments || commentsState.loading || commentsState.hasLoadedOnce) {
    return false;
  }
  return commentsState.source !== "bilibili";
}

function canRequestNextReaderComments(commentsState) {
  if (!commentsState || commentsState.loading) {
    return false;
  }
  if (commentsState.source === "bilibili" && !commentsState.hasLoadedOnce) {
    return Boolean(commentsState.sourceItemId);
  }
  return Boolean(commentsState.hasMore && commentsState.nextCursor);
}

function normalizeReaderComments(comments) {
  if (!Array.isArray(comments)) {
    return [];
  }
  return comments
    .filter((comment) => comment?.content)
    .map((comment) => ({
      authorName: comment.authorName || "匿名",
      content: comment.content || "",
      likeCount: Number.isFinite(Number(comment.likeCount)) ? Number(comment.likeCount) : null,
    }));
}

function summarizeReaderCommentsError(source, errorText, hasCachedComments) {
  const raw = String(errorText || "").replace(/\s+/g, " ").trim();
  const lowered = raw.toLowerCase();

  if (source === "bilibili") {
    if (
      lowered.includes("412") ||
      lowered.includes("precondition failed") ||
      lowered.includes("security control policy") ||
      raw.includes("\u98ce\u63a7")
    ) {
      return hasCachedComments
        ? "\u0042\u7ad9\u8bc4\u8bba\u63a5\u53e3\u89e6\u53d1\u98ce\u63a7\uff0c\u5f53\u524d\u663e\u793a\u5df2\u7f13\u5b58\u8bc4\u8bba\u3002\u7a0d\u540e\u53ef\u518d\u8bd5\u3002"
        : "\u0042\u7ad9\u8bc4\u8bba\u63a5\u53e3\u89e6\u53d1\u98ce\u63a7\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002";
    }
    if (
      lowered.includes("not_authenticated") ||
      lowered.includes("re-login") ||
      raw.includes("\u91cd\u65b0\u767b\u5f55")
    ) {
      return "\u0042\u7ad9\u8bc4\u8bba\u767b\u5f55\u6001\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u3002";
    }
  }

  if (!raw) {
    return "\u8bc4\u8bba\u52a0\u8f7d\u5931\u8d25";
  }
  return raw.length <= 160 ? raw : `${raw.slice(0, 157)}...`;
}

async function loadReaderCommentsPage(mode) {
  const commentsState = state.readerComments;
  if (!commentsState || !bridge?.getReaderComments || commentsState.loading) {
    return;
  }
  if (!commentsState.sourceItemId) {
    commentsState.error = "评论元数据缺失。";
    renderReaderCommentsSection();
    return;
  }

  let nextCursorStack = commentsState.cursorStack.slice();
  let nextPageNumber = commentsState.pageNumber;
  let targetCursor = commentsState.cursor;

  if (mode === "next") {
    if (commentsState.source === "bilibili" && !commentsState.hasLoadedOnce) {
      targetCursor = "";
      nextCursorStack = [""];
      nextPageNumber = 1;
    } else {
      if (!commentsState.hasMore || !commentsState.nextCursor) {
        return;
      }
      targetCursor = commentsState.nextCursor;
      nextCursorStack = commentsState.cursorStack.concat([targetCursor]);
      nextPageNumber = commentsState.pageNumber + 1;
    }
  } else if (mode === "first") {
    if (commentsState.cursorStack.length <= 1) {
      return;
    }
    targetCursor = "";
    nextCursorStack = [""];
    nextPageNumber = 1;
  } else if (mode === "prev") {
    if (commentsState.cursorStack.length <= 1) {
      return;
    }
    nextCursorStack = commentsState.cursorStack.slice(0, -1);
    targetCursor = nextCursorStack[nextCursorStack.length - 1] || "";
    nextPageNumber = Math.max(1, commentsState.pageNumber - 1);
  } else {
    targetCursor = commentsState.cursorStack[commentsState.cursorStack.length - 1] || "";
  }

  const cacheKey = targetCursor || "__first_page__";
  const cachedPage = commentsState.pageCache?.[cacheKey];
  if (cachedPage) {
    commentsState.cursor = String(cachedPage.cursor || targetCursor || "");
    commentsState.nextCursor = String(cachedPage.nextCursor || "");
    commentsState.hasMore = Boolean(cachedPage.hasMore);
    commentsState.pageNumber = nextPageNumber;
    commentsState.cursorStack = nextCursorStack;
    commentsState.comments = normalizeReaderComments(cachedPage.comments || []);
    commentsState.message = String(cachedPage.message || "");
    commentsState.error = "";
    commentsState.hasLoadedOnce = true;
    renderReaderCommentsSection();
    return;
  }

  commentsState.loading = true;
  commentsState.error = "";
  if (mode === "initial") {
    commentsState.message = "";
  }
  renderReaderCommentsSection();

  const requestId = commentsState.requestId + 1;
  commentsState.requestId = requestId;

  try {
    const result = await bridge.getReaderComments({
      source: commentsState.source,
      entityType: commentsState.entityType,
      sourceItemId: commentsState.sourceItemId,
      canonicalUrl: commentsState.canonicalUrl,
      cursor: targetCursor,
      limit: commentsState.pageSize,
    });
    if (
      !state.readerComments ||
      state.readerComments.itemId !== commentsState.itemId ||
      state.readerComments.requestId !== requestId
    ) {
      return;
    }

    commentsState.loading = false;
    if (!result?.ok) {
      commentsState.error = summarizeReaderCommentsError(
        commentsState.source,
        result?.error || "comments_load_failed",
        commentsState.comments.length > 0
      );
      renderReaderCommentsSection();
      return;
    }

    const payload = result.payload || {};
    const normalizedComments = normalizeReaderComments(payload.comments || []);
    commentsState.cursor = String(payload.cursor || targetCursor || "");
    commentsState.nextCursor = String(payload.nextCursor || "");
    commentsState.hasMore = Boolean(payload.hasMore);
    commentsState.pageNumber = nextPageNumber;
    commentsState.cursorStack = nextCursorStack;
    commentsState.comments = normalizedComments;
    commentsState.message = String(payload.message || "");
    commentsState.hasLoadedOnce = true;
    commentsState.pageCache[cacheKey] = {
      cursor: commentsState.cursor,
      nextCursor: commentsState.nextCursor,
      hasMore: commentsState.hasMore,
      message: commentsState.message,
      comments: normalizedComments,
    };
    renderReaderCommentsSection();
  } catch (error) {
    if (!state.readerComments || state.readerComments.itemId !== commentsState.itemId) {
      return;
    }
    commentsState.loading = false;
    commentsState.error = summarizeReaderCommentsError(
      commentsState.source,
      error?.message || error || "comments_load_failed",
      commentsState.comments.length > 0
    );
    renderReaderCommentsSection();
  }
}

function renderReaderCommentsSection() {
  const commentsState = state.readerComments;
  const dom = commentsState?.dom;
  if (!commentsState || !dom) {
    return;
  }

  dom.topButton.disabled = false;
  dom.firstButton.disabled = commentsState.loading || commentsState.cursorStack.length <= 1;
  dom.prevButton.disabled = commentsState.loading || commentsState.cursorStack.length <= 1;
  dom.nextButton.disabled = !canRequestNextReaderComments(commentsState);

  if (commentsState.loading) {
    dom.status.textContent = `评论第 ${commentsState.pageNumber} 页加载中...`;
  } else if (commentsState.error) {
    dom.status.textContent =
      commentsState.comments.length > 0 ? `评论更新失败：${commentsState.error}` : `评论加载失败：${commentsState.error}`;
  } else if (commentsState.message) {
    dom.status.textContent = commentsState.message;
  } else {
    dom.status.textContent = `评论第 ${commentsState.pageNumber} 页`;
  }

  dom.list.innerHTML = "";
  if (commentsState.comments.length === 0) {
    const empty = document.createElement("p");
    empty.className = "reader-note";
    empty.textContent = commentsState.loading ? "正在加载评论..." : "暂无评论";
    dom.list.appendChild(empty);
    return;
  }

  for (const comment of commentsState.comments) {
    dom.list.appendChild(createCommentItem(comment));
  }
}

function createCommentItem(comment) {
  const item = document.createElement("article");
  item.className = "comment-item";

  const header = document.createElement("div");
  header.className = "comment-header";

  const author = document.createElement("span");
  author.className = "comment-author";
  author.textContent = comment.authorName || "匿名";

  const likes = document.createElement("span");
  likes.className = "comment-like";
  likes.textContent =
    Number.isFinite(Number(comment.likeCount)) && Number(comment.likeCount) > 0
      ? `赞 ${Number(comment.likeCount)}`
      : "";

  const body = document.createElement("p");
  body.className = "comment-body";
  body.textContent = comment.content || "";

  header.appendChild(author);
  if (likes.textContent) {
    header.appendChild(likes);
  }
  item.appendChild(header);
  item.appendChild(body);
  return item;
}

function scrollReaderToTop() {
  refs.readerView?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function normalizeReaderImages(images) {
  if (!Array.isArray(images)) {
    return [];
  }
  const urls = [];
  for (const item of images) {
    const text = String(item || "").trim();
    if (!text || urls.includes(text)) {
      continue;
    }
    urls.push(text);
  }
  return urls;
}

function normalizeReaderContentBlocks(blocks) {
  if (!Array.isArray(blocks)) {
    return [];
  }
  const normalized = [];
  for (const entry of blocks) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    if (entry.type === "text") {
      const text = String(entry.text || "").trim();
      if (text) {
        normalized.push({ type: "text", text });
      }
      continue;
    }
    if (entry.type === "image") {
      const url = String(entry.url || "").trim();
      if (url) {
        normalized.push({ type: "image", url });
      }
    }
  }
  return normalized;
}

function parseSourceItemId(item) {
  const parts = String(item?.id || "").split(":");
  return parts.length >= 3 ? parts.slice(2).join(":") : "";
}

function appendChipSection(title, values) {
  if (!Array.isArray(values) || values.length === 0) {
    return;
  }
  const section = createReaderSection(title);
  const row = document.createElement("div");
  row.className = "label-row";
  for (const value of values) {
    const chip = document.createElement("span");
    chip.className = "pill";
    chip.textContent = value;
    row.appendChild(chip);
  }
  section.appendChild(row);
  refs.readerBody.appendChild(section);
}

function appendInfoSection(title, text) {
  if (!text) {
    return;
  }
  const section = createReaderSection(title);
  const copy = document.createElement("p");
  copy.className = "reader-note";
  copy.textContent = text;
  section.appendChild(copy);
  refs.readerBody.appendChild(section);
}

function createReaderSection(title) {
  const section = document.createElement("section");
  section.className = "reader-section";

  const heading = document.createElement("h2");
  heading.className = "reader-section-title";
  heading.textContent = title;

  section.appendChild(heading);
  return section;
}

function schedulePushPollIfNeeded() {
  clearPoll();
  if (normalizedItems("push").length > 0 || !bridge || currentListPage() !== "push") {
    return;
  }
  state.pollTimer = window.setTimeout(() => {
    void loadPush({ ensureCurrent: false });
  }, 5000);
}

function ensureSearchReviewPolling() {
  clearSearchReviewPoll();
  if (!bridge || currentListPage() !== "search") {
    return;
  }
  const payload = activePayload("search") || {};
  const review = payload.review || {};
  const status = String(review.status || "pending").trim();
  const keyword = normalizeSearchQuery(state.searchQuery || payload?.meta?.query || "");
  if (!keyword || status === "completed" || status === "disabled" || status === "failed") {
    return;
  }
  state.searchReviewPollTimer = window.setTimeout(async () => {
    try {
      const result = await bridge.searchContent({ query: keyword });
      if (result?.ok) {
        applyPayload(result.payload);
        render();
      }
    } catch {
      return;
    }
  }, 2500);
}

function clearPoll() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function clearSearchReviewPoll() {
  if (state.searchReviewPollTimer) {
    window.clearTimeout(state.searchReviewPollTimer);
    state.searchReviewPollTimer = null;
  }
}

function setStatus(primary, secondary) {
  refs.statusLine.textContent = primary || "";
  refs.metaLine.textContent = secondary || "";
}

function syncStatusForPage(page = currentListPage()) {
  if (page === "settings") {
    const primary = state.settingsLlm.saving
      ? "正在保存 LLM 配置..."
      : state.settingsAuth.loading || state.settingsLlm.loading
        ? "正在读取设置..."
        : "账号与设置";
    setStatus(primary, metaText("settings"));
    return;
  }
  if (page === "search" && !normalizeSearchQuery(state.searchQuery || activePayload("search")?.meta?.query || "")) {
    setStatus("输入关键词开始搜索。", "");
    return;
  }
  const meta = activePayload(page)?.meta || {};
  if (meta.statusText || meta.lastUpdated) {
    setStatus(meta.statusText || "", metaText(page));
    return;
  }
  setStatus(page === "hot" ? "热门内容准备中..." : "推送准备中...", metaText(page));
}

function metaText(page = currentListPage()) {
  if (page === "settings") {
    const updatedAt = state.settingsAuth.updatedAt ? formatDateTime(state.settingsAuth.updatedAt) : "";
    const sources = state.settingsAuth.sources || {};
    const readyCount = Object.values(sources).filter((entry) => entry?.authenticated).length;
    const totalCount = SETTINGS_SOURCE_ORDER.length;
    const parts = [];
    if (updatedAt) {
      parts.push(`更新 ${updatedAt}`);
    }
    parts.push(`已登录 ${readyCount}/${totalCount}`);
    parts.push(state.settingsLlm.apiKeyPresent ? "Key 已就绪" : "Key 未设置");
    return parts.join(" / ");
  }
  const meta = activePayload(page)?.meta || {};
  if (page === "hot") {
    const sourceCounts = meta.sourceCounts || {};
    const totalCount = Object.values(sourceCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    const parts = [];
    if (meta.lastUpdated) {
      parts.push(`更新 ${meta.lastUpdated}`);
    }
    if (totalCount > 0) {
      parts.push(`热门 ${totalCount} 条`);
    }
    const labels = [];
    if (Number(sourceCounts.bilibili || 0) > 0) {
      labels.push(`B站 ${Number(sourceCounts.bilibili)}`);
    }
    if (Number(sourceCounts.zhihu || 0) > 0) {
      labels.push(`知乎 ${Number(sourceCounts.zhihu)}`);
    }
    if (Number(sourceCounts.xiaohongshu || 0) > 0) {
      labels.push(`小红书 ${Number(sourceCounts.xiaohongshu)}`);
    }
    if (labels.length > 0) {
      parts.push(labels.join(" / "));
    }
    return parts.join(" / ");
  }
  if (page === "search") {
    const sourceCounts = meta.sourceCounts || {};
    const parts = [];
    if (meta.lastUpdated) {
      parts.push(`更新 ${meta.lastUpdated}`);
    }
    if (meta.query) {
      parts.push(`搜索 ${meta.query}`);
    }
    if (Number(meta.itemCount || 0) > 0) {
      parts.push(`${Number(meta.itemCount)} 条`);
    }
    const labels = [];
    if (Number(sourceCounts.bilibili || 0) > 0) {
      labels.push(`B站 ${Number(sourceCounts.bilibili)}`);
    }
    if (Number(sourceCounts.zhihu || 0) > 0) {
      labels.push(`知乎 ${Number(sourceCounts.zhihu)}`);
    }
    if (Number(sourceCounts.xiaohongshu || 0) > 0) {
      labels.push(`小红书 ${Number(sourceCounts.xiaohongshu)}`);
    }
    if (labels.length > 0) {
      parts.push(labels.join(" / "));
    }
    return parts.join(" / ");
  }
  const parts = [];
  if (meta.lastUpdated) {
    parts.push(`更新 ${meta.lastUpdated}`);
  }
  parts.push(`缓存 ${Number(meta.readyCount || 0)} 条`);
  parts.push(meta.workerRunning ? "后台运行中" : "后台未运行");
  return parts.join(" / ");
}

function showToast(text) {
  refs.toast.textContent = text;
  refs.toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    refs.toast.classList.remove("is-visible");
  }, 1800);
}

function normalizeSearchQuery(value) {
  return String(value || "").trim();
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "--";
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remain = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}

function formatDateTime(value) {
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function entityTypeLabel(entityType) {
  return {
    video: "视频",
    answer: "回答",
    article: "文章",
    question: "问题",
    note: "笔记",
  }[entityType] || entityType || "";
}

function buildBilibiliPlayerUrl(item, reader) {
  const media = reader?.media || {};
  const bvid = extractBvid(item, reader);
  const aid = Number(media.aid || 0);
  const cid = Number(media.cid || 0);
  const pageNumber = Number(media.pageNumber || 1) || 1;

  if (bvid && cid > 0) {
    const params = new URLSearchParams({
      bvid,
      cid: String(cid),
      p: String(pageNumber),
      page: String(pageNumber),
      high_quality: "1",
      qn: "80",
      as_wide: "1",
      danmaku: "0",
    });
    if (aid > 0) {
      params.set("aid", String(aid));
    }
    return `https://player.bilibili.com/player.html?${params.toString()}`;
  }

  return item?.canonicalUrl || reader?.canonicalUrl || "";
}

function extractBvid(item, reader) {
  const candidates = [item?.id, reader?.canonicalUrl, item?.canonicalUrl];
  for (const candidate of candidates) {
    const match = String(candidate || "").match(/BV[0-9A-Za-z]+/);
    if (match) {
      return match[0];
    }
  }
  return "";
}

function wireBilibiliWebview(webview) {
  let pollTimer = null;
  let attempts = 0;
  let webscreenAttempts = 0;

  const stop = () => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const tick = async () => {
    if (!webview || typeof webview.executeJavaScript !== "function") {
      stop();
      return;
    }
    try {
      const snapshot = await webview.executeJavaScript(buildBilibiliSnapshotScript());
      if (snapshot?.isWebscreen) {
        await webview.executeJavaScript(buildBilibiliCleanupScript());
        stop();
        return;
      }
      if (snapshot?.isNormalPlayer) {
        if (webscreenAttempts < 4) {
          webscreenAttempts += 1;
          await webview.executeJavaScript(buildBilibiliEnterWebscreenScript());
          return;
        }
        await webview.executeJavaScript(buildBilibiliCleanupScript());
        stop();
        return;
      }
    } catch {}

    attempts += 1;
    if (attempts >= 30) {
      stop();
    }
  };

  const start = () => {
    stop();
    attempts = 0;
    webscreenAttempts = 0;
    void tick();
    pollTimer = window.setInterval(() => {
      void tick();
    }, 700);
  };

  webview.addEventListener("dom-ready", start);
  webview.addEventListener("did-finish-load", start);
  webview.addEventListener("did-navigate-in-page", start);
}

function buildBilibiliSnapshotScript() {
  return `
    (() => {
      const isVisible = (element) => {
        if (!element) {
          return false;
        }
        const style = window.getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) {
          return false;
        }
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };

      const playerRoot =
        document.querySelector('#bilibili-player') ||
        document.querySelector('.bpx-player-container') ||
        document.querySelector('.bpx-player-primary-area');
      const media =
        playerRoot?.querySelector('video') ||
        playerRoot?.querySelector('canvas') ||
        document.querySelector('video') ||
        document.querySelector('canvas');
      const controls =
        playerRoot?.querySelector('.bpx-player-control-wrap, .bpx-player-shadow-progress-area, .bpx-player-progress-area, .bpx-player-control-bottom');
      const playerRect = playerRoot?.getBoundingClientRect() || null;
      const mediaRect = media?.getBoundingClientRect() || null;
      const isWebscreen = Boolean(
        document.body.classList.contains('webscreen-fix') ||
          document.documentElement.classList.contains('webscreen-fix')
      );
      const isNormalPlayer = Boolean(
        playerRoot &&
          media &&
          controls &&
          isVisible(media) &&
          isVisible(controls) &&
          playerRect &&
          mediaRect &&
          mediaRect.width >= playerRect.width * 0.78 &&
          mediaRect.height >= playerRect.height * 0.45
      );

      let overlayClosePoint = null;
      if (!isNormalPlayer) {
        const selectors = [
          '[aria-label*="关闭"]',
          '[title*="关闭"]',
          '[class*="close"]',
          '[class*="dismiss"]',
          '[data-action*="close"]',
          '[class*="skip"]',
          '[class*="mask-close"]',
        ];

        for (const selector of selectors) {
          for (const element of document.querySelectorAll(selector)) {
            if (!isVisible(element)) {
              continue;
            }
            const rect = element.getBoundingClientRect();
            const nearTopRight = rect.right > window.innerWidth - 180 && rect.top < 180;
            const smallButtonLike = rect.width <= 128 && rect.height <= 128;
            if (!nearTopRight || !smallButtonLike) {
              continue;
            }
            overlayClosePoint = {
              x: Math.round(rect.left + rect.width / 2),
              y: Math.round(rect.top + rect.height / 2),
            };
            break;
          }
          if (overlayClosePoint) {
            break;
          }
        }

        if (!overlayClosePoint) {
          const fallback = document.elementFromPoint(window.innerWidth - 24, 24);
          if (isVisible(fallback)) {
            const rect = fallback.getBoundingClientRect();
            const nearTopRight = rect.right > window.innerWidth - 180 && rect.top < 180;
            const smallButtonLike = rect.width <= 128 && rect.height <= 128;
            if (nearTopRight && smallButtonLike) {
              overlayClosePoint = {
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
              };
            }
          }
        }
      }

      return {
        isWebscreen,
        isNormalPlayer,
        overlayClosePoint,
      };
    })();
  `;
}

function buildBilibiliEnterWebscreenScript() {
  return `
    (() => {
      const playerRoot =
        document.querySelector('#bilibili-player') ||
        document.querySelector('.bpx-player-container') ||
        document.querySelector('.bpx-player-primary-area');
      const webscreenButton = playerRoot?.querySelector('.bpx-player-ctrl-web');
      if (!playerRoot || !webscreenButton) {
        return false;
      }

      playerRoot.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, view: window }));
      webscreenButton.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, cancelable: true, view: window }));
      webscreenButton.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
      webscreenButton.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
      webscreenButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
      return true;
    })();
  `;
}

function buildBilibiliCleanupScript() {
  return `
    (() => {
      const styleId = 'stream-curator-bili-cleanup-style';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = \`
          #bili-header-container,
          .bili-header,
          header,
          .right-container,
          .left-container-under-player,
          .video-toolbar-v1,
          .video-toolbar-container,
          .video-toolbar-left,
          .video-toolbar-right,
          .video-tag-container,
          .video-tag-container-v1,
          .video-info-container,
          .video-desc-container,
          .video-sections,
          .up-panel-container,
          #commentapp,
          #comment-module,
          #reco_list,
          .recommend-list-v1,
          .fixed-nav,
          .ad-report,
          .video-page-special-card-small,
          .video-page-game-card-small,
          .video-page-creative-card-small,
          .bpx-player-ending-related {
            display: none !important;
          }

          #app,
          main,
          .video-container-v1 {
            display: block !important;
            max-width: none !important;
            margin: 0 !important;
            padding: 0 !important;
          }

          .left-container,
          .left-area,
          .left-layout {
            display: block !important;
            margin: 0 auto !important;
            padding: 0 !important;
          }

          body {
            overflow-y: auto !important;
          }
        \`;
        document.documentElement.appendChild(style);
      }

      const playerRoot =
        document.querySelector('#bilibili-player') ||
        document.querySelector('.bpx-player-container') ||
        document.querySelector('.bpx-player-primary-area');
      if (playerRoot) {
        const top = Math.max(0, playerRoot.getBoundingClientRect().top + window.scrollY - 2);
        window.scrollTo(0, top);
      }
    })();
  `;
}

function buildBilibiliTrimScript() {
  return `
    (() => {
      const css = ${JSON.stringify(BILIBILI_VIDEO_ONLY_CSS)};
      const hiddenSelectors = ${JSON.stringify(BILIBILI_TRIM_HIDDEN_SELECTORS)};
      const wideSelectors = ${JSON.stringify(BILIBILI_TRIM_WIDE_SELECTORS)};
      const focusSelectors = [
        '.bpx-player-video-wrap video',
        '.bpx-player-video-area video',
        '#bilibili-player video',
        '.bpx-player-video-wrap',
        '.bpx-player-video-area',
        '.bpx-player-primary-area',
        '.bpx-player-container',
        '#bilibili-player'
      ];

      function ensureStyle() {
        let style = document.getElementById('stream-curator-bili-video-only-style');
        if (!style) {
          style = document.createElement('style');
          style.id = 'stream-curator-bili-video-only-style';
          style.textContent = css;
          document.documentElement.appendChild(style);
        }
      }

      function pickFocusTarget() {
        for (const selector of focusSelectors) {
          const match = document.querySelector(selector);
          if (match) {
            return match;
          }
        }
        return null;
      }

      function hideSiblings(parent, keepNode) {
        if (!parent || !keepNode) {
          return;
        }
        Array.from(parent.children).forEach((child) => {
          if (child === keepNode || child.contains(keepNode)) {
            return;
          }
          child.style.setProperty('display', 'none', 'important');
        });
      }

      function isVisible(element) {
        if (!element) {
          return false;
        }
        const style = window.getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) {
          return false;
        }
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }

      function hasNormalPlayerState(playerRoot) {
        if (!playerRoot) {
          return false;
        }
        const controls = playerRoot.querySelector(
          '.bpx-player-control-wrap, .bpx-player-shadow-progress-area, .bpx-player-progress-area, .bpx-player-control-bottom'
        );
        const media =
          playerRoot.querySelector('video') ||
          playerRoot.querySelector('canvas');
        if (!controls || !media || !isVisible(controls) || !isVisible(media)) {
          return false;
        }
        const rootRect = playerRoot.getBoundingClientRect();
        const mediaRect = media.getBoundingClientRect();
        return (
          rootRect.width > 0 &&
          mediaRect.width >= rootRect.width * 0.78 &&
          mediaRect.height >= rootRect.height * 0.45
        );
      }

      function tryDismissOverlay(playerRoot, playerShell) {
        if (!playerRoot || !playerShell) {
          return false;
        }

        const rootRect = playerRoot.getBoundingClientRect();
        const shellRect = playerShell.getBoundingClientRect();
        const shellTooSmall =
          shellRect.width > 0 &&
          rootRect.width > 0 &&
          (shellRect.width < rootRect.width * 0.82 || shellRect.height < rootRect.height * 0.72);

        if (!shellTooSmall) {
          return false;
        }

        const selectors = [
          '[aria-label*="关闭"]',
          '[title*="关闭"]',
          '[class*="close"]',
          '[class*="dismiss"]',
          '[data-action*="close"]',
        ];

        for (const selector of selectors) {
          for (const element of playerRoot.querySelectorAll(selector)) {
            if (!isVisible(element)) {
              continue;
            }
            const rect = element.getBoundingClientRect();
            const nearTopRight = rect.right > rootRect.right - 140 && rect.top < rootRect.top + 140;
            if (!nearTopRight) {
              continue;
            }

            const target = element.closest('button, [role="button"], a, div, span');
            if (!target || !isVisible(target)) {
              continue;
            }

            target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            return true;
          }
        }

        return false;
      }

      function tryDismissOverlayV2(playerRoot, playerShell) {
        if (!playerRoot || !playerShell) {
          return false;
        }

        const rootRect = playerRoot.getBoundingClientRect();
        const mediaNode =
          playerRoot.querySelector('video') ||
          playerRoot.querySelector('canvas') ||
          playerShell.querySelector('video') ||
          playerShell.querySelector('canvas');
        const mediaRect = mediaNode ? mediaNode.getBoundingClientRect() : null;
        const mediaTooSmall =
          mediaRect &&
          mediaRect.width > 0 &&
          rootRect.width > 0 &&
          (mediaRect.width < rootRect.width * 0.82 || mediaRect.height < rootRect.height * 0.72);

        if (!mediaTooSmall) {
          return false;
        }

        const clickTarget = (target) => {
          if (!target || !isVisible(target)) {
            return false;
          }
          ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach((type) => {
            target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
          });
          return true;
        };

        const selectors = [
          '[aria-label*="关闭"]',
          '[title*="关闭"]',
          '[class*="close"]',
          '[class*="dismiss"]',
          '[data-action*="close"]',
          '[class*="skip"]',
          '[class*="mask-close"]',
        ];

        for (const selector of selectors) {
          for (const element of playerRoot.querySelectorAll(selector)) {
            if (!isVisible(element)) {
              continue;
            }
            const rect = element.getBoundingClientRect();
            const nearTopRight = rect.right > rootRect.right - 140 && rect.top < rootRect.top + 140;
            if (!nearTopRight) {
              continue;
            }
            const target = element.closest('button, [role="button"], a, div, span');
            if (clickTarget(target)) {
              return true;
            }
          }
        }

        const probePoints = [
          [Math.max(8, window.innerWidth - 24), 24],
          [Math.max(8, window.innerWidth - 40), 40],
          [Math.max(8, rootRect.right - 24), Math.max(8, rootRect.top + 24)],
          [Math.max(8, rootRect.right - 40), Math.max(8, rootRect.top + 40)],
        ];

        for (const [x, y] of probePoints) {
          const element = document.elementFromPoint(x, y);
          if (!element || !isVisible(element)) {
            continue;
          }
          const rect = element.getBoundingClientRect();
          const nearTopRight = rect.right > window.innerWidth - 180 && rect.top < 180;
          const smallButtonLike = rect.width <= 128 && rect.height <= 128;
          if (nearTopRight && smallButtonLike && clickTarget(element.closest('button, [role="button"], a, div, span') || element)) {
            return true;
          }
        }

        return false;
      }

      function apply() {
        const focusTarget = pickFocusTarget();
        if (!focusTarget) {
          return false;
        }

        const playerShell =
          focusTarget.closest('.bpx-player-video-wrap, .bpx-player-video-area, .bpx-player-primary-area, .bpx-player-container, #bilibili-player') ||
          focusTarget;
        const playerRoot =
          focusTarget.closest('#bilibili-player, .bpx-player-container, .bpx-player-primary-area') ||
          playerShell;
        const playerSection =
          playerShell.closest('.player-container, .left-container > div, .video-container-v1 > div') ||
          playerShell.parentElement ||
          playerShell;
        const leftContainer =
          playerShell.closest('.left-container, .left-area, .left-layout, .video-container-v1') ||
          playerSection.parentElement;
        const splitContainer = leftContainer?.parentElement || null;

        if (!hasNormalPlayerState(playerRoot)) {
          return false;
        }

        ensureStyle();

        hiddenSelectors.forEach((selector) => {
          document.querySelectorAll(selector).forEach((element) => {
            element.style.setProperty('display', 'none', 'important');
          });
        });

        wideSelectors.forEach((selector) => {
          document.querySelectorAll(selector).forEach((element) => {
            element.style.setProperty('width', '100%', 'important');
            element.style.setProperty('max-width', 'none', 'important');
            element.style.setProperty('margin', '0', 'important');
            element.style.setProperty('padding', '0', 'important');
          });
        });

        playerRoot.style.setProperty('position', 'relative', 'important');
        playerRoot.style.setProperty('inset', 'auto', 'important');
        playerRoot.style.setProperty('z-index', 'auto', 'important');
        playerRoot.style.setProperty('width', '100%', 'important');
        playerRoot.style.setProperty('height', 'auto', 'important');
        playerRoot.style.setProperty('max-width', 'none', 'important');
        playerRoot.style.setProperty('max-height', 'none', 'important');
        playerRoot.style.setProperty('margin', '0', 'important');
        playerRoot.style.setProperty('padding', '0', 'important');
        playerRoot.style.setProperty('overflow', 'visible', 'important');

        playerShell.style.setProperty('width', '100%', 'important');
        playerShell.style.setProperty('height', 'auto', 'important');
        playerShell.style.setProperty('max-width', 'none', 'important');
        playerShell.style.setProperty('max-height', 'none', 'important');
        playerShell.style.setProperty('margin', '0', 'important');
        playerShell.style.setProperty('padding', '0', 'important');
        playerShell.style.setProperty('overflow', 'visible', 'important');

        if (playerSection) {
          playerSection.style.setProperty('width', '100%', 'important');
          playerSection.style.setProperty('height', 'auto', 'important');
          playerSection.style.setProperty('max-width', 'none', 'important');
          playerSection.style.setProperty('flex', '1 1 100%', 'important');
          playerSection.style.setProperty('margin', '0', 'important');
          playerSection.style.setProperty('padding', '0', 'important');
        }

        if (leftContainer) {
          leftContainer.style.setProperty('width', '100%', 'important');
          leftContainer.style.setProperty('height', 'auto', 'important');
          leftContainer.style.setProperty('max-width', 'none', 'important');
          leftContainer.style.setProperty('flex', '1 1 100%', 'important');
          leftContainer.style.setProperty('display', 'block', 'important');
          leftContainer.style.setProperty('margin', '0', 'important');
          leftContainer.style.setProperty('padding', '0', 'important');
        }

        if (splitContainer) {
          splitContainer.style.setProperty('display', 'block', 'important');
          splitContainer.style.setProperty('width', '100%', 'important');
          splitContainer.style.setProperty('max-width', 'none', 'important');
          splitContainer.style.setProperty('margin', '0', 'important');
          splitContainer.style.setProperty('padding', '0', 'important');
        }

        if (leftContainer && playerSection) {
          hideSiblings(leftContainer, playerSection);
        }
        if (splitContainer && leftContainer) {
          hideSiblings(splitContainer, leftContainer);
        }

        document.documentElement.style.setProperty('overflow', 'auto', 'important');
        document.documentElement.style.setProperty('background', '#fff', 'important');
        document.body.style.setProperty('overflow', 'auto', 'important');
        document.body.style.setProperty('background', '#fff', 'important');
        const top = Math.max(0, playerRoot.getBoundingClientRect().top + window.scrollY - 2);
        window.scrollTo(0, top);
        return true;
      }

      if (!window.__streamCuratorBiliVideoOnlyInstalled) {
        window.__streamCuratorBiliVideoOnlyInstalled = true;
        const observer = new MutationObserver(() => {
          window.requestAnimationFrame(() => {
            try {
              apply();
            } catch {}
          });
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
        window.addEventListener('load', () => {
          try {
            apply();
          } catch {}
        }, { once: true });
      }

      try {
        apply();
        setTimeout(() => {
          try {
            apply();
          } catch {}
        }, 500);
        setTimeout(() => {
          try {
            apply();
          } catch {}
        }, 900);
        setTimeout(() => {
          try {
            apply();
          } catch {}
        }, 1600);
      } catch {}
    })();
  `;
}

function formatReaderText(text, options = {}) {
  const value = String(text || "").trim();
  if (!value) {
    return "";
  }
  if (options.source !== "zhihu" || value.includes("\n")) {
    return value;
  }
  return value
    .replace(/([。！？])/g, "$1\n\n")
    .replace(/([；])/g, "$1\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
