/* Karaoke Studio – Web-App (ohne Build-Schritt) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ------------------------------------------------------------------ Einstellungen
const store = {
  get(k, d = null) { try { const v = localStorage.getItem("ks." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ks." + k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem("ks." + k); } catch {} },
};
const DEFAULT_REPO = "virtualchickenfr/Candyrain";
const cfg = {
  get token() { return store.get("token", ""); },
  get repo() { return store.get("repo", DEFAULT_REPO); },
  get name() { return store.get("name", ""); },
};

// ------------------------------------------------------------------ GitHub-API
const API = "https://api.github.com";
let branch = store.get("branch", "main");

async function gh(path, { method = "GET", body, accept = "application/vnd.github+json", raw = false } = {}) {
  const res = await fetch(path.startsWith("http") ? path : `${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      Accept: accept,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).message || msg; } catch {}
    const err = new Error(msg); err.status = res.status; throw err;
  }
  if (raw) return res;
  if (res.status === 204) return null;
  return res.json();
}
const repoPath = (p = "") => `/repos/${cfg.repo}${p}`;
const rawFile = (path, ref = branch) => gh(repoPath(`/contents/${encodeURI(path)}?ref=${encodeURIComponent(ref)}`), { accept: "application/vnd.github.raw+json", raw: true });
const VIDEO_BRANCH = "videos";

function friendlyError(e) {
  if (e.status === 401) return "Der Schlüssel ist ungültig oder abgelaufen. Trenne das Gerät in den Einstellungen und verbinde es neu.";
  if (e.status === 403) return "Der Schlüssel darf das nicht. Prüfe, ob „Actions“ und „Contents“ auf „Read and write“ stehen.";
  if (e.status === 404) return "Repository nicht gefunden. Prüfe den Namen und ob der Schlüssel Zugriff darauf hat.";
  if (e instanceof TypeError) return "Keine Verbindung. Prüfe das Internet und versuche es noch einmal.";
  return `GitHub meldet: ${e.message}`;
}

// ------------------------------------------------------------------ Hilfen
const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Math.random().toString(36).slice(2, 8);
let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), ms);
}
function show(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  window.scrollTo(0, 0);
}
function fillRange(input) {
  const p = ((input.value - input.min) / (input.max - input.min)) * 100;
  input.style.setProperty("--fill", `${p}%`);
}

// ------------------------------------------------------------------ Einrichtung
async function connect(token, repo) {
  store.set("token", token.trim());
  store.set("repo", repo.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "").replace(/\/$/, ""));
  const info = await gh(repoPath());
  branch = info.default_branch || "main";
  store.set("branch", branch);
  if (!info.permissions?.push) throw Object.assign(new Error("keine Schreibrechte"), { status: 403 });
}

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter; btn.disabled = true; $("#setup-error").textContent = "";
  try {
    await connect($("#setup-token").value, $("#setup-repo").value);
    store.set("name", $("#setup-name").value.trim());
    openLibrary();
  } catch (err) {
    store.del("token");
    $("#setup-error").textContent = friendlyError(err);
  } finally { btn.disabled = false; }
});

// ------------------------------------------------------------------ Bibliothek
let songs = store.get("songs", []);
const coverCache = new Map();

async function loadSongs() {
  try {
    let dirs = [];
    try {
      dirs = (await gh(repoPath(`/contents/songs?ref=${branch}`))).filter((d) => d.type === "dir");
    } catch (e) { if (e.status !== 404) throw e; }
    const known = new Map(songs.map((s) => [s.id, s]));
    const metas = await Promise.all(dirs.map(async (d) => {
      if (known.has(d.name)) return known.get(d.name);
      try { return await (await rawFile(`songs/${d.name}/meta.json`)).json(); } catch { return null; }
    }));
    songs = metas.filter(Boolean).sort((a, b) => (b.added || "").localeCompare(a.added || ""));
    store.set("songs", songs);
    $("#library-error").textContent = "";
  } catch (e) {
    $("#library-error").textContent = friendlyError(e);
  }
  renderSongs();
}

function renderSongs() {
  const q = $("#search").value.trim().toLowerCase();
  const list = songs.filter((s) => !q || `${s.title} ${s.artist}`.toLowerCase().includes(q));
  $("#song-list").innerHTML = list.map((s) => `
    <li><button class="song" data-id="${esc(s.id)}">
      <span class="cover" data-cover="${s.has_cover ? esc(s.id) : ""}">${esc((s.title || "?")[0].toUpperCase())}</span>
      <span class="song-text"><b>${esc(s.title)}</b><span>${esc(s.artist || "Unbekannt")}${s.added_by ? ` · von ${esc(s.added_by)}` : ""}</span></span>
      <span class="song-meta">${fmt(s.duration)}</span>
    </button></li>`).join("");
  $("#library-empty").hidden = songs.length > 0 || !!$("#library-error").textContent;
  $$("[data-cover]").forEach((el) => el.dataset.cover && loadCover(el.dataset.cover).then((url) => {
    if (url) { el.style.backgroundImage = `url(${url})`; el.textContent = ""; }
  }));
}

async function loadCover(id) {
  if (!coverCache.has(id)) {
    coverCache.set(id, rawFile(`songs/${id}/cover.jpg`).then((r) => r.blob()).then((b) => URL.createObjectURL(b)).catch(() => null));
  }
  return coverCache.get(id);
}

$("#search").addEventListener("input", renderSongs);
$("#song-list").addEventListener("click", (e) => {
  const b = e.target.closest(".song"); if (b) openPlayer(songs.find((s) => s.id === b.dataset.id));
});

// ------------------------------------------------------------------ Laufende Aufträge
let jobTimer = null;
let lastActive = 0;
const seenDone = new Set(store.get("seenDone", []));

async function loadJobs() {
  clearTimeout(jobTimer);
  let runs = [];
  try {
    runs = (await gh(repoPath(`/actions/runs?per_page=20`))).workflow_runs || [];
  } catch { jobTimer = setTimeout(loadJobs, 30000); return; }

  const recent = Date.now() - 1000 * 60 * 60 * 6;
  const relevant = runs.filter((r) => /process\.yml|render\.yml/.test(r.path || ""));
  const active = relevant.filter((r) => r.status !== "completed");
  const failed = relevant.filter((r) => r.status === "completed" && r.conclusion === "failure" &&
    new Date(r.updated_at).getTime() > recent && !seenDone.has(r.id));
  const finished = relevant.filter((r) => r.status === "completed" && r.conclusion === "success" && !seenDone.has(r.id) &&
    new Date(r.updated_at).getTime() > recent);

  $("#jobs").innerHTML = [...active, ...failed].map((r) => {
    const name = esc((r.display_title || r.name).replace(/\s*\[[^\]]*\]\s*$/, "").replace(/^[🎤🎬]\s*/u, ""));
    const isExport = /render\.yml/.test(r.path);
    const state = r.status === "completed" ? "failed" : r.status === "queued" ? "queued" : "running";
    const mins = Math.max(0, Math.round((Date.now() - new Date(r.run_started_at || r.created_at)) / 60000));
    const text = state === "failed" ? "Hat nicht geklappt – tippe auf „Details“, um den Grund zu sehen."
      : state === "queued" ? "Wartet auf einen freien GitHub-Rechner …"
      : isExport ? `Video wird erstellt · seit ${mins} Min.` : `Wird verarbeitet · seit ${mins} Min. (dauert ca. 10–15)`;
    return `<div class="job ${state}"><span class="dot"></span>
      <span class="job-text"><b>${isExport ? "Video: " : ""}${name}</b><span>${text}</span></span>
      <a href="${esc(r.html_url)}" target="_blank" rel="noopener">Details</a>
      ${state === "failed" ? `<button class="btn btn-ghost" data-dismiss="${r.id}">OK</button>` : ""}</div>`;
  }).join("");

  if (finished.length) {
    finished.forEach((r) => seenDone.add(r.id));
    store.set("seenDone", [...seenDone].slice(-100));
    if (finished.some((r) => /process\.yml/.test(r.path))) { toast("Neuer Song ist fertig 🎤"); loadSongs(); }
    if (finished.some((r) => /render\.yml/.test(r.path))) toast("Dein Video ist fertig – unter „Videos“ im Player.");
  }
  lastActive = active.length;
  jobTimer = setTimeout(loadJobs, active.length ? 15000 : 60000);
}
$("#jobs").addEventListener("click", (e) => {
  const id = e.target.dataset?.dismiss; if (!id) return;
  seenDone.add(Number(id)); store.set("seenDone", [...seenDone].slice(-100)); loadJobs();
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && cfg.token) loadJobs(); });

