"use strict";

const get = (id) => document.getElementById(id);
const tracks = get("tracks");
const dialog = get("confirm");
const mode = get("mode");
const tableScroll = document.querySelector(".table-scroll");
const selected = new Set();
const state = {
  inventory: null,
  inventoryFingerprint: "",
  rows: [],
  pathDirty: false,
  loaded: false,
  updating: false,
  editing: false,
  launching: false,
  stopped: false,
  job: null,
  draft: null,
};
const labels = {
  pending: "待转码",
  original_ready: "待补 MP3",
  repair: "待补齐",
  success: "成功",
  failed: "失败",
  incomplete: "下载未完成",
};
const workStatuses = new Set(["pending", "original_ready", "repair", "failed"]);
const qualityOrder = ["无损", "有损", "未知"];
const presetLabels = {
  tunefree: "TuneFree · 320 kbps + 原音",
  qm: "QM Unlock · V2 + 原音",
  qmc: "QMC Decoder · 仅原音",
};
const pinyin = new Intl.Collator("zh-u-co-pinyin", { numeric: true, sensitivity: "base" });
const latin = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
const hanInitials = [
  ["阿", "A"], ["八", "B"], ["嚓", "C"], ["咑", "D"], ["妸", "E"], ["发", "F"],
  ["旮", "G"], ["哈", "H"], ["击", "J"], ["咔", "K"], ["垃", "L"], ["妈", "M"],
  ["拿", "N"], ["噢", "O"], ["啪", "P"], ["七", "Q"], ["然", "R"], ["仨", "S"],
  ["他", "T"], ["洼", "W"], ["夕", "X"], ["丫", "Y"], ["匝", "Z"],
];

function fold(value) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

function sortInitial(value) {
  const first = Array.from(value.trim()).find((char) => /[\p{L}\p{N}]/u.test(char));
  if (!first) return "{";
  const letter = fold(first).toUpperCase();
  if (/^[A-Z]$/.test(letter)) return letter;
  if (!/\p{Script=Han}/u.test(first)) return "{";
  let initial = "A";
  for (const [anchor, candidate] of hanInitials) {
    if (pinyin.compare(first, anchor) < 0) break;
    initial = candidate;
  }
  return initial;
}

function orderByName(a, b) {
  return latin.compare(sortInitial(a), sortInitial(b)) || pinyin.compare(a, b);
}

function diskName(path) {
  return String(path ?? "").split(/[\\/]/).at(-1) || "";
}

function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeRow(row) {
  const name = clean(row.name) || diskName(row.source);
  const fileExtension = diskName(row.source).match(/\.([^.]+)$/u)?.[1];
  const format = clean(row.source_format).toUpperCase() || fileExtension?.toUpperCase() || "未知";
  const suffix = fileExtension ? "." + fileExtension.toLowerCase() : "";
  const stem = suffix && name.toLowerCase().endsWith(suffix) ? name.slice(0, -suffix.length) : name;
  const separator = stem.includes(" - ") ? " - " : "-";
  const cut = stem.indexOf(separator);
  const nameArtist = cut > 0 ? stem.slice(0, cut).trim() : "";
  const nameTitle = cut > 0 ? stem.slice(cut + separator.length).trim() : stem;
  const supplied = Array.isArray(row.artists) ? row.artists.map(clean).filter(Boolean) : [];
  const fallback = nameArtist.split(/[,，、;；]/u).map(clean).filter(Boolean);
  const quality = row.status === "incomplete" ? "未知" : clean(row.quality) ||
    ({ FLAC: "无损", MFLAC: "无损", OGG: "有损", MGG: "有损", MP3: "有损" }[format] || "未知");
  return {
    ...row,
    name,
    format,
    quality,
    artists: supplied.length ? supplied : fallback,
    title: clean(row.title) || nameTitle || name,
    copies: Array.isArray(row.copies) ? row.copies : [],
  };
}

function saveSession(name, value) {
  try {
    if (value) sessionStorage.setItem(name, value);
    else sessionStorage.removeItem(name);
  } catch (_) {}
}

function loadSession(name) {
  try { return sessionStorage.getItem(name); }
  catch (_) { return null; }
}

const token = new URLSearchParams(location.hash.slice(1)).get("token") || loadSession("qqmusic.local.token");
if (token && location.hash) {
  saveSession("qqmusic.local.token", token);
  history.replaceState(null, "", location.pathname + location.search);
}

