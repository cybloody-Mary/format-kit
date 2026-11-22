"use strict";

const $ = (id) => document.getElementById(id);
const iconPaths = {
  music: '<path d="M9 18V5l12-2v13M9 9l12-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="18" cy="16" rx="3" ry="2"/>',
  history: '<path d="M3 12a9 9 0 1 1 2.6 6.4M3 12V6m0 6h6m3-5v5l3 2"/>',
  settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="white"/><circle cx="16" cy="12" r="2" fill="white"/><circle cx="10" cy="18" r="2" fill="white"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  refresh: '<path d="M20 8a8 8 0 0 0-14-2L3 9m0 0V4m0 5h5m-4 7a8 8 0 0 0 14 2l3-3m0 0v5m0-5h-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  sort: '<path d="M8 4v16m-3-3 3 3 3-3m5 3V4m-3 3 3-3 3 3"/>',
  play: '<path d="m8 5 12 7-12 7Z"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4 4 10-10"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4v.01"/>',
  new: '<path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="8"/>',
  exit: '<path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5m-1-8h11m-4-4 4 4-4 4"/>',
};
const labels = {
  pending: "待处理", preparing: "准备中", running: "转换中", complete: "已导出",
  partial: "部分成功", failed: "失败", cancelled: "已取消",
};
const collator = new Intl.Collator("zh-CN-u-co-pinyin", { numeric: true, sensitivity: "base" });
const dateFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const selected = new Set();
let state = { settings: {}, songs: [], history: [], scan_error: "" };
let view = "library";
let statusFilter = "all";
let order = 1;
let busy = false;
let outputDirty = false;
let revision = 0;
let lastHistory = "";
let cancellingBatch = "";
let stopped = false;

function make(tag, cls = "", value = "") {
  const element = document.createElement(tag);
  element.className = cls;
  if (value) element.textContent = value;
  return element;
}

function addIcon(element, name) {
  element.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + iconPaths[name] + '</svg>';
}

function iconize(element) {
  for (const slot of element.querySelectorAll("[data-icon]")) addIcon(slot, slot.dataset.icon);
}

function button(label, kind = "secondary", icon = "") {
  const result = make("button", "button " + kind);
  result.type = "button";
  if (icon) {
    const image = make("span");
    addIcon(image, icon);
    result.append(image);
  }
  result.append(document.createTextNode(label));
  return result;
}