// ------------------------------------------------------------------ Song hinzufügen
let addTab = "yt";
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  addTab = t.dataset.tab;
  $$(".tab").forEach((x) => x.setAttribute("aria-selected", String(x === t)));
  $$("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== addTab));
}));
$("#btn-add").addEventListener("click", () => { $("#add-error").textContent = ""; $("#dlg-add").showModal(); });

function toBase64(buf) {
  const bytes = new Uint8Array(buf); let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

$("#add-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault();
  const btn = $("#add-submit"); const err = $("#add-error"); err.textContent = "";
  const reqId = uid();
  const inputs = {
    artist: $("#add-artist").value.trim(), title: $("#add-title").value.trim(),
    added_by: cfg.name, request_id: reqId,
  };
  try {
    btn.disabled = true;
    if (addTab === "yt") {
      const url = $("#add-url").value.trim();
      if (!/^https?:\/\/(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//.test(url)) { err.textContent = "Bitte einen YouTube-Link einfügen, z. B. https://youtu.be/…"; return; }
      inputs.url = url;
    } else {
      const file = $("#add-file").files[0];
      if (!file) { err.textContent = "Bitte eine Audiodatei auswählen."; return; }
      if (file.size > 40 * 1024 * 1024) { err.textContent = "Die Datei ist größer als 40 MB. Bitte eine kleinere MP3 wählen."; return; }
      btn.textContent = "Lade hoch …";
      const safe = file.name.normalize("NFKD").replace(/[^\w.-]+/g, "_").slice(-60);
      const path = `inbox/${reqId}-${safe}`;
      await gh(repoPath(`/contents/${path}`), {
        method: "PUT",
        body: { message: `Upload: ${file.name}`, content: toBase64(await file.arrayBuffer()), branch },
      });
      inputs.upload_path = path;
      if (!inputs.title) inputs.title = file.name.replace(/\.[^.]+$/, "");
    }
    btn.textContent = "Starte …";
    await gh(repoPath(`/actions/workflows/process.yml/dispatches`), { method: "POST", body: { ref: branch, inputs } });
    $("#dlg-add").close(); $("#add-form").reset();
    toast("Song wird erstellt. Das dauert etwa 10–15 Minuten.");
    setTimeout(loadJobs, 2500);
  } catch (ex) {
    err.textContent = friendlyError(ex);
  } finally {
    btn.disabled = false; btn.textContent = "Song erstellen";
  }
});

// ------------------------------------------------------------------ Einstellungen
$("#btn-settings").addEventListener("click", () => {
  $("#settings-info").textContent = `Verbunden mit ${cfg.repo}.`;
  $("#set-name").value = cfg.name;
  $("#dlg-settings").showModal();
});
$("#btn-save-settings").addEventListener("click", () => store.set("name", $("#set-name").value.trim()));
$("#btn-logout").addEventListener("click", () => {
  if (!confirm("Dieses Gerät trennen? Der Schlüssel wird hier gelöscht.")) return;
  ["token", "songs", "seenDone"].forEach(store.del);
  $("#dlg-settings").close(); location.reload();
});

// ------------------------------------------------------------------ Audio-Engine
const engine = {
  ctx: null, inst: null, voc: null, gInst: null, gVoc: null, srcs: [],
  playing: false, offset: 0, startedAt: 0, duration: 0,
  ensure() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.gInst = this.ctx.createGain(); this.gVoc = this.ctx.createGain();
      this.gInst.connect(this.ctx.destination); this.gVoc.connect(this.ctx.destination);
    }
    return this.ctx;
  },
  get time() { return this.playing ? Math.min(this.duration, this.offset + this.ctx.currentTime - this.startedAt) : this.offset; },
  _stopSources() { this.srcs.forEach((s) => { s.onended = null; try { s.stop(); } catch {} }); this.srcs = []; },
  async play() {
    if (!this.inst) return;
    await this.ensure().resume();
    if (this.offset >= this.duration - 0.05) this.offset = 0;
    this._stopSources();
    const when = this.ctx.currentTime + 0.05;
    [[this.inst, this.gInst], [this.voc, this.gVoc]].forEach(([buf, g]) => {
      const s = this.ctx.createBufferSource(); s.buffer = buf; s.connect(g); s.start(when, this.offset); this.srcs.push(s);
    });
    this.srcs[0].onended = () => { if (this.playing && this.time >= this.duration - 0.1) { this.pause(); this.offset = this.duration; onEnded(); } };
    this.startedAt = when; this.playing = true;
  },
  pause() { if (!this.playing) return; this.offset = this.time; this._stopSources(); this.playing = false; },
  seek(t) { const was = this.playing; this.pause(); this.offset = Math.max(0, Math.min(this.duration, t)); if (was) this.play(); },
  setVolumes(voice, music) {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    this.gVoc.gain.setTargetAtTime(voice, now, 0.03);
    this.gInst.gain.setTargetAtTime(music, now, 0.03);
  },
  unload() { this.pause(); this.inst = this.voc = null; this.offset = 0; },
};

