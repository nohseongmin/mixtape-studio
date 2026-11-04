"use strict";
const $ = (id) => document.getElementById(id);
const state = { config: null, analysisId: null, tracks: [], busy: false, jobId: null, cover: null, image: null, editingImage: null, crop: null, pollTimer: null, analysisTimer: null };
const POLL_MS = 850;
const AUTO_ANALYZE_MS = 700;

function duration(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60].map((value) => String(value).padStart(2, "0")).join(":");
}

function notify(message = "") {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

async function api(path, data) {
  let response;
  try {
    response = await fetch(path, data === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Mixtape-Request": "1" }, body: JSON.stringify(data),
    });
  } catch (error) {
    throw new Error("Unable to connect. Close Mixtape and open it again.", { cause: error });
  }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Unable to process the request.");
  return result;
}

function controls() {
  const tracks = state.tracks.filter((track) => track.input.trim());
  const unchecked = tracks.filter((track) => !track.id || track.pending || track.error).length;
  $("render").disabled = state.busy || !state.config || !!state.config.missing.length || !state.analysisId || !tracks.length || !!unchecked || !$("title").value.trim();
  for (const id of ["add-link", "title", "artist", "album", "choose-cover", "remove-cover", "bitrate"]) $(id).disabled = state.busy;
  document.querySelectorAll(".track-link").forEach((input) => { input.disabled = state.busy; });
  $("edit-cover").disabled = state.busy || !state.image;
  document.querySelectorAll(".track-actions button").forEach((button) => { button.disabled = state.busy || button.dataset.edge === "true"; });
}

function renderTracks() {
  const fragment = document.createDocumentFragment();
  state.tracks.forEach((track, index) => {
    const row = document.createElement("li");
    row.className = track.error ? "track error" : "track";
    const number = document.createElement("span");
    number.className = "track-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    copy.className = "track-copy";
    const title = document.createElement("strong");
    title.textContent = track.title || "Add a video link";
    title.title = track.title || "";
    const detail = document.createElement("small");
    detail.textContent = track.error || (track.pending ? "Checking video details…" : "");
    detail.hidden = !detail.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "track-link";
    input.value = track.input;
    input.placeholder = "https://youtu.be/…";
    input.spellcheck = false;
    input.autocomplete = "off";
    input.maxLength = state.config.links_max_chars;
    input.setAttribute("aria-label", `Video link ${index + 1}`);
    input.addEventListener("input", () => {
      state.tracks[index] = { input: input.value };
      title.textContent = input.value.trim() ? "New link · waiting to check" : "Add a video link";
      detail.textContent = "";
      detail.hidden = true;
      time.textContent = "—";
      row.classList.remove("error");
      updateTotal(); controls(); scheduleAnalysis();
    });
    input.addEventListener("paste", (event) => {
      const links = event.clipboardData.getData("text").trim().split(/\s+/).filter(Boolean);
      if (links.length < 2) return;
      event.preventDefault();
      const length = state.tracks.reduce((sum, item, i) => sum + (i === index ? 0 : item.input.length + 1), 0) + links.join("\n").length;
      if (length > state.config.links_max_chars) { notify("Too many link characters. Split the list into smaller batches."); return; }
      state.tracks.splice(index, 1, ...links.map((link) => ({ input: link })));
      renderTracks(); scheduleAnalysis();
    });
    copy.append(input, title, detail);
    const time = document.createElement("span");
    time.className = "track-time";
    time.textContent = track.error || !track.duration ? "—" : duration(track.duration).replace(/^00:/, "");
    const actions = document.createElement("div");
    actions.className = "track-actions";
    if (track.error || (track.pending && !state.busy)) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "icon-button";
      retry.textContent = "↻";
      retry.title = "Retry";
      retry.setAttribute("aria-label", `Retry track ${index + 1}`);
      retry.addEventListener("click", () => {
        state.tracks[index] = { input: track.input };
        renderTracks(); scheduleAnalysis();
      });
      actions.append(retry);
    }
    [["↑", "Move up", -1], ["↓", "Move down", 1], ["×", "Remove", 0]].forEach(([symbol, label, offset]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "icon-button";
      button.textContent = symbol;
      button.setAttribute("aria-label", `${label} track ${index + 1}`);
      button.title = label;
      button.dataset.edge = String(offset !== 0 && (index + offset < 0 || index + offset >= state.tracks.length));
      button.addEventListener("click", () => {
        if (offset === 0) {
          state.tracks.splice(index, 1);
          if (!state.tracks.length) state.tracks.push({ input: "" });
        }
        else [state.tracks[index], state.tracks[index + offset]] = [state.tracks[index + offset], state.tracks[index]];
        renderTracks();
      });
      actions.append(button);
    });
    row.append(number, copy, time, actions);
    fragment.append(row);
  });
  $("track-list").replaceChildren(fragment);
  updateTotal();
  controls();
}