async function api(path, body) {
  const options = { headers: { "X-QQMusic-Request": "1" } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/" + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}

function notify(message, error = false) {
  const notice = $("notice");
  notice.hidden = !message;
  notice.textContent = message;
  notice.classList.toggle("error", error);
}

async function execute(action) {
  if (busy || stopped) return;
  busy = true;
  ++revision;
  updateSelection();
  renderBatch();
  try {
    await action();
  } catch (error) {
    notify(error.message, true);
  } finally {
    busy = false;
    updateSelection();
    renderBatch();
  }
}

function empty(content, heading, message, action = undefined) {
  const template = $("empty-template").content.cloneNode(true);
  template.querySelector("h3").textContent = heading;
  template.querySelector("p").textContent = message;
  iconize(template);
  content.replaceChildren(template);
  if (action) content.append(action);
}

function options(id, values, label, transform = (value) => value) {
  const select = $(id);
  const prior = select.value;
  const sorted = [...new Set(values)].sort(collator.compare);
  select.replaceChildren(new Option(label, ""), ...sorted.map((value) => new Option(transform(value), value)));
  select.value = sorted.includes(prior) ? prior : "";
}

function activeBatch() {
  return state.history.find((batch) => batch.status === "preparing" || batch.status === "running");
}

function showBatchResult(batch) {
  const details = batch.error ? ` · ${batch.error}` : "";
  const result = batch.status === "cancelled" ? "批次已取消" : "批次已结束";
  notify(`${result} · 已导出 ${batch.succeeded}/${batch.total} 首${details}`, Boolean(batch.error || batch.failed));
}

function applyState(value) {
  const old = activeBatch();
  const previousRoot = state.settings.input_dir;
  const changed = JSON.stringify(state) !== JSON.stringify(value);
  state = value;
  const current = activeBatch();
  if (previousRoot && previousRoot !== state.settings.input_dir) selected.clear();
  if (old && !current) {
    const finished = state.history.find((batch) => batch.id === old.id);
    if (finished) showBatchResult(finished);
  }
  if (current?.id !== cancellingBatch) cancellingBatch = "";
  const currentSongs = new Set(state.songs.map((song) => song.id));
  for (const id of selected) if (!currentSongs.has(id)) selected.delete(id);
  $("open-output").disabled = !state.settings.output_dir;
  if (!outputDirty) {
    $("format").value = state.settings.format || "copy";
    $("bitrate").value = state.settings.bitrate || "2";
  }
  $("bitrate-wrap").hidden = $("format").value !== "mp3";
  $("history-count").textContent = state.history.length;
  $("history-count").hidden = state.history.length === 0;
  if (!changed) return;
  options("quality", state.songs.map((song) => song.quality), "全部音质");
  options("format-filter", state.songs.map((song) => song.format), "全部格式", (f) => f.toUpperCase());
  options("artist", state.songs.map((song) => song.artist), "全部歌手");
  renderLibrary();
  renderBatch();
  renderHistory();
}

function setView(next) {
  view = next;
  $("library").hidden = view !== "library";
  $("history").hidden = view !== "history";
  $("heading").textContent = view === "history" ? "转换历史" : "歌曲库";
  $("subtitle").textContent = view === "history" ? "查看每批导出的文件和失败原因。" : "整理 QQ 音乐下载，导出到你常用的播放器。";
  for (const nav of document.querySelectorAll("[data-view]")) {
    const active = nav.dataset.view === view;
    nav.classList.toggle("active", active);
    if (active) nav.setAttribute("aria-current", "page");
    else nav.removeAttribute("aria-current");
  }
}

function visibleSongs() {
  const search = $("search").value.trim().toLocaleLowerCase("zh-CN").normalize("NFKC");
  const format = $("format-filter").value;
  const result = state.songs.filter((song) => {
    if (statusFilter === "new" && !song.is_new) return false;
    if (statusFilter !== "new" && statusFilter !== "all" && song.status !== statusFilter) return false;
    if (format && song.format !== format) return false;
    if ($("quality").value && song.quality !== $("quality").value) return false;
    if ($("artist").value && song.artist !== $("artist").value) return false;
    return !search || [song.title, song.artist].some((value) => value.toLocaleLowerCase("zh-CN").normalize("NFKC").includes(search));
  });
  return result.sort((a, b) => order * collator.compare(a.title, b.title) || collator.compare(a.artist, b.artist));
}

function renderStats() {
  const entries = [
    ["all", "全部歌曲", state.songs.length, "music", ""],
    ["new", "新增文件", state.songs.filter((s) => s.is_new).length, "new", "new"],
    ["complete", "已导出", state.songs.filter((s) => s.status === "complete").length, "check", "success"],
    ["failed", "失败", state.songs.filter((s) => s.status === "failed").length, "alert", "failed"],
  ];
  $("stats").replaceChildren(...entries.map(([value, label, count, symbol, cls]) => {
    const tile = make("button", "stat " + cls);
    tile.type = "button";
    tile.dataset.status = value;
    tile.setAttribute("aria-pressed", String(statusFilter === value));
    const chip = make("span", "chip");
    addIcon(chip, symbol);
    const text = make("span");
    text.append(make("strong", "", String(count)), make("small", "", label));
    tile.append(chip, text);
    return tile;
  }));
}

function renderRow(song) {
  const tr = make("tr");
  const checkCell = make("td", "check-cell");
  const check = make("input");
  check.type = "checkbox";
  check.checked = selected.has(song.id);
  check.setAttribute("aria-label", "选择 " + song.title);
  check.addEventListener("change", () => {
    if (check.checked) selected.add(song.id);
    else selected.delete(song.id);
    tr.classList.toggle("picked", check.checked);
    updateSelection();
  });
  checkCell.append(check);
  const name = make("td", "song");
  name.title = song.path;
  name.append(make("strong", "", song.title), make("small", "", song.artist));
  const quality = make("td");
  quality.append(make("span", "pill" + (song.quality === "无损" ? " lossless" : ""), song.quality));
  const status = make("td");
  const style = song.is_new && song.status === "pending" ? "new" : song.status;
  status.append(make("span", "pill " + style, style === "new" ? "新增" : labels[song.status] || labels.pending));
  tr.append(checkCell, name, quality, make("td", "", song.format.toUpperCase()), status);
  tr.classList.toggle("picked", check.checked);
  return tr;
}

function updateSelection() {
  const visible = visibleSongs();
  const checked = visible.filter((song) => selected.has(song.id)).length;
  const all = $("select-all");
  all.checked = visible.length > 0 && checked === visible.length;
  all.indeterminate = checked > 0 && checked < visible.length;
  $("selected-count").textContent = `已选 ${selected.size} 首`;
  const hasBatch = Boolean(activeBatch());
  $("create-batch").disabled = !selected.size || hasBatch || busy;
  $("exit-app").disabled = hasBatch || busy;
  $("exit-app").title = hasBatch ? "请等待转换完成或先取消批次" : "退出应用";
}

function renderLibrary() {
  renderStats();
  const visible = visibleSongs();
  $("visible-count").textContent = `${visible.length} 首`;
  $("table-wrap").hidden = visible.length === 0;
  $("empty").hidden = visible.length > 0;
  const body = $("songs");
  const group = $("group").value;
  const groups = new Map();
  for (const song of visible) {
    const value = group ? song[group] : "";
    const tracks = groups.get(value) || [];
    tracks.push(song);
    groups.set(value, tracks);
  }
  const content = document.createDocumentFragment();
  for (const key of [...groups.keys()].sort(collator.compare)) {
    if (key) {
      const row = make("tr", "group-title");
      const title = group === "format" ? key.toUpperCase() : key;
      const cell = make("td", "", `${title} · ${groups.get(key).length} 首`);
      cell.colSpan = 5;
      row.append(cell);
      content.append(row);
    }
    for (const song of groups.get(key)) content.append(renderRow(song));
  }
  body.replaceChildren(content);
  if (!visible.length) {
    if (!state.settings.input_dir) {
      const setup = button("选择下载目录", "primary", "folder");
      setup.addEventListener("click", openSettings);
      empty($("empty"), "从下载目录导入歌曲", "选择 QQ 音乐的下载目录，应用会自动发现已下载的歌曲。", setup);
    } else if (state.scan_error) {
      empty($("empty"), "读取下载目录失败", state.scan_error);
    } else if (state.songs.length) {
      empty($("empty"), "没有符合条件的歌曲", "换个搜索词，或调整筛选条件。");
    } else {
      empty($("empty"), "下载目录中没有支持的文件", "将下载好的 MFLAC、MGG 或 QMC 文件保存到此目录，应用会自动刷新。");
    }
  }
  updateSelection();
}

function folderCommand(kind, batch_id = "") {
  execute(() => api("folder/open", { kind, batch_id }));
}

function actionButton(label, callback, kind = "secondary", icon = "") {
  const result = button(label, kind, icon);
  result.addEventListener("click", callback);
  return result;
}

function renderBatch() {
  const batch = activeBatch();
  $("active-batch").hidden = !batch;
  if (!batch) return;
  const cancelling = cancellingBatch === batch.id || Boolean(batch.cancelling);
  $("batch-heading").textContent = cancelling ? "正在取消批次" :
    batch.status === "preparing" ? "正在准备批次" : "正在转换";
  const format = batch.format === "copy" ? "保持原格式" : `MP3 V${batch.bitrate}`;
  $("batch-description").textContent = batch.error || (batch.current_title ? `${format} · ${batch.current_title}` : format);
  const processed = batch.succeeded + batch.failed;
  const progress = $("batch-progress");
  progress.max = 100;
  const processedPercent = Math.round(100 * processed / batch.total);
  progress.value = Math.max(processedPercent, batch.percent || 0);
  $("batch-count").textContent = `已处理 ${processed}/${batch.total} 首 · 已导出 ${batch.succeeded} 首${batch.failed ? ` · 失败 ${batch.failed} 首` : ""}`;
  $("cancel-batch").disabled = busy || cancelling;
}

function renderHistory() {
  const container = $("history-items");
  const signature = JSON.stringify(state.history.map((b) => [b.id, b.status, b.succeeded, b.failed, b.finished_at, b.error]));
  if (signature === lastHistory) return;
  lastHistory = signature;
  container.className = state.history.length ? "" : "empty";
  if (!state.history.length) {
    empty(container, "暂无转换记录", "开始转换后，每批结果都会保存在这里。");
    return;
  }
  const open = new Set([...container.querySelectorAll("details[open]")].map((detail) => detail.dataset.id));
  const items = state.history.map((batch) => {
    const panel = make("details", "history-batch");
    panel.dataset.id = batch.id;
    panel.open = open.has(batch.id);
    const summary = make("summary", "", `${dateFormat.format(new Date(batch.created_at))} · ${batch.format === "copy" ? "原格式" : "MP3 V" + batch.bitrate} · ${batch.succeeded}/${batch.total} 首`);
    summary.append(make("span", "pill " + batch.status, labels[batch.status]));
    panel.append(summary);
    const controls = make("div", "batch-actions");
    controls.append(actionButton("打开导出目录", () => folderCommand("output_dir", batch.id), "secondary", "folder"));
    panel.append(controls);
    if (batch.error) panel.append(make("p", "file", batch.error));
    for (const item of batch.items) {
      const row = make("p", "file");
      row.append(make("span", "pill " + item.status, labels[item.status]), make("strong", "", item.title), " · " + item.artist);
      if (item.output || item.error) row.append(make("br"), item.output || item.error);
      panel.append(row);
    }
    return panel;
  });
  container.replaceChildren(...items);
}

function openSettings() {
  if ($("settings-dialog").open) return;
  for (const field of ["input_dir", "output_dir"]) $(field).value = state.settings[field] || "";
  $("settings-error").hidden = true;
  $("settings-dialog").showModal();
}

iconize(document);
$("open-settings").addEventListener("click", openSettings);
$("sidebar-settings").addEventListener("click", openSettings);
$("exit-app").addEventListener("click", () => execute(async () => {
  await api("exit", {});
  stopped = true;
  clearInterval(pollTimer);
  $("app").hidden = true;
  $("stopped").hidden = false;
  $("stopped-heading").focus();
}));
$("close-settings").addEventListener("click", () => $("settings-dialog").close());
$("open-output").addEventListener("click", () => folderCommand("output_dir"));
$("refresh").addEventListener("click", () => execute(async () => {
  const refreshed = await api("scan", {});
  applyState(refreshed);
  notify(refreshed.scan_error || `已刷新 · ${refreshed.songs.length} 首`, Boolean(refreshed.scan_error));
}));
for (const nav of document.querySelectorAll("[data-view]")) nav.addEventListener("click", () => setView(nav.dataset.view));
for (const id of ["quality", "artist", "format-filter", "group"]) $(id).addEventListener("change", renderLibrary);
$("search").addEventListener("input", renderLibrary);
$("stats").addEventListener("click", (event) => {
  const tile = event.target.closest("[data-status]");
  if (!tile) return;
  statusFilter = statusFilter === tile.dataset.status ? "all" : tile.dataset.status;
  renderLibrary();
  $("stats").querySelector(`[data-status="${statusFilter}"]`).focus();
});
$("sort").addEventListener("click", () => {
  order *= -1;
  $("sort-label").textContent = order === 1 ? "A → Z" : "Z → A";
  renderLibrary();
});
$("select-all").addEventListener("change", () => {
  const checked = $("select-all").checked;
  for (const song of visibleSongs()) {
    if (checked) selected.add(song.id);
    else selected.delete(song.id);
  }
  renderLibrary();
});
for (const id of ["format", "bitrate"]) {
  $(id).addEventListener("change", () => {
    outputDirty = true;
    $("bitrate-wrap").hidden = $("format").value !== "mp3";
  });
}
$("create-batch").addEventListener("click", () => {
  if (!state.settings.output_dir) {
    openSettings();
    return;
  }
  execute(async () => {
    const created = await api("batches", {
      song_ids: [...selected], format: $("format").value, bitrate: $("bitrate").value,
    });
    selected.clear();
    notify("");
    applyState(created);
    if (!activeBatch() && created.history[0]) showBatchResult(created.history[0]);
  });
});
$("cancel-batch").addEventListener("click", () => {
  const batch = activeBatch();
  if (!batch || !window.confirm("确认取消本批次？已导出的歌曲会保留。")) return;
  execute(async () => {
    cancellingBatch = batch.id;
    renderBatch();
    try {
      applyState(await api("batches/cancel", { batch_id: batch.id }));
    } catch (error) {
      cancellingBatch = "";
      throw error;
    }
  });
});
for (const pick of document.querySelectorAll("[data-pick]")) {
  pick.addEventListener("click", async () => {
    pick.disabled = true;
    try {
      const field = pick.dataset.pick;
      const result = await api("pick", { field });
      if (result.path) $(field).value = result.path;
    } catch (error) {
      $("settings-error").textContent = error.message;
      $("settings-error").hidden = false;
    } finally {
      pick.disabled = false;
    }
  });
}
$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  execute(async () => {
    $("save-settings").disabled = true;
    try {
      const fields = ["input_dir", "output_dir"];
      applyState(await api("settings", Object.fromEntries(fields.map((key) => [key, $(key).value]))));
      $("settings-dialog").close();
      notify("");
    } catch (error) {
      $("settings-error").textContent = error.message;
      $("settings-error").hidden = false;
    } finally {
      $("save-settings").disabled = false;
    }
  });
});

async function refreshSilently() {
  if (busy || stopped) return;
  const before = revision;
  try {
    const updated = await api("state");
    if (before === revision && !busy && !stopped) applyState(updated);
  } catch {
    if (before === revision && !busy && !stopped) notify("无法连接启动器。请确认启动器仍在运行。", true);
  }
}
refreshSilently();
const pollTimer = setInterval(refreshSilently, 2500);