// ------------------------------------------------------------------ Player
let current = null, lyrics = null, curLine = -2, raf = 0, loadToken = 0;

async function openPlayer(song) {
  if (!song) return;
  current = song; lyrics = null; curLine = -2;
  engine.unload(); engine.ensure();
  show("player");
  $("#p-title").textContent = song.title; $("#p-artist").textContent = song.artist || "";
  $("#player-bg").style.backgroundImage = "";
  if (song.has_cover) loadCover(song.id).then((u) => u && current === song && ($("#player-bg").style.backgroundImage = `url(${u})`));
  $("#lyrics").innerHTML = ""; $("#p-loading").hidden = false; $("#p-loading-text").textContent = "Lade Song …";
  $("#btn-play").disabled = true;
  const my = ++loadToken;
  const prefs = store.get(`mix.${song.id}`, null);
  $("#vol-voice").value = prefs?.voice ?? store.get("voiceDefault", 0);
  $("#vol-music").value = prefs?.music ?? 100;
  updateMix();
  try {
    const [ly, inst, voc] = await Promise.all([
      rawFile(`songs/${song.id}/lyrics.json`).then((r) => r.json()),
      rawFile(`songs/${song.id}/instrumental.mp3`).then((r) => r.arrayBuffer()),
      rawFile(`songs/${song.id}/vocals.mp3`).then((r) => r.arrayBuffer()),
    ]);
    if (my !== loadToken) return;
    $("#p-loading-text").textContent = "Bereite Audio vor …";
    const ctx = engine.ensure();
    const decode = (buf) => new Promise((res, rej) => ctx.decodeAudioData(buf, res, rej));
    const [bi, bv] = await Promise.all([decode(inst), decode(voc)]);
    if (my !== loadToken) return;
    engine.inst = bi; engine.voc = bv; engine.duration = Math.min(bi.duration, bv.duration);
    lyrics = ly;
    $("#t-dur").textContent = fmt(engine.duration);
    $("#p-loading").hidden = true; $("#btn-play").disabled = false;
    updateMix(); renderLyrics(0, true); tick();
  } catch (e) {
    if (my !== loadToken) return;
    $("#p-loading-text").textContent = e.status ? friendlyError(e) : "Der Song konnte nicht geladen werden. Versuche es noch einmal.";
    $(".spinner").hidden = true;
  }
}

