"use strict";
const $ = (id) => document.getElementById(id);
const state = { config: null, analysisId: null, tracks: [], busy: false, jobId: null, cover: null, image: null, editingImage: null, crop: null, pollTimer: null };
const POLL_MS = 850;
const STORAGE_KEY = "mixtape-draft-v1";

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
    throw new Error("앱에 연결할 수 없어요. 실행 창이 열려 있는지 확인하고 페이지를 새로고침하세요.", { cause: error });
  }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "요청을 처리하지 못했어요.");
  return result;
}

function saveDraft() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      links: $("links").value, title: $("title").value, artist: $("artist").value, album: $("album").value,
      analysisId: state.analysisId, tracks: state.tracks, jobId: state.jobId, cover: state.cover,
    }));
  } catch (error) {
    notify("브라우저에 임시 저장하지 못했어요. 새로고침하면 입력 내용이 사라질 수 있어요.");
  }
}

function controls() {
  $("analyze").disabled = state.busy || !state.config || !$("links").value.trim();
  $("render").disabled = state.busy || !state.config || !!state.config.missing.length || !state.analysisId || !state.tracks.length || state.tracks.some((track) => track.error) || !$("title").value.trim();
  for (const id of ["links", "title", "artist", "album", "choose-cover", "remove-cover"]) $(id).disabled = state.busy;
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
    title.textContent = track.title;
    title.title = track.title;
    const detail = document.createElement("small");
    detail.textContent = track.error || "YouTube · 전체 오디오";
    copy.append(title, detail);
    const time = document.createElement("span");
    time.className = "track-time";
    time.textContent = track.error ? "—" : duration(track.duration).replace(/^00:/, "");
    const actions = document.createElement("div");
    actions.className = "track-actions";
    [["↑", "위로 이동", -1], ["↓", "아래로 이동", 1], ["×", "목록에서 제거", 0]].forEach(([symbol, label, offset]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "icon-button";
      button.textContent = symbol;
      button.setAttribute("aria-label", `${index + 1}번째 곡 ${label}`);
      button.title = label;
      button.dataset.edge = String(offset !== 0 && (index + offset < 0 || index + offset >= state.tracks.length));
      button.addEventListener("click", () => {
        if (offset === 0) state.tracks.splice(index, 1);
        else [state.tracks[index], state.tracks[index + offset]] = [state.tracks[index + offset], state.tracks[index]];
        renderTracks(); saveDraft();
      });
      actions.append(button);
    });
    row.append(number, copy, time, actions);
    fragment.append(row);
  });
  $("track-list").replaceChildren(fragment);
  $("empty-list").hidden = !!state.tracks.length;
  $("track-count").textContent = `${state.tracks.length}곡`;
  $("expected").textContent = duration(state.tracks.reduce((sum, track) => sum + track.duration, 0));
  $("duration-note").textContent = state.tracks.some((track) => track.error)
    ? "확인된 영상만 합산했어요. 오류가 있는 곡은 제거하거나 다시 확인해주세요."
    : "영상 길이의 합계 · 앞뒤 무음 제거 후 조금 짧아져요.";
  controls();
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
  $("cancel").textContent = "작업 취소";
  $("progress").value = 0;
  controls(); saveDraft();
  pollJob(id);
}

async function pollJob(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    $("progress-title").textContent = job.message;
    $("progress").value = job.progress;
    $("progress-eyebrow").textContent = job.kind === "analyze" ? "COLLECTING YOUR SONGS" : "MAKING YOUR MIX";
    $("progress-detail").textContent = job.kind === "analyze"
      ? `${job.tracks.length}개 영상 확인 · 전체 길이를 확인하고 있어요.`
      : `${job.tracks.length}곡 무음 정리 완료 · 정리된 길이 ${duration(job.trimmed_duration)} · 제거한 무음 ${(job.removed || 0).toFixed(1)}초`;
    if (job.kind === "analyze") {
      state.tracks = job.tracks;
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
      $("result-summary").textContent = `${job.tracks.length}곡 · ${(job.size / 1024 / 1024).toFixed(1)} MB · 무음 ${(job.removed || 0).toFixed(1)}초 제거`;
      $("progress-title").textContent = "당신의 믹스가 완성됐어요.";
      $("progress-detail").textContent = "아래에서 들어보거나 MP3를 저장하세요. outputs 폴더에도 원본이 보관돼요.";
      $("download").href = `/api/jobs/${job.id}/download`;
      $("download").download = job.filename;
      $("player").src = $("download").href;
    } else if (state.tracks.some((track) => track.error)) notify("일부 영상 정보를 가져오지 못했어요. 표시된 이유를 확인하고 해당 곡을 제거하거나 목록을 다시 확인해주세요.");
    if (job.cleanup_warning) notify(job.cleanup_warning);
    controls(); saveDraft();
  } catch (error) {
    notify(error.message);
    $("progress-title").textContent = "연결을 확인해주세요. 새로고침하면 진행 중인 작업을 다시 찾을 수 있어요.";
    // Keep submission locked: a connection failure does not mean the worker stopped.
    $("cancel").hidden = true;
  }
}