function updateTotal() {
  const tracks = state.tracks.filter((track) => track.input.trim());
  $("track-count").textContent = `${tracks.length} tracks`;
  $("expected").textContent = duration(tracks.reduce((sum, track) => sum + (track.duration || 0), 0));
  $("duration-note").textContent = tracks.some((track) => track.error || track.pending || !track.id)
    ? "Checked tracks only. The total updates as new links are checked."
    : "Total video duration · slightly shorter after trimming edge silence.";
}

function showCover() {
  $("cover-preview").hidden = !state.cover;
  $("default-cover").hidden = !!state.cover;
  $("remove-cover").hidden = !state.cover;
  if (state.cover) $("cover-preview").src = state.cover;
  else $("cover-preview").removeAttribute("src");
  controls();
}

function watchJob(id) {
  clearTimeout(state.pollTimer);
  state.jobId = id;
  state.busy = true;
  $("progress-panel").hidden = false;
  $("result").hidden = true;
  $("player").pause();
  $("player").removeAttribute("src");
  $("cancel").hidden = false;
  $("cancel").disabled = false;
  $("cancel").textContent = "Cancel task";
  $("progress").value = 0;
  controls();
  pollJob(id);
}

async function pollJob(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    $("progress-title").textContent = job.message;
    $("progress").value = job.progress;
    $("progress-eyebrow").textContent = job.kind === "analyze" ? "COLLECTING YOUR SONGS" : "MAKING YOUR MIX";
    $("progress-detail").textContent = job.kind === "analyze"
      ? `${job.tracks.filter((track) => !track.pending).length} videos checked`
      : `${job.tracks.length} tracks trimmed · ${duration(job.trimmed_duration)} · ${(job.removed || 0).toFixed(1)}s of silence removed`;
    if (job.kind === "analyze") {
      let index = 0;
      state.tracks = state.tracks.map((track) => {
        if (!track.input.trim()) return track;
        const result = job.tracks[index++];
        return result ? { ...result, input: track.input } : track;
      });
      state.analysisId = job.status === "done" ? job.id : null;
      renderTracks();
    }
    if (job.status === "running") {
      state.pollTimer = setTimeout(() => pollJob(id), POLL_MS);
      return;
    }
    state.busy = false;
    $("cancel").hidden = true;
    if (job.status === "error" || job.status === "cancelled") notify(job.message);
    else if (job.kind === "render") {
      $("result").hidden = false;
      $("result-duration").textContent = duration(job.duration);
      $("result-summary").textContent = `${job.tracks.length} tracks · ${parseInt(job.bitrate || state.config.bitrate, 10)} kbps · ${(job.size / 1024 / 1024).toFixed(1)} MB · ${(job.removed || 0).toFixed(1)}s of silence removed`;
      $("progress-title").textContent = "Your mix is ready.";
      $("progress-detail").textContent = "Listen below or save your MP3. A copy is also kept in the outputs folder.";
      $("download").href = `/api/jobs/${job.id}/download`;
      $("download").download = job.filename;
      $("player").src = $("download").href;
    } else if (state.tracks.some((track) => track.error)) notify("Some videos could not be checked. Use ↻ to retry or edit the link.");
    if (job.cleanup_warning) notify(job.cleanup_warning);
    if (job.kind === "analyze") renderTracks();
    controls();
  } catch (error) {
    notify(error.message);
    $("progress-title").textContent = "Check your connection. Reload the window to reconnect to the running task.";
    // Keep submission locked: a connection failure does not mean the worker stopped.
    $("cancel").hidden = true;
  }
}

$("add-link").addEventListener("click", () => {
  state.tracks.push({ input: "" });
  renderTracks();
  $("track-list").lastElementChild.querySelector("input").focus();
});
for (const id of ["title", "artist", "album"]) $(id).addEventListener("input", () => { controls(); });

function scheduleAnalysis() {
  clearTimeout(state.analysisTimer);
  state.analysisTimer = setTimeout(() => {
    state.analysisTimer = null;
    analyzeLinks();
  }, AUTO_ANALYZE_MS);
}

async function analyzeLinks() {
  const tracks = state.tracks.filter((track) => track.input.trim());
  if (state.busy || !tracks.length || !tracks.some((track) => !track.id || track.pending)) return;
  if (state.tracks.some((track) => /\s/.test(track.input.trim()))) {
    notify("Use one link per row. Paste multiple links together to split them automatically.");
    return;
  }
  notify(); state.busy = true; controls();
  try {
    const job = await api("/api/analyze", { links: state.tracks.map((track) => track.input.trim()).filter(Boolean).join("\n") });
    state.analysisId = null; watchJob(job.id);
  } catch (error) { state.busy = false; controls(); notify(error.message); }
}