function closePlayer() {
  loadToken++; cancelAnimationFrame(raf); engine.unload(); setPlayingUI(false);
  if (document.fullscreenElement) document.exitFullscreen?.();
  $(".spinner").hidden = false; show("library");
}

function lineIndexAt(t) {
  const L = lyrics?.lines || [];
  let i = -1;
  for (let k = 0; k < L.length; k++) { if (L[k].start - 0.35 <= t) i = k; else break; }
  return i;
}

function lineHTML(line, cls) {
  if (!line) return `<div class="ly ${cls}">&nbsp;</div>`;
  return `<div class="ly ${cls}">${line.words.map((w) => `<span class="w">${esc(w.t)}</span>`).join(" ")}</div>`;
}

function renderLyrics(t, force = false) {
  const L = lyrics?.lines || [];
  const el = $("#lyrics");
  if (!L.length) {
    if (force) el.innerHTML = `<div class="intro-title">${esc(current.title)}</div><div class="no-lyrics">Für diesen Song wurden keine Lyrics gefunden.</div>`;
    return;
  }
  let i = lineIndexAt(t);
  // nach langer Pause: nächste Zeile schon zeigen statt der alten
  if (i >= 0 && L[i + 1] && t > L[i].end + 1.5 && L[i + 1].start - t < 3.5) i = i + 1;
  const showIntro = i < 0 && t < L[0].start - 3;
  const key = showIntro ? -1 : Math.max(0, i);
  if (force || key !== curLine) {
    curLine = key;
    if (showIntro) {
      el.innerHTML = `<div class="intro-title">${esc(current.title)}</div><div class="intro-artist">${esc(current.artist || "")}</div>`;
    } else {
      const c = Math.max(0, i);
      el.innerHTML = `<div class="countdown" id="cd"></div>` +
        lineHTML(L[c - 1], "ly-prev") + lineHTML(L[c], "ly-cur") + lineHTML(L[c + 1], "ly-next") + lineHTML(L[c + 2], "ly-next2");
    }
  }
  if (showIntro) return;
  const c = Math.max(0, i), line = L[c];
  // Wort-Füllung der aktuellen Zeile
  const spans = $$(".ly-cur .w", el);
  line.words.forEach((w, k) => {
    const p = t <= w.s ? 0 : t >= w.e ? 1 : (t - w.s) / Math.max(0.01, w.e - w.s);
    spans[k]?.style.setProperty("--p", p.toFixed(3));
  });
  // Countdown vor dem Einsatz (am Anfang und nach langen Pausen)
  const cd = $("#cd", el);
  const prevEnd = c > 0 ? L[c - 1].end : 0;
  const until = line.start - t;
  const n = (until > 0 && until <= 3 && line.start - prevEnd > 6) ? Math.ceil(until) : 0;
  if (cd && cd.dataset.n !== String(n)) { cd.dataset.n = n; cd.innerHTML = "<i></i>".repeat(n); }
}