async function request(path, body) {
  if (!token) throw new Error("请从 QQ 音乐转码启动程序重新打开此页面。");
  const headers = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    credentials: "omit",
    mode: "same-origin",
    referrerPolicy: "no-referrer",
  });
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error("本地服务返回了无法读取的响应。"); }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error("本地连接已失效。请从启动程序重新打开。");
    }
    throw new Error(clean(result.message) || clean(result.error) || "本地操作未完成。");
  }
  return result;
}

function showNotice(message, error = false) {
  const notice = get("notice");
  notice.textContent = message;
  notice.hidden = !message;
  notice.classList.toggle("error", error);
}

function connection(connected) {
  const badge = get("connection");
  badge.textContent = connected ? "本机已连接" : "连接中断";
  badge.classList.toggle("connected", connected);
  badge.classList.toggle("disconnected", !connected);
}

function busy() {
  return Boolean(state.editing || state.launching || state.job || state.stopped);
}

function configured() {
  return state.inventory?.paths?.configured === true;
}

function actionable(row) {
  return workStatuses.has(row.status) && (mode.value !== "qmc" ||
    (row.status !== "original_ready" && row.has_original !== true));
}

function needsKey(row) {
  return row.needs_login === true;
}

function updateChoices(id, choices) {
  const control = get(id);
  const previous = control.value;
  const names = [...new Set(choices)].filter(Boolean);
  const defaultOption = control.options[0].cloneNode(true);
  control.replaceChildren(defaultOption, ...names.map((name) => new Option(name, name)));
  control.value = names.includes(previous) ? previous : "";
}

function useInventory(inventory) {
  const fingerprint = JSON.stringify(inventory);
  if (state.inventoryFingerprint === fingerprint) return;
  state.inventoryFingerprint = fingerprint;
  state.inventory = inventory;
  state.rows = (Array.isArray(inventory.rows) ? inventory.rows : []).map(normalizeRow);
  const paths = inventory.paths || {};
  const source = clean(paths.source);
  const exported = clean(paths.export) || clean(inventory.export_dir);
  if (!state.pathDirty) {
    get("source").value = source || clean(paths.suggested_source);
    get("export").value = exported || clean(paths.suggested_export);
  }
  get("paths-caption").textContent = configured()
    ? `${source}  →  ${exported}`
    : "选择源目录与导出目录";
  get("paths-caption").title = get("paths-caption").textContent;
  if (!state.loaded) {
    get("paths-panel").open = !configured();
    state.loaded = true;
  }
  const summary = inventory.summary || {};
  const counts = {
    pending: [...workStatuses].reduce((total, key) => total + (Number(summary[key]) || 0), 0),
    success: Number(summary.success) || 0,
    failed: Number(summary.failed) || 0,
    incomplete: Number(summary.incomplete) || 0,
    historical: Number(summary.historical) || 0,
  };
  for (const [name, count] of Object.entries(counts)) get(`count-${name}`).textContent = count;
  const names = state.rows.flatMap((row) => row.artists);
  const qualities = state.rows.map((row) => row.quality);
  updateChoices("quality", [...qualityOrder.filter((value) => qualities.includes(value)), ...qualities]);
  updateChoices("format", state.rows.map((row) => row.format).sort(orderByName));
  updateChoices("artist", names.sort(orderByName));
  pruneSelection();
  renderTracks();
}

function pruneSelection() {
  const eligible = new Set(state.rows.filter(actionable).map((row) => row.id));
  for (const id of selected) {
    if (!eligible.has(id)) selected.delete(id);
  }
}