$("links").addEventListener("input", () => {
  state.analysisId = null; state.tracks = []; renderTracks(); saveDraft();
});
for (const id of ["title", "artist", "album"]) $(id).addEventListener("input", () => { controls(); saveDraft(); });

$("analyze").addEventListener("click", async () => {
  notify(); state.busy = true; controls();
  try {
    const job = await api("/api/analyze", { links: $("links").value });
    state.analysisId = null; state.tracks = []; renderTracks(); watchJob(job.id);
  } catch (error) { state.busy = false; controls(); notify(error.message); }
});

$("render").addEventListener("click", async () => {
  notify(); state.busy = true; controls();
  try {
    const job = await api("/api/render", {
      analysis_id: state.analysisId, track_ids: state.tracks.map((track) => track.id),
      title: $("title").value, artist: $("artist").value, album: $("album").value, cover: state.cover,
    });
    watchJob(job.id);
    $("progress-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) { state.busy = false; controls(); notify(error.message); }
});

$("cancel").addEventListener("click", async () => {
  $("cancel").disabled = true;
  try { await api(`/api/jobs/${state.jobId}/cancel`, {}); $("cancel").textContent = "취소 중…"; }
  catch (error) { notify(error.message); $("cancel").disabled = false; }
});

$("choose-cover").addEventListener("click", () => $("cover-file").click());
$("remove-cover").addEventListener("click", () => { state.cover = null; state.image = null; state.crop = null; showCover(); saveDraft(); });

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
  if (!state.config || file.size > state.config.image_max_bytes) { notify("표지 이미지는 20 MB 이하로 넣어주세요."); return; }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = url; await image.decode();
    if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > state.config.image_max_pixels) throw new Error("이미지 크기가 너무 크거나 올바르지 않아요. 4천만 화소 이하의 이미지를 사용하세요.");
    notify(); openCrop(image);
  } catch (error) { notify(error instanceof DOMException ? "이미지를 열지 못했어요. JPG, PNG, WebP 등 브라우저가 읽을 수 있는 이미지를 사용하세요." : error.message); }
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
  showCover(); saveDraft(); $("crop-dialog").close();
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
    state.config = await api("/api/config");
    $("links").maxLength = state.config.links_max_chars;
    for (const id of ["title", "artist", "album"]) $(id).maxLength = state.config.tag_max_chars;
    $("bitrate-label").textContent = `${parseInt(state.config.bitrate, 10)} kbps`;
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(STORAGE_KEY)); }
    catch (error) { notify("저장된 입력 내용을 읽지 못했어요. 다시 입력해주세요."); }
    if (draft && typeof draft === "object") {
      for (const id of ["links", "title", "artist", "album"]) if (typeof draft[id] === "string") $(id).value = draft[id];
      if (typeof draft.cover === "string" && draft.cover.startsWith("data:image/jpeg;base64,")) {
        state.cover = draft.cover;
        const restored = new Image(); restored.src = draft.cover;
        try { await restored.decode(); state.image = restored; } catch (error) { state.cover = null; notify("저장된 표지를 복원하지 못했어요. 이미지를 다시 선택해주세요."); }
      }
      if (draft.analysisId) {
        try {
          const analysis = await api(`/api/jobs/${encodeURIComponent(draft.analysisId)}`);
          if (analysis.kind === "analyze" && analysis.status === "done") {
            state.analysisId = analysis.id;
            const byId = new Map(analysis.tracks.map((track) => [track.id, track]));
            state.tracks = Array.isArray(draft.tracks) ? draft.tracks.map((track) => byId.get(track.id)).filter(Boolean) : analysis.tracks;
          }
        } catch (error) { notify("앱이 다시 시작됐어요. 입력한 링크의 목록을 다시 확인해주세요."); }
      }
    }
    showCover(); renderTracks();
    if (state.config.missing.length) notify(`필수 도구가 없어요: ${state.config.missing.join(", ")}. README의 설치 안내를 확인하세요.`);
    const jobId = state.config.active_job || draft?.jobId;
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