function tick() {
  cancelAnimationFrame(raf);
  const loop = () => {
    const t = engine.time;
    if (lyrics) renderLyrics(t);
    $("#t-cur").textContent = fmt(t);
    const seek = $("#seek");
    if (!seek.dragging && engine.duration) { seek.value = Math.round((t / engine.duration) * 1000); fillRange(seek); }
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
}

function setPlayingUI(on) {
  $("#btn-play").classList.toggle("playing", on);
  $("#btn-play").setAttribute("aria-label", on ? "Pause" : "Abspielen");
}
function onEnded() { setPlayingUI(false); }
async function togglePlay() {
  if (!engine.inst) return;
  if (engine.playing) engine.pause(); else await engine.play();
  setPlayingUI(engine.playing);
}

function updateMix() {
  const v = Number($("#vol-voice").value), m = Number($("#vol-music").value);
  $("#o-voice").textContent = `${v} %`; $("#o-music").textContent = `${m} %`;
  fillRange($("#vol-voice")); fillRange($("#vol-music"));
  engine.setVolumes(v / 100, m / 100);
  if (current) store.set(`mix.${current.id}`, { voice: v, music: m });
  $("#btn-export").textContent = `Video mit ${v} % Gesang erstellen`;
}

$("#btn-play").addEventListener("click", togglePlay);
$("#btn-rew").addEventListener("click", () => engine.seek(engine.time - 10));
$("#btn-fwd").addEventListener("click", () => engine.seek(engine.time + 10));
$("#btn-back").addEventListener("click", closePlayer);
$("#vol-voice").addEventListener("input", updateMix);
$("#vol-music").addEventListener("input", updateMix);
const seekEl = $("#seek");
seekEl.addEventListener("pointerdown", () => (seekEl.dragging = true));
seekEl.addEventListener("input", () => { fillRange(seekEl); $("#t-cur").textContent = fmt((seekEl.value / 1000) * engine.duration); });
seekEl.addEventListener("change", () => { seekEl.dragging = false; engine.seek((seekEl.value / 1000) * engine.duration); });

$("#btn-fullscreen").addEventListener("click", () => {
  const el = $("#view-player");
  if (document.fullscreenElement) document.exitFullscreen?.();
  else (el.requestFullscreen || el.webkitRequestFullscreen)?.call(el);
});

// Bedienelemente bei Mausruhe ausblenden (nur während der Wiedergabe)
let idleTimer;
["pointermove", "pointerdown", "keydown"].forEach((ev) => document.addEventListener(ev, () => {
  $("#view-player").classList.remove("idle"); clearTimeout(idleTimer);
  idleTimer = setTimeout(() => engine.playing && matchMedia("(hover: hover)").matches && $("#view-player").classList.add("idle"), 3500);
}));

document.addEventListener("keydown", (e) => {
  if ($("#view-player").hidden || $("dialog[open]") || e.target.matches("input[type=text], input[type=url], input[type=search]")) return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  if (e.code === "ArrowLeft") engine.seek(engine.time - 5);
  if (e.code === "ArrowRight") engine.seek(engine.time + 5);
  if (e.code === "Escape" && !document.fullscreenElement) closePlayer();
});

// ------------------------------------------------------------------ Video-Export
$("#btn-export").addEventListener("click", async () => {
  if (!current) return;
  const v = Number($("#vol-voice").value);
  const btn = $("#btn-export"); btn.disabled = true;
  try {
    await gh(repoPath(`/actions/workflows/render.yml/dispatches`), {
      method: "POST", body: { ref: branch, inputs: { song_id: current.id, vocals: String(v), request_id: uid() } },
    });
    toast(`Video mit ${v} % Gesang wird erstellt – dauert ein paar Minuten.`);
    setTimeout(loadJobs, 2500);
  } catch (e) { toast(friendlyError(e), 6000); }
  finally { btn.disabled = false; }
});

const videoBlobs = new Map();
async function videoBlob(path) {
  if (!videoBlobs.has(path)) {
    videoBlobs.set(path, rawFile(path, VIDEO_BRANCH).then((r) => r.blob()).catch((e) => { videoBlobs.delete(path); throw e; }));
  }
  return videoBlobs.get(path);
}

$("#btn-videos").addEventListener("click", async () => {
  if (!current) return;
  const list = $("#video-list"); const note = $("#videos-note");
  list.innerHTML = `<li><span>Lade …</span></li>`; note.textContent = "";
  $("#video-player").hidden = true; $("#video-player").removeAttribute("src");
  $("#dlg-videos").showModal();
  try {
    const files = (await gh(repoPath(`/contents/${current.id}?ref=${VIDEO_BRANCH}`)))
      .filter((f) => f.type === "file" && f.name.endsWith(".mp4"))
      .sort((a, b) => a.name.localeCompare(b.name, "de", { numeric: true }));
    list.innerHTML = files.map((f) => {
      const m = f.name.match(/gesang-(\d+)/);
      return `<li><b>${m ? `${m[1]} % Gesang` : esc(f.name)}</b><span>${Math.max(1, Math.round(f.size / 1048576))} MB</span>
        <span class="vid-actions">
          <button type="button" class="btn btn-ghost" data-play="${esc(f.path)}">Ansehen</button>
          <button type="button" class="btn btn-primary" data-save="${esc(f.path)}" data-name="${esc(f.name)}">Speichern</button>
        </span></li>`;
    }).join("") || `<li><span>Noch keine Videos.</span></li>`;
  } catch (e) {
    list.innerHTML = `<li><span>${e.status === 404 ? "Noch keine Videos. Erstelle eins mit dem Button im Player – nach ein paar Minuten ist es hier." : esc(friendlyError(e))}</span></li>`;
  }
});

$("#video-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-play],[data-save]"); if (!b) return;
  const path = b.dataset.play || b.dataset.save;
  const label = b.textContent; b.disabled = true; b.textContent = "Lädt …";
  try {
    const url = URL.createObjectURL(await videoBlob(path));
    if (b.dataset.play) {
      engine.pause(); setPlayingUI(false);
      const v = $("#video-player"); v.src = url; v.hidden = false; v.play().catch(() => {});
    } else {
      const a = document.createElement("a");
      a.href = url; a.download = b.dataset.name; document.body.append(a); a.click(); a.remove();
      $("#videos-note").textContent = "Das Video wird gespeichert. Auf dem iPhone: im Video auf Teilen tippen → „Video sichern“.";
    }
  } catch (err) {
    $("#videos-note").textContent = friendlyError(err);
  } finally { b.disabled = false; b.textContent = label; }
});
$("#dlg-videos").addEventListener("close", () => { const v = $("#video-player"); v.pause(); });

// ------------------------------------------------------------------ Start
function openLibrary() {
  show("library"); renderSongs(); loadSongs(); loadJobs();
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

(function init() {
  $("#setup-repo").value = cfg.repo;
  if (cfg.token) openLibrary(); else show("setup");
})();