function visibleRows() {
  const status = get("status").value;
  const query = fold(get("search").value.trim());
  const scope = get("search-scope").value;
  const quality = get("quality").value;
  const format = get("format").value;
  const artist = get("artist").value;
  return state.rows.filter((row) => {
    if (status === "actionable" && !workStatuses.has(row.status)) return false;
    if (status !== "all" && status !== "actionable" && status !== row.status) return false;
    if (quality && quality !== row.quality) return false;
    if (format && format !== row.format) return false;
    if (artist && !row.artists.includes(artist)) return false;
    if (!query) return true;
    const matchTitle = fold(row.title).includes(query);
    const matchArtist = row.artists.some((name) => fold(name).includes(query));
    return (scope !== "artists" && matchTitle) || (scope !== "title" && matchArtist);
  });
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function rowElement(row) {
  const tr = node("tr", "", "track");
  if (selected.has(row.id)) tr.classList.add("selected");
  const selection = node("td", "", "check-col");
  const checkbox = node("input");
  checkbox.type = "checkbox";
  checkbox.checked = selected.has(row.id);
  checkbox.disabled = !configured() || !actionable(row) || busy();
  checkbox.setAttribute("aria-label", `选择 ${row.title}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selected.add(row.id);
    else selected.delete(row.id);
    tr.classList.toggle("selected", checkbox.checked);
    renderSelection();
  });
  selection.append(checkbox);

  const metadata = node("td");
  const title = node("div", row.title, "track-name");
  title.title = row.name;
  metadata.append(title);
  if (row.artists.length) metadata.append(node("div", row.artists.join("、"), "track-meta"));
  if (row.copies.length) {
    const duplicates = node("div", `${row.copies.length} 个同音质副本`, "track-detail");
    duplicates.title = row.copies.map(diskName).join("\n");
    metadata.append(duplicates);
  }

  const quality = node("td");
  const sourceLine = node("div", "", "source-line");
  sourceLine.append(node("strong", row.format), node("span", row.quality));
  quality.append(sourceLine);
  if (clean(row.quality_detail)) quality.append(node("div", row.quality_detail, "track-detail"));

  const status = node("td");
  const className = Object.hasOwn(labels, row.status) ? row.status : "";
  status.append(node("span", labels[className] || "状态未知", `status-label ${className}`));
  if (clean(row.detail)) status.append(node("div", row.detail, "track-detail"));
  if (actionable(row) && needsKey(row)) status.append(node("div", "需歌曲密钥", "track-detail"));
  tr.append(selection, metadata, quality, status);
  return tr;
}

function renderTracks() {
  const rows = visibleRows();
  const direction = get("sort").value === "desc" ? -1 : 1;
  rows.sort((a, b) => direction * (orderByName(a.title, b.title) || orderByName(a.name, b.name)));
  const grouped = get("group").value;
  const scrollTop = tableScroll.scrollTop;
  const fragment = document.createDocumentFragment();
  if (!rows.length) {
    const template = get("empty-row").content.cloneNode(true);
    template.querySelector("td").textContent = !state.loaded
      ? "正在读取曲库"
      : configured() ? "当前筛选下没有歌曲" : "请先配置工作目录";
    fragment.append(template);
  } else if (grouped === "none") {
    for (const row of rows) fragment.append(rowElement(row));
  } else {
    const groups = new Map();
    for (const row of rows) {
      const group = grouped === "artist" ? row.artists[0] || "未知歌手" : row[grouped];
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(row);
    }
    const names = [...groups.keys()];
    if (grouped === "quality") {
      names.sort((a, b) => (qualityOrder.indexOf(a) + 1 || 99) - (qualityOrder.indexOf(b) + 1 || 99));
    } else {
      names.sort(orderByName);
    }
    for (const name of names) {
      const items = groups.get(name);
      const tr = node("tr", "", "group-row");
      const td = node("td", `${name} · ${items.length} 首`);
      td.colSpan = 4;
      tr.append(td);
      fragment.append(tr);
      for (const row of items) fragment.append(rowElement(row));
    }
  }
  tracks.replaceChildren(fragment);
  tableScroll.scrollTop = scrollTop;
  get("library-count").textContent = `显示 ${rows.length} / ${state.rows.length} 首`;
  renderSelection(rows);
}

function renderSelection(rows = visibleRows()) {
  const available = rows.filter(actionable);
  const picked = available.filter((row) => selected.has(row.id)).length;
  const selectAll = get("select-all");
  selectAll.disabled = !configured() || busy() || !available.length;
  selectAll.checked = Boolean(available.length && picked === available.length);
  selectAll.indeterminate = picked > 0 && picked < available.length;
  get("convert").disabled = !configured() || busy() || !selected.size;
  get("convert").textContent = state.job ? "转换中" : "开始转换";
  get("selection-info").textContent = `已选 ${selected.size} 首${selected.size !== picked ? `，其中 ${picked} 首在当前筛选中` : ""}`;
  get("refresh").disabled = state.updating || busy();
  get("stop").disabled = state.updating || busy() || !state.loaded;
  get("apply-paths").disabled = state.updating || busy() || !token;
  for (const item of document.querySelectorAll(".pick, #source, #export, #mode")) {
    item.disabled = busy() || !token;
  }
}

async function refresh({ silent = false } = {}) {
  if (state.updating || busy()) return;
  state.updating = true;
  renderSelection();
  try {
    useInventory(await request("/api/inventory"));
    connection(true);
    if (!silent) showNotice("");
  } catch (error) {
    connection(false);
    if (!silent || !state.loaded) showNotice(error.message, true);
    if (!state.loaded) {
      get("library-count").textContent = "暂未读取曲库";
      const template = get("empty-row").content.cloneNode(true);
      template.querySelector("td").textContent = error.message;
      tracks.replaceChildren(template);
    }
  } finally {
    state.updating = false;
    renderSelection();
  }
}

async function configure(event) {
  event.preventDefault();
  if (busy() || state.updating) return;
  const source = get("source").value.trim();
  const exported = get("export").value.trim();
  if (!source || !exported) return;
  state.editing = true;
  renderSelection();
  try {
    const inventory = await request("/api/configure", { source, export: exported });
    state.pathDirty = false;
    selected.clear();
    useInventory(inventory);
    get("paths-panel").open = !configured();
    connection(true);
    showNotice(configured() ? "工作目录已更新。" : "请检查两个工作目录。", !configured());
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    state.editing = false;
    renderTracks();
  }
}

async function pickDirectory(kind) {
  if (busy() || state.updating) return;
  state.editing = true;
  renderSelection();
  try {
    const result = await request("/api/pick", { kind });
    if (clean(result.path)) {
      get(kind).value = result.path;
      state.pathDirty = true;
    }
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    state.editing = false;
    renderSelection();
  }
}

async function stopServer() {
  if (busy() || state.updating || !state.loaded) return;
  state.editing = true;
  renderSelection();
  try {
    await request("/api/stop", {});
    state.stopped = true;
    saveSession("qqmusic.local.token", "");
    saveSession("qqmusic.local.job", "");
    connection(false);
    get("connection").textContent = "已退出";
    showNotice("本地服务已退出。再次使用时，请重新打开启动程序。");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    state.editing = false;
    renderTracks();
  }
}

function cleanDraft() {
  for (const input of document.querySelectorAll("#key-list input")) input.value = "";
  get("key-list").replaceChildren();
  state.draft = null;
}

function selectedRows() {
  return state.rows.filter((row) => selected.has(row.id) && actionable(row));
}

function draftReadyCount() {
  if (!state.draft) return 0;
  return state.draft.rows.reduce((total, row) =>
    total + (!needsKey(row) || state.draft.inputs.get(row.id)?.value.trim() ? 1 : 0), 0);
}

function renderDraft() {
  const draft = state.draft;
  if (!draft) return;
  const ready = draftReadyCount();
  get("confirm-convert").disabled = ready !== draft.rows.length;
  get("ready-only").hidden = ready === 0 || ready === draft.rows.length;
  get("ready-only").textContent = `仅转换已就绪的 ${ready} 首`;
}

function confirmBatch() {
  if (busy() || !configured()) return;
  const rows = selectedRows();
  if (!rows.length) return;
  cleanDraft();
  const keyList = get("key-list");
  const draft = { rows, mode: mode.value, inputs: new Map() };
  state.draft = draft;
  const requiringKeys = rows.filter(needsKey);
  const suffix = requiringKeys.length
    ? `其中 ${requiringKeys.length} 首需要各自的歌曲专用密钥，其余可直接处理。`
    : "这一批可直接在本机处理。";
  get("confirm-summary").textContent = `将以 ${presetLabels[draft.mode]} 处理 ${rows.length} 首歌曲。${suffix}`;
  get("key-panel").hidden = !requiringKeys.length;
  get("confirm-error").hidden = true;

  for (const row of requiringKeys) {
    const label = node("label", "", "key-field");
    label.append(node("span", row.artists.length ? `${row.title} · ${row.artists.join("、")}` : row.title));
    const field = node("input");
    field.type = "password";
    field.autocomplete = "off";
    field.spellcheck = false;
    field.autocapitalize = "off";
    field.placeholder = "粘贴这首歌的 ekey";
    field.maxLength = 4096;
    field.addEventListener("input", renderDraft);
    label.append(field);
    keyList.append(label);
    draft.inputs.set(row.id, field);
  }
  renderDraft();
  dialog.showModal();
}

function jobPanel(total) {
  get("job-panel").hidden = false;
  get("job-count").textContent = `0 / ${total || "—"}`;
  get("job-progress").max = total || 1;
  get("job-progress").value = 0;
  get("job-message").textContent = "准备转换";
  get("job-events").replaceChildren();
}

function appendProgress(event) {
  const total = Math.max(Number(event.total) || 0, 1);
  const done = Math.min(Math.max(Number(event.done) || 0, 0), total);
  get("job-progress").max = total;
  get("job-progress").value = done;
  if (event.total) get("job-count").textContent = `${done} / ${total}`;
  const message = clean(event.message);
  if (message) get("job-message").textContent = message;
  if (event.event === "progress" && message) {
    const events = get("job-events");
    const item = node("li", message);
    if (event.status === "failed") item.classList.add("failed");
    events.append(item);
    while (events.children.length > 30) events.firstElementChild.remove();
    events.scrollTop = events.scrollHeight;
  }
  if (event.event === "complete") {
    get("job-message").textContent = `已完成 ${Number(event.success) || 0} 首，失败 ${Number(event.failed) || 0} 首，跳过 ${Number(event.skipped) || 0} 首。`;
  } else if (event.event === "error") {
    showNotice(message || "本批转换中止。", true);
  }
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function followJob(job) {
  let cursor = 0;
  let failures = 0;
  while (state.job === job) {
    try {
      const result = await request(`/api/jobs/${encodeURIComponent(job)}?after=${cursor}`);
      failures = 0;
      for (const event of result.events || []) appendProgress(event);
      cursor = Number.isInteger(result.next) ? result.next : cursor;
      if (!result.running) break;
    } catch (error) {
      failures += 1;
      if (failures >= 8) {
        showNotice("暂时无法读取进度。请刷新曲库确认最新结果。", true);
        break;
      }
    }
    await wait(failures ? 1100 : 430);
  }
  if (state.job === job) {
    state.job = null;
    saveSession("qqmusic.local.job", "");
    await refresh({ silent: true });
    renderTracks();
  }
}

async function startBatch(onlyReady = false) {
  const draft = state.draft;
  if (!draft || busy()) return;
  const keys = Object.create(null);
  const ready = [];
  for (const row of draft.rows) {
    const entered = draft.inputs.get(row.id)?.value.trim() || "";
    if (!needsKey(row) || entered) ready.push(row.id);
    if (entered && needsKey(row)) keys[row.id] = entered;
  }
  if (!ready.length || (!onlyReady && ready.length !== draft.rows.length)) {
    get("confirm-error").textContent = "请填写所选歌曲的密钥，或选择仅处理已就绪歌曲。";
    get("confirm-error").hidden = false;
    return;
  }

  const conversionMode = draft.mode;
  state.launching = true;
  cleanDraft();
  dialog.close();
  showNotice("");
  jobPanel(ready.length);
  renderTracks();
  try {
    const result = await request("/api/convert", {
      selected: ready, mode: conversionMode, allow_qq_login: false, keys,
    });
    if (!result.job) throw new Error("本地服务未启动转换批次。");
    state.job = String(result.job);
    saveSession("qqmusic.local.job", state.job);
    get("job-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    void followJob(state.job);
  } catch (error) {
    get("job-message").textContent = "转换未启动。";
    showNotice(error.message, true);
  } finally {
    for (const id of Object.keys(keys)) delete keys[id];
    state.launching = false;
    renderTracks();
  }
}

get("refresh").addEventListener("click", () => void refresh());
get("stop").addEventListener("click", () => void stopServer());
get("paths-form").addEventListener("submit", configure);
for (const id of ["source", "export"]) {
  get(id).addEventListener("input", () => { state.pathDirty = true; });
}
for (const button of document.querySelectorAll(".pick")) {
  button.addEventListener("click", () => void pickDirectory(button.dataset.kind));
}
for (const id of ["group", "sort", "status", "quality", "format", "artist"]) {
  get(id).addEventListener("change", renderTracks);
}
get("search").addEventListener("input", renderTracks);
get("search-scope").addEventListener("change", () => {
  const prompts = { all: "搜索歌名或歌手", title: "搜索歌名", artists: "搜索歌手" };
  get("search").placeholder = prompts[get("search-scope").value];
  renderTracks();
});
get("clear-filters").addEventListener("click", () => {
  for (const id of ["search", "quality", "format", "artist"]) get(id).value = "";
  get("status").value = "actionable";
  get("search-scope").value = "all";
  get("search-scope").dispatchEvent(new Event("change"));
});
mode.addEventListener("change", () => {
  pruneSelection();
  renderTracks();
});
get("select-all").addEventListener("change", (event) => {
  for (const row of visibleRows().filter(actionable)) {
    if (event.target.checked) selected.add(row.id);
    else selected.delete(row.id);
  }
  renderTracks();
});
get("convert").addEventListener("click", confirmBatch);
get("cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", cleanDraft);
get("confirm-convert").addEventListener("click", () => void startBatch());
get("ready-only").addEventListener("click", () => void startBatch(true));

async function boot() {
  renderTracks();
  await refresh();
  const existingJob = token ? loadSession("qqmusic.local.job") : null;
  if (existingJob && state.loaded) {
    state.job = existingJob;
    jobPanel(0);
    renderTracks();
    void followJob(existingJob);
  }
  setInterval(() => {
    if (token && !document.hidden && !dialog.open) void refresh({ silent: true });
  }, 5000);
}

void boot();
