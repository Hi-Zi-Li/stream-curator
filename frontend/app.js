const bridge = globalThis.streamCuratorDesktop ?? null;

const state = {
  payload: null,
  selectedId: null,
  pollTimer: null,
  isLoading: false,
  isRefreshing: false,
};

const refs = {};

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
  refs.statusLine = document.getElementById("status-line");
  refs.metaLine = document.getElementById("meta-line");
  refs.refreshButton = document.getElementById("refresh-button");
  refs.cardGrid = document.getElementById("card-grid");
  refs.detailEmpty = document.getElementById("detail-empty");
  refs.detailContent = document.getElementById("detail-content");
  refs.detailSource = document.getElementById("detail-source");
  refs.detailLevel = document.getElementById("detail-level");
  refs.detailAuthor = document.getElementById("detail-author");
  refs.detailTitle = document.getElementById("detail-title");
  refs.detailLabels = document.getElementById("detail-labels");
  refs.detailSummary = document.getElementById("detail-summary");
  refs.detailReason = document.getElementById("detail-reason");
  refs.detailSections = document.getElementById("detail-sections");
  refs.openButton = document.getElementById("open-button");
  refs.copyButton = document.getElementById("copy-button");
  refs.toast = document.getElementById("toast");
}

function bindEvents() {
  refs.refreshButton.addEventListener("click", () => {
    void refreshPush();
  });

  refs.openButton.addEventListener("click", async () => {
    const item = selectedItem();
    if (!item?.canonicalUrl) {
      return;
    }
    await bridge.openExternal(item.canonicalUrl);
  });

  refs.copyButton.addEventListener("click", async () => {
    const item = selectedItem();
    if (!item?.canonicalUrl) {
      return;
    }
    await bridge.copyText(item.canonicalUrl);
    showToast("链接已复制");
  });
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
    schedulePollIfNeeded();
  }
}

async function refreshPush() {
  if (!bridge || state.isRefreshing) {
    return;
  }
  state.isRefreshing = true;
  setStatus("正在切换下一组推送。", metaText());
  render();
  try {
    const result = await bridge.refreshPush();
    if (!result?.ok) {
      throw new Error(result?.error || "push_refresh_failed");
    }
    applyPayload(result.payload);
  } catch (error) {
    showToast("刷新失败");
    setStatus("刷新失败。", String(error.message || error));
  } finally {
    state.isRefreshing = false;
    render();
    schedulePollIfNeeded();
  }
}

function applyPayload(payload) {
  clearPoll();
  state.payload = payload || { items: [], meta: {} };
  const items = normalizedItems();
  if (items.length > 0) {
    const stillExists = items.some((item) => item.id === state.selectedId);
    state.selectedId = stillExists ? state.selectedId : items[0].id;
  } else {
    state.selectedId = null;
  }
  const meta = state.payload?.meta || {};
  setStatus(meta.statusText || "推送已更新。", metaText());
}

function normalizedItems() {
  const items = state.payload?.items;
  return Array.isArray(items) ? items : [];
}

function selectedItem() {
  return normalizedItems().find((item) => item.id === state.selectedId) || null;
}

function render() {
  renderToolbar();
  renderCards();
  renderDetail();
}

function renderToolbar() {
  refs.refreshButton.disabled = state.isRefreshing || state.isLoading;
  refs.refreshButton.textContent = state.isRefreshing ? "…" : "↻";
}

function renderCards() {
  const items = normalizedItems();
  refs.cardGrid.innerHTML = "";

  if (state.isLoading && items.length === 0) {
    for (let index = 0; index < 6; index += 1) {
      refs.cardGrid.appendChild(createPlaceholderCard("正在准备推送…"));
    }
    return;
  }

  const visibleItems = items.slice(0, 6);
  for (const item of visibleItems) {
    refs.cardGrid.appendChild(createCard(item));
  }
  for (let index = visibleItems.length; index < 6; index += 1) {
    refs.cardGrid.appendChild(
      createPlaceholderCard(index === 0 ? "等待第一组推送…" : "后台继续补充中…")
    );
  }
}

function createCard(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `card ${item.id === state.selectedId ? "is-selected" : ""}`;
  button.addEventListener("click", () => {
    state.selectedId = item.id;
    render();
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

function renderDetail() {
  const item = selectedItem();
  if (!item) {
    refs.detailEmpty.textContent = state.isLoading ? "正在准备第一组推送…" : "等待第一组推送…";
    refs.detailEmpty.classList.remove("is-hidden");
    refs.detailContent.classList.add("is-hidden");
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

function schedulePollIfNeeded() {
  clearPoll();
  if (normalizedItems().length > 0 || !bridge) {
    return;
  }
  state.pollTimer = window.setTimeout(() => {
    void loadPush({ ensureCurrent: false });
  }, 5000);
}

function clearPoll() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function setStatus(primary, secondary) {
  refs.statusLine.textContent = primary || "";
  refs.metaLine.textContent = secondary || "";
}

function metaText() {
  const meta = state.payload?.meta || {};
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
