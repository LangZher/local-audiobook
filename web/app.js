// Local Audiobook UI: library, paginated reader with play + faint highlight, voices, speed log, storage.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
};
const store = {get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
               set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }};
const fmtS = s => s == null ? "–" : s < 90 ? `${s.toFixed(1)} s` : s < 5400 ? `${(s / 60).toFixed(1)} min` : `${(s / 3600).toFixed(1)} h`;
const fmtB = b => b < 1e6 ? `${(b / 1e3).toFixed(0)} KB` : b < 1e9 ? `${(b / 1e6).toFixed(1)} MB` : `${(b / 1e9).toFixed(2)} GB`;
const day = t => new Date(t * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
const CHARS_PER_AUDIO_S = 4.2;   // Chinese narration, measured on Ming 0.5B; only used for estimates

let S = {presets: [], backends: {}}, view = "library";

// ---------------- nav ----------------
function show(v) {
  view = v;
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  document.querySelectorAll(".view").forEach(s => s.classList.toggle("on", s.id === v));
  ({library: loadLibrary, gpu: loadGpu, voices: () => { renderVoices(); refreshSamples(); }, bench: loadBench, storage: loadStorage, reader: () => R.book && layout()}[v])();
}
document.querySelectorAll("#nav button").forEach(b => b.onclick = () => show(b.dataset.view));

// ---------------- library ----------------
// Guided flow: ① find → ② import → ③ listen. The step strip shows where you are; every panel says what to do next.
let scanned = false, fresh = new Set(store.get("fresh", []));

async function loadLibrary() {
  $("#libdir").textContent = `Your library folder: ${S.data_dir.replace(/^\/Users\/[^/]+/, "~")}/books`;
  const books = await api("/api/library");
  const anyAudio = books.some(b => Object.keys(b.audio).length);
  const done = [scanned || books.length > 0, books.length > 0, anyAudio];
  document.querySelectorAll("#steps li").forEach((li, i) => {
    li.classList.toggle("done", done[i]);
    li.classList.toggle("next", !done[i] && done.slice(0, i).every(Boolean));
  });
  $("#steps").hidden = done.every(Boolean);
  $("#books").innerHTML = books.length ? books.map(b => {
    const pct = b.progress ? Math.round(100 * (b.progress.ch + 1) / b.chapters) : 0;
    const made = Math.max(0, ...Object.values(b.audio));
    const au = made ? `<div class="au has">🔊 audio for ${Math.max(1, Math.round(100 * made / b.chunks))}% of the book</div>`
                    : `<div class="au">no audio yet · about ${fmtS(b.chars / CHARS_PER_AUDIO_S)} to listen</div>`;
    return `<div class="book" data-id="${b.id}"><div class="cv" style="background-image:url('/api/cover?book=${b.id}')"></div>
      <div class="t">${fresh.has(b.id) ? '<span class="badge">NEW</span>' : ""}${esc(b.title)}</div>
      <div class="a">${esc(b.author)} · ${(b.chars / 1e4).toFixed(1)}万字</div>${au}
      <div class="pg"><i style="width:${pct}%"></i></div></div>`;
  }).join("") : `<p class="muted" style="grid-column:1/-1">No books yet. Press <b>Find EPUBs on this Mac</b> above.</p>`;
  document.querySelectorAll(".book").forEach(el => el.onclick = () => {
    fresh.delete(el.dataset.id); store.set("fresh", [...fresh]);
    openBook(el.dataset.id);
  });
  return books;
}

$("#scan").onclick = async () => {
  const box = $("#scanlist");
  box.hidden = false;
  $("#scan").disabled = true;
  box.innerHTML = `<p class="muted">Looking for EPUBs in Apple Books, iCloud Drive, Downloads, Documents and Desktop… (a few seconds)</p>`;
  let found;
  try { found = await api("/api/scan", {}); } finally { $("#scan").disabled = false; }
  scanned = true;
  loadLibrary();
  const {new: nw, have} = found;
  const close = `<button id="scanclose">Close</button>`;
  if (!nw.length) {
    box.innerHTML = `<div class="scanhead"><b>Nothing new to import.</b><span class="muted">All ${have.length} books found are already in your library.</span><span class="grow"></span>${close}</div>`;
    $("#scanclose").onclick = () => box.hidden = true;
    return;
  }
  box.innerHTML = `<div class="scanhead"><b>Found ${nw.length} book${nw.length > 1 ? "s" : ""} not in your library</b>
      ${have.length ? `<span class="muted">(${have.length} more already in it)</span>` : ""}<span class="grow"></span>
      <button id="doimport" class="primary"></button>${close}</div>
    <p class="muted" style="margin:0 0 8px">Untick what you don't want, then press Import. Each book is copied into your library folder; the original stays where it is.
      Same book in two places? It is listed once (the Apple Books copy when there is one).</p>
    <table><tr><th><input type="checkbox" id="selall" checked title="Select all"></th><th>Title</th><th>Author</th><th class="n">Size</th><th>Found in</th></tr>
    ${nw.map((f, i) => `<tr><td><input type="checkbox" class="pick" data-i="${i}" checked></td><td>${esc(f.title)}</td><td>${esc(f.author)}</td>
      <td class="n">${fmtB(f.size)}</td><td class="muted">${esc(f.where)}${f.others.length ? `<br>also in ${f.others.map(esc).join(", ")}` : ""}</td></tr>`).join("")}</table>`;
  const picks = () => [...box.querySelectorAll(".pick:checked")];
  const label = () => { const n = picks().length; $("#doimport").textContent = n ? `Import ${n} book${n > 1 ? "s" : ""}` : "Select books to import"; $("#doimport").disabled = !n; };
  box.querySelectorAll(".pick").forEach(c => c.onchange = label);
  $("#selall").onchange = e => { box.querySelectorAll(".pick").forEach(c => c.checked = e.target.checked); label(); };
  $("#scanclose").onclick = () => box.hidden = true;
  label();
  $("#doimport").onclick = async () => {
    const paths = picks().map(c => nw[+c.dataset.i].path);
    $("#doimport").disabled = true;
    $("#doimport").textContent = `Importing ${paths.length}…`;
    const {imported} = await api("/api/import", {paths});
    imported.forEach(b => fresh.add(b.id)); store.set("fresh", [...fresh]);
    await loadLibrary();
    box.innerHTML = `<div class="scanhead"><b>✓ Imported ${imported.length} book${imported.length === 1 ? "" : "s"}</b>
      <span class="muted">They are below, marked NEW. Next: open one and press ▶ to listen.</span><span class="grow"></span>
      ${imported[0] ? `<button class="primary" id="opennew">Open ${esc(imported[0].title.slice(0, 18))}</button>` : ""}${close}</div>`;
    $("#scanclose").onclick = () => box.hidden = true;
    if (imported[0]) $("#opennew").onclick = () => { fresh.delete(imported[0].id); store.set("fresh", [...fresh]); openBook(imported[0].id); };
  };
};

// ---------------- reader ----------------
const R = {book: null, ch: 0, page: 0, pages: 1, pageOf: {}, order: [], ready: new Set(), asked: new Set(),
           playing: false, cur: null, waitingFor: null, map: store.get("map", true)};
const audio = $("#audio");
const presetId = () => $("#preset").value;

async function openBook(id, keepPage) {
  if (R.book?.id !== id) {
    stop();
    R.book = await api(`/api/book?id=${id}`);
    const prog = (await api("/api/library")).find(b => b.id === id)?.progress;
    R.ch = prog?.ch ?? 0;
    R.resumeChunk = prog?.chunk;
    $("#rtitle").textContent = R.book.title;
    $("#chapter").innerHTML = R.book.chapters.map((c, i) => `<option value="${i}">${esc(c.title)}</option>`).join("");
  }
  show("reader");
  showHint();
  renderChapter(R.ch, keepPage);
}

// How audio gets made, said once per book until dismissed, with today's measured speed
function showHint() {
  const h = $("#hint");
  if (store.get("hint-ok", false)) { h.hidden = true; return; }
  const rtf = lastRtf();
  const speed = rtf == null ? "Local TTS is slower than real time today"
    : rtf > 1 ? `This voice takes about ${rtf.toFixed(1)} s to make 1 s of speech`
    : `This voice is faster than real time (${rtf.toFixed(2)} s per 1 s of speech), so ▶ alone never waits`;
  h.innerHTML = `<span><b>▶ Play</b> reads from this page; each sentence's audio is made just before it plays (the first one takes ~10–20 s).
    ${speed}${rtf == null || rtf > 1 ? `, so playback pauses between sentences. For smooth listening, choose <b>Make audio ahead… → Next 10 pages</b> and come back in a while.` : "."}
    Audio is kept, so each sentence is made only once per voice.</span><button id="hintok">Got it</button>`;
  h.hidden = false;
  $("#hintok").onclick = () => { store.set("hint-ok", true); h.hidden = true; layout(); };
}

function renderChapter(ch, page = 0) {
  R.ch = ch;
  $("#chapter").value = ch;
  const c = R.book.chapters[ch];
  R.order = c.paras.flatMap(p => p.chunks.map(k => k.id));
  R.text = Object.fromEntries(c.paras.flatMap(p => p.chunks.map(k => [k.id, k.t])));
  $("#flow").innerHTML = c.paras.map(p => `<p class="${p.h ? "h" : ""}">${p.chunks.map(k =>
    `<span class="ck" data-id="${k.id}">${esc(k.t)}</span>`).join("")}</p>`).join("");
  R.ready = new Set();
  layout(page === "end" ? 1e9 : page);
  refreshReady();
}

function layout(page) {
  const wrap = $("#pagewrap"), flow = $("#flow");
  const W = wrap.clientWidth, gap = W > 900 ? 120 : 40, cols = W > 900 ? 2 : 1;
  R.W = W; R.gap = gap;
  flow.style.columnWidth = `${(W - gap * (cols - 1)) / cols}px`;
  flow.style.columnGap = `${gap}px`;
  flow.style.columnFill = "auto";
  flow.style.transition = "none";   // relayout jumps; only page turns slide
  flow.style.transform = "none";
  const wl = wrap.getBoundingClientRect().left;
  const pg = r => Math.max(0, Math.floor((r.left - wl + 2) / (W + gap)));
  R.pageOf = {}; R.split = {};
  for (const el of flow.querySelectorAll(".ck")) {
    const rs = [...el.getClientRects()].filter(r => r.width);
    if (!rs.length) continue;
    const id = el.dataset.id, p0 = pg(rs[0]), p1 = pg(rs[rs.length - 1]);
    R.pageOf[id] = p0;
    if (p1 > p0) {   // the sentence runs onto the next page: the share of its text on this page says when to turn
      const w = rs.reduce((a, r) => a + r.width, 0), w0 = rs.filter(r => pg(r) === p0).reduce((a, r) => a + r.width, 0);
      R.split[id] = {page: p1, at: w0 / w};
    }
  }
  R.pages = Math.max(1, ...Object.values(R.pageOf).map(p => p + 1));
  if (page === undefined) page = R.cur != null && R.pageOf[R.cur] != null ? R.pageOf[R.cur] : R.page;
  if (R.resumeChunk && R.pageOf[R.resumeChunk] != null) { page = R.pageOf[R.resumeChunk]; R.resumeChunk = null; }
  goPage(Math.min(page, R.pages - 1));
  flow.offsetWidth;   // apply the jump before turning the slide back on
  flow.style.transition = "";
}

function goPage(p) {
  if (p < 0) { if (R.ch > 0) renderChapter(R.ch - 1, "end"); return; }
  if (p >= R.pages) { if (R.ch < R.book.chapters.length - 1) renderChapter(R.ch + 1, 0); return; }
  R.page = p;
  $("#flow").style.transform = `translateX(${-p * (R.W + R.gap)}px)`;
  $("#pageno").textContent = `${R.book.chapters[R.ch].title} · page ${p + 1} / ${R.pages}`;
  paintReady();
  api("/api/progress", {book: R.book.id, ch: R.ch, page: p, chunk: idsOnPages(p, p)[0]}).catch(() => {});
}
const idsOnPages = (a, b) => R.order.filter(id => R.pageOf[id] >= a && R.pageOf[id] <= b);

$("#turnL").onclick = () => goPage(R.page - 1);
$("#turnR").onclick = () => goPage(R.page + 1);
$("#chapter").onchange = e => { stop(); renderChapter(+e.target.value, 0); };
window.addEventListener("resize", () => R.book && view === "reader" && layout());
document.addEventListener("keydown", e => {
  if (view !== "reader" || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  if (e.key === "ArrowRight") goPage(R.page + 1);
  else if (e.key === "ArrowLeft") goPage(R.page - 1);
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
});
$("#flow").ondblclick = e => { const id = e.target.closest(".ck")?.dataset.id; if (id) { window.getSelection().removeAllRanges(); startAt(id); } };
$("#map").onclick = () => { R.map = !R.map; store.set("map", R.map); paintReady(); };
// highlight strength 0–100: background and underline of the sentence being read
function setHl(v) {
  const st = document.documentElement.style, a = v / 100;
  st.setProperty("--now", `rgba(255, 214, 102, ${(0.03 + 0.47 * a).toFixed(3)})`);
  st.setProperty("--nowline", `rgba(255, 214, 102, ${Math.min(1, 1.2 * a).toFixed(3)})`);
}
$("#hl").value = store.get("hl", 55);
setHl(+$("#hl").value);
$("#hl").oninput = e => { setHl(+e.target.value); store.set("hl", +e.target.value); };
$("#preset").onchange = () => { store.set("preset", presetId()); stop(); R.asked.clear(); refreshReady(); };

async function refreshReady() {
  if (!R.book) return;
  const {ids} = await api(`/api/ready?book=${R.book.id}&preset=${presetId()}&ch=${R.ch}`);
  R.ready = new Set(ids);
  paintReady();
}

function paintReady() {
  const flow = $("#flow");
  flow.classList.toggle("map", R.map);
  $("#map").classList.toggle("on", R.map);
  for (const el of flow.querySelectorAll(".ck")) {
    const id = el.dataset.id;
    el.classList.toggle("ok", R.ready.has(id));
    el.classList.toggle("wait", !R.ready.has(id) && R.asked.has(id));
    el.classList.toggle("now", id === R.cur);
  }
  const ids = idsOnPages(R.page, R.page);
  $("#pagestat").textContent = `audio for this page: ${ids.filter(i => R.ready.has(i)).length} / ${ids.length}`;
}

async function request(ids, priority, label, replace) {
  ids = ids.filter(i => !R.ready.has(i));
  ids.forEach(i => R.asked.add(i));
  if (!ids.length && !replace) return;
  await api("/api/enqueue", {book: R.book.id, preset: presetId(), ids, priority, label, replace: !!replace});
  paintReady();
}

$("#pre").onchange = async e => {
  const v = e.target.value; e.target.value = "";
  if (!v) return;
  let ids, label;
  const c = R.book.chapters[R.ch].title;
  if (v === "page") { ids = idsOnPages(R.page, R.page); label = `${c} p.${R.page + 1}`; }
  else if (v === "10") { ids = idsOnPages(R.page, R.page + 9); label = `${c} p.${R.page + 1}–${Math.min(R.pages, R.page + 10)}`; }
  else if (v === "chapter") { ids = R.order; label = `${c} (whole chapter)`; }
  else {
    const chars = R.book.chapters.reduce((n, ch) => n + ch.paras.reduce((m, p) => m + p.chunks.reduce((k, x) => k + x.t.length, 0), 0), 0);
    const rtf = lastRtf();
    if (!confirm(`About ${fmtS(chars / CHARS_PER_AUDIO_S)} of audio. At the last measured RTF (${rtf ?? "?"}) that takes ~${rtf ? fmtS(chars / CHARS_PER_AUDIO_S * rtf) : "?"} of generation. Queue it in the background?`)) return;
    for (let i = 0; i < R.book.chapters.length; i++) {
      const ids2 = R.book.chapters[i].paras.flatMap(p => p.chunks.map(k => k.id));
      await api("/api/enqueue", {book: R.book.id, preset: presetId(), ids: ids2, priority: 2, label: `${R.book.chapters[i].title} (book)`});
    }
    $("#playstat").textContent = "Whole book queued; progress is at the top right.";
    return;
  }
  const todo = ids.filter(i => !R.ready.has(i));
  await request(ids, 2, label);
  if (!R.map) { R.map = true; store.set("map", true); paintReady(); }
  const rtf = lastRtf(), chars = todo.reduce((n, i) => n + R.text[i].length, 0);
  $("#playstat").textContent = !todo.length ? "This already has audio." :
    `Making audio for ${todo.length} sentences${rtf ? ` (~${fmtS(chars / CHARS_PER_AUDIO_S * rtf)})` : ""}: a dotted line marks each one that is done; progress is at the top right.`;
};

// playback: start at the first sentence of the page (or the one double-clicked), keep `lookahead` chunks queued ahead
const onPage = id => R.pageOf[id] === R.page || R.split[id]?.page === R.page;
function togglePlay() { R.playing ? stop() : startAt(R.cur && onPage(R.cur) ? R.cur : idsOnPages(R.page, R.page)[0]); }
$("#play").onclick = togglePlay;

async function startAt(id) {
  if (!id) return;
  audio.pause();
  R.playing = true;
  $("#play").textContent = "❚❚ Pause";
  const i = R.order.indexOf(id);
  await request(R.order.slice(i, i + 1 + S.lookahead), 0, "play", true);
  playChunk(id);
}

function stop() {
  R.playing = false; R.waitingFor = null;
  audio.pause();
  $("#play").textContent = "▶ Play";
  $("#playstat").textContent = "";
}

async function playChunk(id) {
  if (!R.playing) return;
  R.cur = id;
  if (!onPage(id)) goPage(R.pageOf[id]);
  paintReady();
  const i = R.order.indexOf(id);
  request(R.order.slice(i + 1, i + 1 + S.lookahead), 1, "read-ahead");
  if (!R.ready.has(id)) {
    R.waitingFor = id;
    $("#playstat").textContent = "generating this sentence…";
    return;   // the poller calls playChunk again once it is ready
  }
  R.waitingFor = null;
  $("#playstat").textContent = "";
  if (R.split[id] && R.page > R.pageOf[id]) goPage(R.pageOf[id]);   // (re)starting it: begin where it begins
  audio.src = `/api/audio?book=${R.book.id}&preset=${presetId()}&id=${id}&v=${Date.now()}`;
  try { await audio.play(); } catch (e) { $("#playstat").textContent = `can't play: ${e.message}`; }
}

// a sentence that runs onto the next page turns it when the voice gets there, not when the sentence ends
audio.ontimeupdate = () => {
  const s = R.playing && R.split[R.cur];
  if (s && audio.duration && R.page < s.page && audio.currentTime / audio.duration >= s.at) goPage(s.page);
};

audio.onended = () => {
  if (!R.playing) return;
  const i = R.order.indexOf(R.cur);
  if (i + 1 < R.order.length) playChunk(R.order[i + 1]);
  else if (R.ch < R.book.chapters.length - 1) { renderChapter(R.ch + 1, 0); startAt(R.order[0]); }
  else stop();
};

// ---------------- status poller ----------------
let lastRuns = [];
const lastRtf = () => { const r = lastRuns.filter(x => x.preset === presetId()).slice(-10); return r.length ? +(r.reduce((a, x) => a + x.rtf, 0) / r.length).toFixed(2) : null; };

async function poll() {
  try {
    const q = lastQ = await api("/api/queue");
    const pend = Object.values(q.pending).reduce((a, b) => a + b, 0);
    const w = q.worker;
    let s = w.state === "loading" ? `loading ${w.backend}…` : w.state === "error" ? `⚠ ${w.error}` : w.label ? w.label : "model idle";
    if (q.running.length > 1) s = `${q.running.length} workers generating · ${s}`;
    else if (q.current) s = `generating ${q.current.chars} chars (${q.current.elapsed}s of ~${q.current.est_s}s) · ${s}`;
    if (pend) s += ` · ${pend} waiting`;
    for (const b of q.batches.filter(b => !BG(b) && !["play", "read-ahead"].includes(b.label))) s += ` · ${b.label}: ${b.done + b.cached}/${b.n}`;
    $("#qstat").textContent = s;
    renderJobs();
    if (view === "reader" && R.book) {
      await refreshReady();
      if (R.playing && R.waitingFor && R.ready.has(R.waitingFor)) playChunk(R.waitingFor);
      else if (R.playing && R.waitingFor && q.current) {
        const c = q.running.find(c => c.id === R.waitingFor && c.book === R.book.id);
        $("#playstat").textContent = c ? `generating this sentence… ${Math.round(c.elapsed)} s of ~${Math.round(c.est_s)} s` : "generating… (waiting for a worker)";
      }
    }
    if (view === "voices" && making.size) await refreshSamples();
    if (view === "gpu") await loadGpu();
  } catch (e) { $("#qstat").textContent = "server not reachable"; }
  setTimeout(poll, 1500);
}

// ---------------- background jobs ("Make audio ahead…"): progress, time left, pause, cancel ----------------
let lastQ = null, jobsOpen = false;
const BG = b => b.priority >= 2;   // what Pause holds; play, read-ahead and samples always go
const clock = s => new Date(Date.now() + s * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});

function renderJobs() {
  const q = lastQ, bs = q ? q.batches.filter(BG) : [];
  $("#jobs").hidden = !bs.length;
  if (!bs.length) { jobsOpen = false; return; }
  const n = bs.reduce((a, b) => a + b.n, 0), done = bs.reduce((a, b) => a + b.done + b.cached + b.failed, 0);
  const left = bs.reduce((a, b) => a + b.left_s, 0);
  const finishing = q.paused && q.current?.priority >= 2;
  $("#jobs").classList.toggle("paused", q.paused);
  $("#jobstxt").textContent = `${q.paused ? (finishing ? "Pausing after this sentence" : "Paused") : "Making audio"} · ${bs.length > 1 ? `${bs.length} jobs` : bs[0].label} · ${done}/${n} · `
    + (q.paused ? `${fmtS(left)} of work left` : `~${fmtS(left)} left`);
  $("#jobsbar").style.width = `${(100 * done / n).toFixed(1)}%`;
  $("#pause").textContent = q.paused ? "▶ Resume" : "❚❚ Pause";
  $("#jobpanel").hidden = !jobsOpen;
  if (!jobsOpen) return;
  $("#jobpanel").innerHTML = `<p class="muted" style="margin:0 0 4px">${q.paused ? "Paused: nothing is made in the background until you resume. ▶ Play still makes the sentences it reads."
      : `All done around <b>${clock(left)}</b>, at this voice's measured speed. Pause any time; jobs keep their place.`}</p>`
    + bs.map(b => `<div class="job"><div><b>${esc(b.title)}</b> · ${esc(b.label)}</div>
      <div class="muted">${b.done + b.cached}/${b.n} sentences · ${esc(b.preset_name)} · ~${fmtS(b.left_s)} left</div>
      <i class="bar"><b style="width:${(100 * (b.done + b.cached + b.failed) / b.n).toFixed(1)}%"></b></i>
      <button data-cancel="${b.id}">Cancel</button></div>`).join("");
  document.querySelectorAll("[data-cancel]").forEach(btn => btn.onclick = async () => {
    await api("/api/cancel-batch", {id: btn.dataset.cancel});
    lastQ.batches = lastQ.batches.filter(b => b.id !== btn.dataset.cancel); renderJobs();
  });
}
$("#jobsbtn").onclick = () => { jobsOpen = !jobsOpen; renderJobs(); };
$("#pause").onclick = async () => {
  await api("/api/pause", {paused: !lastQ.paused});
  lastQ.paused = !lastQ.paused; renderJobs();
};
document.addEventListener("click", e => { if (jobsOpen && !e.target.closest("#jobs")) { jobsOpen = false; renderJobs(); } });

// ---------------- voices ----------------
function renderVoices() {
  $("#vlist").innerHTML = S.presets.map((p, i) => {
    const opt = S.backends[p.backend] || {fields: []};
    const task = p.voice.task;
    const fields = opt.fields.filter(f => !f.when || f.when === task).map(f => {
      const v = p.voice[f.key] ?? f.default;
      const inp = f.type === "select" ? `<select data-k="${f.key}">${f.choices.map(c => `<option ${c === v ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`
        : f.type === "textarea" ? `<textarea data-k="${f.key}">${esc(v)}</textarea>`
        : f.type === "bool" ? `<input type="checkbox" data-k="${f.key}" ${v ? "checked" : ""}>`
        : `<input type="${f.type === "number" ? "number" : "text"}" step="any" data-k="${f.key}" data-num="${f.type === "number"}" value="${esc(v)}">`;
      return `<label>${esc(f.label)}</label><div>${inp}</div>${f.hint ? `<div class="hint muted">${esc(f.hint)}</div>` : ""}`;
    }).join("");
    return `<div class="voice" data-i="${i}"><label>Name</label><div><input type="text" class="vname" value="${esc(p.name)}"></div>
      <label>Model</label><div><select class="vback">${Object.entries(S.backends).map(([k, o]) => `<option value="${k}" ${k === p.backend ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></div>
      ${fields}<div class="samples"><span class="muted">Hear it</span>${p.id && !S.dirty
        ? Object.entries(LANGS).map(([l, name]) => `<button class="vs" data-lang="${l}">▶ ${name}</button>`).join("")
        : `<button class="vs" data-lang="zh">Save and hear it</button>`}</div>
      <div class="row"><span class="muted">id ${p.id || "new"}</span><button class="vdel">Delete</button></div></div>`;
  }).join("");
  document.querySelectorAll(".voice").forEach(el => {
    const p = S.presets[+el.dataset.i];
    el.querySelector(".vname").oninput = e => p.name = e.target.value;
    el.querySelector(".vback").onchange = e => { p.backend = e.target.value; p.voice = defaults(p.backend); dirty(); };
    el.querySelectorAll("[data-k]").forEach(inp => inp.onchange = () => {
      const k = inp.dataset.k;
      p.voice[k] = inp.type === "checkbox" ? inp.checked : inp.dataset.num === "true" ? +inp.value : inp.value;
      dirty();
    });
    el.querySelector(".vdel").onclick = () => { S.presets.splice(+el.dataset.i, 1); dirty(); };
    el.querySelectorAll(".vs").forEach(b => b.onclick = async () => {
      if (S.dirty || !p.id) await saveVoices();
      sample(S.presets[+el.dataset.i].id, b.dataset.lang);
    });
  });
  paintSamples();
}
const dirty = () => { S.dirty = true; renderVoices(); };

// voice samples: a short line in Chinese and in English, made once per voice and kept
const LANGS = {zh: "中文", en: "English"};
let samples = {}, making = new Set(), wantPlay = null;
async function refreshSamples() {
  samples = await api("/api/samples");
  for (const k of [...making]) { const [pid, l] = k.split("|"); if (samples[pid]?.[l] || !samples[pid]) making.delete(k); }
  if (wantPlay && samples[wantPlay.pid]?.[wantPlay.lang]) { playSample(wantPlay.pid, wantPlay.lang); wantPlay = null; }
  paintSamples();
}
function paintSamples() {
  document.querySelectorAll(".voice").forEach(el => {
    const pid = S.presets[+el.dataset.i]?.id;
    el.querySelectorAll(".vs[data-lang]").forEach(b => {
      if (!pid || S.dirty || !LANGS[b.dataset.lang] || b.textContent.startsWith("Save")) return;
      const k = `${pid}|${b.dataset.lang}`, cur = lastQ?.running.find(c => c.book === "_samples" && c.preset === pid);
      b.classList.toggle("making", making.has(k));
      b.textContent = making.has(k) ? `making ${LANGS[b.dataset.lang]}…${cur?.book === "_samples" && cur.preset === pid ? ` ${Math.round(cur.elapsed)} s of ~${Math.round(cur.est_s)} s` : ""}`
        : `▶ ${LANGS[b.dataset.lang]}`;
    });
  });
}
async function sample(pid, lang, langs = [lang]) {
  if (samples[pid]?.[lang]) return playSample(pid, lang);
  await api("/api/sample", {preset: pid, langs});
  langs.forEach(l => making.add(`${pid}|${l}`));
  wantPlay = {pid, lang};
  paintSamples();
}
function playSample(pid, lang) {
  stop();
  audio.src = `/api/audio?book=_samples&preset=${pid}&id=${{zh: "0-0", en: "0-1"}[lang]}&v=${Date.now()}`;
  audio.play();
}
const defaults = b => Object.fromEntries(S.backends[b].fields.map(f => [f.key, f.default]));
$("#addvoice").onclick = () => { const b = Object.keys(S.backends)[0]; S.presets.push({name: "New voice", backend: b, voice: defaults(b)}); dirty(); };
async function saveVoices() {
  // keep only the fields that apply to the chosen task, so the voice id does not depend on hidden fields
  for (const p of S.presets) {
    const fs = S.backends[p.backend].fields.filter(f => !f.when || f.when === p.voice.task);
    p.voice = Object.fromEntries(fs.map(f => [f.key, p.voice[f.key] ?? f.default]));
  }
  const before = new Set(S.presets.map(p => p.id));
  S.presets = (await api("/api/presets", {presets: S.presets})).presets;
  S.dirty = false;
  fillPresets(); renderVoices();
  samples = await api("/api/samples");
  // a new voice (or new settings) says something right away: both samples are made, the Chinese one plays
  const fresh = S.presets.filter(p => !before.has(p.id) && !samples[p.id]?.zh);
  for (const p of fresh) await sample(p.id, "zh", Object.keys(LANGS));
  if (fresh.length > 1) wantPlay = {pid: fresh[0].id, lang: "zh"};
  paintSamples();
}
$("#savevoices").onclick = saveVoices;

function fillPresets() {
  const opts = S.presets.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  $("#preset").innerHTML = opts; $("#benchpreset").innerHTML = opts;
  const want = store.get("preset");
  if (S.presets.some(p => p.id === want)) $("#preset").value = want;
}

// ---------------- speed ----------------
async function loadBench() {
  const {runs, batches} = await api("/api/runs");
  lastRuns = runs;
  const groups = {};
  for (const r of runs) {
    const k = `${r.label}|${r.task || "-"}`;
    const g = groups[k] ??= {label: r.label, task: r.task, n: 0, chars: 0, audio: 0, gen: 0, parts: {}, first: r.ts, last: r.ts};
    g.n++; g.chars += r.chars; g.audio += r.audio_s; g.gen += r.total_s; g.last = r.ts;
    for (const [p, v] of Object.entries(r.parts_s || {})) g.parts[p] = (g.parts[p] || 0) + v;
  }
  $("#bylabel").innerHTML = table(["Model setup", "Task", "Chunks", "Chars", "Audio", "Generation", "RTF", "Chars / s", "Where the time goes", "Last"],
    Object.values(groups).map(g => [esc(g.label), esc(g.task), n(g.n), n(g.chars), n(fmtS(g.audio)), n(fmtS(g.gen)), n(`<b>${(g.gen / g.audio).toFixed(2)}</b>`),
      n((g.chars / g.gen).toFixed(1)), Object.entries(g.parts).sort((a, b) => b[1] - a[1]).map(([p, v]) => `${p} ${Math.round(100 * v / g.gen)}%`).join(" · ") || "–", day(g.last)]));
  const bench = batches.filter(b => b.book === "_bench").reverse();
  $("#benchruns").innerHTML = bench.length ? table(["When", "Voice", "Model setup", "Audio", "Wall time", "RTF", ""],
    bench.map(b => [day(b.finished), esc(b.preset_name), esc(labelOf(runs, b)), n(fmtS(b.audio_s)), n(fmtS(b.wall_s)), n(`<b>${b.rtf}</b>`),
      `<button data-play="_bench|${b.preset}|${b.first}">▶</button>`])) : `<p class="muted">Not run yet.</p>`;
  const jobs = batches.filter(b => b.book !== "_bench" && !["play", "read-ahead"].includes(b.label)).reverse();
  $("#batches").innerHTML = jobs.length ? table(["When", "Book", "What", "Voice", "Chunks", "Audio", "Wall time", "RTF", ""],
    jobs.map(b => [day(b.finished), esc(b.title), esc(b.label), esc(b.preset_name), n(`${b.done}${b.cached ? ` (+${b.cached} cached)` : ""}`), n(fmtS(b.audio_s)),
      n(fmtS(b.wall_s)), n(b.rtf ?? "–"), `<button data-play="${b.book}|${b.preset}|${b.first}">▶</button>`])) : `<p class="muted">Preprocess a page in the reader to see it here.</p>`;
  document.querySelectorAll("[data-play]").forEach(btn => btn.onclick = () => {
    const [book, preset, id] = btn.dataset.play.split("|");
    audio.src = `/api/audio?book=${book}&preset=${preset}&id=${id}`; audio.play();
  });
}
const n = v => ({n: v});
const labelOf = (runs, b) => runs.find(r => (r.batches || []).includes(b.id))?.label || b.backend;
const table = (head, rows) => `<table><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr>${rows.map(r => `<tr>${r.map(c =>
  c && c.n !== undefined ? `<td class="n">${c.n}</td>` : `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
$("#runbench").onclick = async () => {
  await api("/api/bench", {preset: $("#benchpreset").value, fresh: $("#benchfresh").checked});
  $("#runbench").textContent = "Queued — see the status at the top right";
  setTimeout(() => $("#runbench").textContent = "Run benchmark passage", 4000);
};

// ---------------- storage ----------------
async function loadStorage() {
  const s = await api("/api/storage");
  $("#stotal").textContent = `${fmtB(s.total)} of audio · ${fmtB(s.free)} free on disk · ${s.data_dir}`;
  $("#srows").innerHTML = s.rows.length ? table(["Book", "Voice", "WAV", "M4A (compressed)", ""], s.rows.map(r => [
    esc(r.title), `${esc(r.preset_name)} <span class="muted">${r.preset}</span>`,
    n(r.wav_n ? `${r.wav_n} files · ${fmtB(r.wav_bytes)}` : "–"), n(r.m4a_n ? `${r.m4a_n} files · ${fmtB(r.m4a_bytes)}` : "–"),
    `${r.wav_n ? `<button data-c="${r.book}|${r.preset}">Compress to AAC</button> ` : ""}<button data-d="${r.book}|${r.preset}">Delete</button>`])) : `<p class="muted">No audio yet.</p>`;
  document.querySelectorAll("[data-c]").forEach(b => b.onclick = async () => {
    const [book, preset] = b.dataset.c.split("|"); b.textContent = "Compressing…"; await api("/api/compress", {book, preset}); loadStorage();
  });
  document.querySelectorAll("[data-d]").forEach(b => b.onclick = async () => {
    const [book, preset] = b.dataset.d.split("|");
    if (confirm("Delete this audio? It can be generated again.")) { await api("/api/delete", {book, preset}); loadStorage(); }
  });
}

// ---------------- GPU: memory, load, how many workers ----------------
const gpuHist = [];
const GB = b => `${(b / 1e9).toFixed(1)} GB`;
// measured on an M4 24GB, 2026-10-30 (4 chunks per worker, warm): shown until this Mac has its own numbers
const M4 = {1: 2.21, 3: 1.07, 4: 1.03, 6: 2.01};

async function loadGpu() {
  const preset = presetId();
  const s = await api(`/api/system${preset ? `?preset=${preset}` : ""}`);
  gpuHist.push(s.gpu.util ?? 0); if (gpuHist.length > 80) gpuHist.shift();
  $("#gutil").textContent = s.gpu.util == null ? "–" : `${s.gpu.util}%`;
  const W = 240, H = 44, pts = gpuHist.map((v, i) => `${(i * W / 79).toFixed(1)},${(H - 2 - v / 100 * (H - 4)).toFixed(1)}`);
  $("#gspark").innerHTML = `<line x1="0" y1="${H - 2}" x2="${W}" y2="${H - 2}" stroke="var(--line)" stroke-width="1"/>`
    + `<polyline points="${pts.join(" ")}" fill="none" stroke="#b88a2e" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  $("#gspark").onmousemove = e => {
    const r = e.currentTarget.getBoundingClientRect(), i = Math.round((e.clientX - r.left) / r.width * 79);
    const v = gpuHist[Math.min(gpuHist.length - 1, Math.max(0, i))];
    $("#gsparkhint").textContent = v == null ? "last 2 minutes" : `${v}% busy, ${Math.round((gpuHist.length - 1 - i) * 1.5)} s ago`;
  };
  $("#gspark").onmouseleave = () => $("#gsparkhint").textContent = "last 2 minutes";

  const other = Math.max(0, s.ram - s.available - s.ours);
  $("#gfree").textContent = GB(s.available);
  $("#gfreesub").textContent = `of ${GB(s.ram)} · workers use ${GB(s.ours)}, other apps ${GB(other)}`;
  const live = s.workers.filter(w => w.pid);
  $("#gwork").textContent = `${live.length} of ${s.n}`;
  $("#gworksub").textContent = live.length < s.n ? "loaded · more start when there is work" : `loaded · ${s.workers.filter(w => w.busy).length} busy now`;
  const ch = R.book ? R.book.chapters[R.ch].paras.reduce((m, p) => m + p.chunks.reduce((k, x) => k + x.t.length, 0), 0) : 0;
  if (ch && s.s_per_char) {
    $("#gch").textContent = `~${fmtS(ch * s.s_per_char[s.n])}`;
    $("#gchsub").textContent = `this chapter (${ch} chars) with ${s.n} worker${s.n > 1 ? "s" : ""}; with 1: ~${fmtS(ch * s.s_per_char[1])}`;
  } else { $("#gch").textContent = "–"; $("#gchsub").textContent = "open a book in the Reader"; }

  // memory bar: each worker, other apps, free (the track); dashed line = Metal's GPU limit
  const pct = b => `${(100 * b / s.ram).toFixed(2)}%`;
  $("#membar").innerHTML = live.map(w => `<div class="seg w" style="width:${pct(w.bytes)}" title="worker ${w.slot + 1}: ${GB(w.bytes)}${w.busy ? " · generating" : " · idle"}"></div>`).join("")
    + `<div class="seg o" style="width:${pct(other)}" title="other apps and macOS: ${GB(other)}"></div>`
    + `<div class="lim" style="left:${pct(s.gpu.budget)}"><span>GPU limit ${GB(s.gpu.budget)}</span></div>`;
  $("#memleg").innerHTML = `<span><i style="background:#b88a2e"></i>model workers ${GB(s.ours)}</span><span><i style="background:#4f7cc0"></i>other apps and macOS ${GB(other)}</span>`
    + `<span><i style="background:var(--line)"></i>free ${GB(s.available)}</span><span>one worker ≈ ${GB(s.per_worker)}</span>`;

  // picker: 1..max that fits (+1 shown as over), with the speed each would give
  const top = Math.min(6, Math.max(s.max + 1, 4));
  const speed = n => { const m = s.by_n[n], one = s.by_n[1]; return m && one ? (one.gen_s / one.audio_s) / (m.gen_s / m.audio_s / n) : M4[n] ? M4[1] / M4[n] : null; };
  $("#wpick").innerHTML = Array.from({length: top}, (_, i) => i + 1).map(n => {
    const sp = speed(n);
    return `<button data-n="${n}" class="${n === s.n ? "on" : ""} ${n > s.max ? "over" : ""}">${n}${n === s.recommended ? " ★" : ""}
      <small>${n > s.max ? "won't fit" : sp ? `${sp.toFixed(1)}× speed` : s.s_per_char ? `~${(s.s_per_char[1] / s.s_per_char[n]).toFixed(1)}× speed` : ""} · ${GB(n * s.per_worker)}</small></button>`;
  }).join("");
  document.querySelectorAll("#wpick button").forEach(b => b.onclick = async () => {
    const n = +b.dataset.n;
    if (n > s.max && !confirm(`${n} workers need about ${GB(n * s.per_worker)}; only ${GB(s.available + s.ours)} is free for them. The Mac will swap and everything gets slower. Use ${n} anyway?`)) return;
    await api("/api/workers", {n}); loadGpu();
  });
  $("#wnote").innerHTML = `★ ${s.recommended} is recommended here: ${Math.min(s.fit_mem, 8)} fit in free memory, ${s.fit_gpu} in the GPU limit,
    and past 3 the GPU itself is full (on an M4, 4 workers were no faster than 3, and 6 were slower than 2 because the Mac swapped).
    Extra workers start when there is work and quit after 90 s idle. ▶ Play always goes first.`;

  const rows = Object.keys({...M4, ...s.by_n}).map(Number).sort((a, b) => a - b).map(k => {
    const m = s.by_n[k];
    return m ? [k, n(m.chunks), n(`${(m.gen_s / m.chunks).toFixed(1)} s`), n((m.gen_s / m.audio_s).toFixed(2)), n(`<b>${(m.gen_s / m.audio_s / k).toFixed(2)}</b>`), "this Mac"]
             : [k, n("–"), n("–"), n("–"), n(`<b>${M4[k].toFixed(2)}</b>`), "M4 24GB test"];
  });
  $("#wspeed").innerHTML = table(["Workers", "Chunks", "Time per chunk", "RTF per chunk", "RTF together", "Measured on"], rows)
    + `<p class="muted">RTF together = generation time ÷ audio made, all workers combined; below 1 is faster than listening.</p>`;
}

// ---------------- start ----------------
(async () => {
  S = await api("/api/state");
  fillPresets();
  api("/api/runs").then(r => lastRuns = r.runs);
  loadLibrary();
  poll();
})();