$("render").addEventListener("click", async () => {
  notify(); state.busy = true; controls();
  try {
    const job = await api("/api/render", {
      analysis_id: state.analysisId, track_ids: state.tracks.filter((track) => track.input.trim()).map((track) => track.id),
      title: $("title").value, artist: $("artist").value, album: $("album").value, cover: state.cover,
      bitrate: $("bitrate").value,
    });
    watchJob(job.id);
    $("progress-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) { state.busy = false; controls(); notify(error.message); }
});

$("cancel").addEventListener("click", async () => {
  $("cancel").disabled = true;
  try { await api(`/api/jobs/${state.jobId}/cancel`, {}); $("cancel").textContent = "Cancelling…"; }
  catch (error) { notify(error.message); $("cancel").disabled = false; }
});

$("choose-cover").addEventListener("click", () => $("cover-file").click());
$("remove-cover").addEventListener("click", () => { state.cover = null; state.image = null; state.crop = null; showCover(); });

function openCrop(image = state.image) {
  state.editingImage = image;
  const crop = image === state.image ? state.crop : null;
  $("zoom").value = crop?.zoom || 1;
  $("crop-x").value = crop?.x ?? 0.5;
  $("crop-y").value = crop?.y ?? 0.5;
  $("crop-dialog").showModal(); drawCrop();
}

function drawCrop(target = $("crop-canvas")) {
  const image = state.editingImage;
  if (!image) return;
  const side = Math.min(image.naturalWidth, image.naturalHeight) / Number($("zoom").value);
  const x = (image.naturalWidth - side) * Number($("crop-x").value);
  const y = (image.naturalHeight - side) * Number($("crop-y").value);
  const context = target.getContext("2d");
  context.fillStyle = "#ffffff"; context.fillRect(0, 0, target.width, target.height);
  context.drawImage(image, x, y, side, side, 0, 0, target.width, target.height);
}

$("cover-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  if (!state.config || file.size > state.config.image_max_bytes) { notify("Choose a cover image no larger than 20 MB."); return; }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = url; await image.decode();
    if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > state.config.image_max_pixels) throw new Error("The image is invalid or too large. Use an image with no more than 40 million pixels.");
    notify(); openCrop(image);
  } catch (error) { notify(error instanceof DOMException ? "Unable to open the image. Use a supported image such as JPG, PNG, or WebP." : error.message); }
  finally { URL.revokeObjectURL(url); controls(); }
});
$("edit-cover").addEventListener("click", () => openCrop());
for (const id of ["zoom", "crop-x", "crop-y"]) $(id).addEventListener("input", () => drawCrop());
$("reset-crop").addEventListener("click", () => { $("zoom").value = 1; $("crop-x").value = 0.5; $("crop-y").value = 0.5; drawCrop(); });
$("close-crop").addEventListener("click", () => $("crop-dialog").close());
$("apply-crop").addEventListener("click", () => {
  const output = document.createElement("canvas");
  output.width = output.height = state.config.cover_size;
  drawCrop(output);
  state.cover = output.toDataURL("image/jpeg", 0.9);
  state.image = state.editingImage;
  state.crop = { zoom: Number($("zoom").value), x: Number($("crop-x").value), y: Number($("crop-y").value) };
  showCover(); $("crop-dialog").close();
});
let drag = null;
$("crop-canvas").addEventListener("pointerdown", (event) => {
  drag = { x: event.clientX, y: event.clientY, cropX: Number($("crop-x").value), cropY: Number($("crop-y").value) };
  event.currentTarget.setPointerCapture(event.pointerId);
});
$("crop-canvas").addEventListener("pointermove", (event) => {
  if (!drag || !state.editingImage) return;
  const image = state.editingImage;
  const side = Math.min(image.naturalWidth, image.naturalHeight) / Number($("zoom").value);
  const scale = side / event.currentTarget.getBoundingClientRect().width;
  if (image.naturalWidth > side) $("crop-x").value = Math.max(0, Math.min(1, drag.cropX - (event.clientX - drag.x) * scale / (image.naturalWidth - side)));
  if (image.naturalHeight > side) $("crop-y").value = Math.max(0, Math.min(1, drag.cropY - (event.clientY - drag.y) * scale / (image.naturalHeight - side)));
  drawCrop();
});
for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) $("crop-canvas").addEventListener(name, () => { drag = null; });

async function init() {
  try {
    localStorage.removeItem("mixtape-draft-v1");
    for (const id of ["title", "artist", "album"]) { $(id).value = ""; $(id).autocomplete = "off"; }
    state.config = await api("/api/config");
    for (const id of ["title", "artist", "album"]) $(id).maxLength = state.config.tag_max_chars;
    $("bitrate").replaceChildren(...state.config.bitrates.map((value) => new Option(`${parseInt(value, 10)} kbps`, value)));
    $("bitrate").value = state.config.bitrate;
    if (!state.tracks.length) state.tracks.push({ input: "" });
    showCover(); renderTracks();
    if (state.config.missing.length) notify(`Required tools are missing: ${state.config.missing.join(", ")}. See the setup instructions in the README.`);
    const jobId = state.config.active_job;
    if (jobId) {
      try {
        const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (job.status === "running" || job.kind === "render") watchJob(jobId);
      }
      catch (error) { state.jobId = null; controls(); }
    }

  } catch (error) { notify(error.message); }
}
init();
