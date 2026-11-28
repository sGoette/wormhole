// Records tab: the record library. Tapping a record makes it "now playing" (title, artist and
// cover on the speakers and the Apple TV); tapping it again goes back to "Turntable".
// Adding searches a cover source (MusicBrainz, Apple Music, ...) on the server.

import {$, call, flash} from "./api.js";

const COVER_MAX = 700000;  // bytes; what the server takes (records.py / pipemeta.py)
const COVER_PX = 1200;

let refresh = () => {};
let records = [], current = null, loaded = false;
let editing = null;  // the record open in the edit dialog

export function onChange(fn) { refresh = fn; }

function note(el, text, cls) {
  el.textContent = text || "";
  el.className = "hint" + (cls ? ` ${cls}` : "");
  el.hidden = !text;
}

function showCover(img, placeholder, url) {
  img.hidden = !url;
  placeholder.hidden = !!url;
  if (url) img.src = url; else img.removeAttribute("src");
}

// -- the grid
function tile(r) {
  const el = $("record-tile").content.firstElementChild.cloneNode(true);
  const q = sel => el.querySelector(sel);
  showCover(q(".rec-art img"), q(".rec-art .no-cover"), r.cover_url);
  q(".rec-title").textContent = r.title;
  q(".rec-artist").textContent = r.artist;
  const pick = q(".rec-pick");
  pick.setAttribute("aria-label", `${r.title}${r.artist ? ` by ${r.artist}` : ""}`);
  pick.onclick = () => choose(r.id === current ? null : r.id);
  q(".rec-edit").setAttribute("aria-label", `Edit ${r.title}`);
  q(".rec-edit").onclick = () => openEdit(r);
  el.dataset.id = r.id;
  return el;
}

function renderGrid() {
  const grid = $("records");
  grid.replaceChildren();
  if (!records.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No records yet. Add the ones you play, then tap the one on the "
      + "turntable: its cover shows on the Apple TV.";
    grid.append(p);
  }
  for (const r of records) grid.append(tile(r));
  $("records-count").textContent = records.length
    ? `${records.length} record${records.length === 1 ? "" : "s"} · tap the one playing`
    : "";
  markCurrent();
}

function markCurrent() {
  for (const el of $("records").querySelectorAll(".rec")) {
    const on = Number(el.dataset.id) === current;
    el.classList.toggle("current", on);
    el.querySelector(".rec-badge").hidden = !on;
    el.querySelector(".rec-pick").setAttribute("aria-pressed", String(on));
  }
}

export async function load() {
  try {
    const data = await call("GET", "/api/records");
    records = data.records;
    current = data.current;
    loaded = true;
    fillSources(data.sources);
    renderGrid();
  } catch (e) {
    $("records").replaceChildren();
    flash(e.message, true);
  }
}

// From /api/state (polled): another phone may have chosen a record.
export function render(s) {
  const id = s.now_playing ? s.now_playing.id : null;
  if (id === current) return;
  current = id;
  if (loaded) markCurrent();
}

async function choose(id) {
  try {
    const data = await call("PUT", "/api/now-playing", {record: id});
    current = data.now_playing ? data.now_playing.id : null;
    markCurrent();
    flash("");
  } catch (e) { flash(e.message, true); }
  refresh();
}

// -- adding: search a cover source
function fillSources(sources) {
  const sel = $("s-source");
  if (sel.options.length) return;
  for (const s of sources) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.label;
    sel.append(opt);
  }
  sel.hidden = sources.length < 2;  // nothing to choose
}

function resultRow(a) {
  const el = $("result-row").content.firstElementChild.cloneNode(true);
  const q = sel => el.querySelector(sel);
  const img = q("img");
  img.onerror = () => { img.hidden = true; q(".result-art").classList.add("no-cover"); };
  if (a.thumb) img.src = a.thumb; else img.onerror();
  q(".result-title").textContent = a.title;
  q(".result-meta").textContent = [a.artist, a.year, a.kind].filter(Boolean).join(" · ");
  const btn = q(".result-add");
  btn.setAttribute("aria-label", `Add ${a.title}`);
  if (a.in_library) { btn.textContent = "Added"; btn.disabled = true; }
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      const r = await call("POST", "/api/records", {source: a.source, ref: a.ref,
                                                    title: a.title, artist: a.artist,
                                                    year: a.year});
      btn.textContent = "Added";
      note($("s-msg"), r.warning || `Added ${r.title}.`, r.warning ? "err" : "good");
      load();
    } catch (e) {
      btn.textContent = "Add";
      btn.disabled = false;
      note($("s-msg"), e.message, "err");
    }
  };
  return el;
}

async function search(event) {
  event.preventDefault();
  const album = $("s-album").value.trim(), artist = $("s-artist").value.trim();
  if (!album && !artist) { note($("s-msg"), "Enter an album or an artist.", "err"); return; }
  const query = new URLSearchParams({album, artist, source: $("s-source").value});
  $("s-go").disabled = true;
  note($("s-msg"), "Searching…");
  $("results").replaceChildren();
  try {
    const data = await call("GET", "/api/records/search?" + query.toString());
    for (const a of data.results) $("results").append(resultRow(a));
    note($("s-msg"), data.results.length ? "" : "Nothing found. Try fewer words, or the "
         + "other source.");
  } catch (e) { note($("s-msg"), e.message, "err"); }
  $("s-go").disabled = false;
  document.activeElement.blur();  // close the iPhone keyboard so the results show
}

async function addManually() {
  const title = $("s-album").value.trim();
  if (!title) { note($("s-msg"), "Enter the album first.", "err"); $("s-album").focus(); return; }
  try {
    const r = await call("POST", "/api/records", {title, artist: $("s-artist").value.trim()});
    $("add-dialog").close();
    await load();
    openEdit(r);
    note($("e-msg"), "Added. Now take or choose a photo of the sleeve.", "good");
  } catch (e) { note($("s-msg"), e.message, "err"); }
}

// -- editing
function openEdit(r) {
  editing = r;
  showCover($("e-cover"), $("e-nocover"), r.cover_url);
  $("e-title").value = r.title;
  $("e-artist").value = r.artist;
  $("e-year").value = r.year;
  note($("e-msg"), "");
  $("edit-dialog").showModal();
}

async function saveEdit(event) {
  event.preventDefault();
  try {
    editing = await call("PUT", `/api/records/${editing.id}`, {
      title: $("e-title").value, artist: $("e-artist").value, year: $("e-year").value});
    note($("e-msg"), "Saved.", "good");
    load();
    refresh();
  } catch (e) { note($("e-msg"), e.message, "err"); }
}

// A phone photo is several MB: cut to a square, scale to 1200 px and send as JPEG.
async function coverFrom(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const size = Math.min(COVER_PX, side);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    canvas.getContext("2d").drawImage(img, (img.naturalWidth - side) / 2,
                                      (img.naturalHeight - side) / 2, side, side,
                                      0, 0, size, size);
    for (const quality of [0.88, 0.8, 0.7, 0.6, 0.5]) {
      const blob = await new Promise(done => canvas.toBlob(done, "image/jpeg", quality));
      if (blob && blob.size <= COVER_MAX) return blob;
    }
    throw new Error("The photo couldn't be made small enough.");
  } catch (e) {
    throw e.message ? e : new Error("This picture can't be read.");
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function upload() {
  const file = $("e-photo").files[0];
  $("e-photo").value = "";
  if (!file) return;
  note($("e-msg"), "Uploading…");
  try {
    editing = await call("PUT", `/api/records/${editing.id}/cover`, await coverFrom(file));
    showCover($("e-cover"), $("e-nocover"), editing.cover_url);
    note($("e-msg"), "New cover saved.", "good");
    load();
    refresh();
  } catch (e) { note($("e-msg"), e.message, "err"); }
}

async function remove() {
  if (!confirm(`Remove "${editing.title}" from the library?`)) return;
  try {
    await call("DELETE", `/api/records/${editing.id}`);
    $("edit-dialog").close();
    load();
    refresh();
  } catch (e) { note($("e-msg"), e.message, "err"); }
}

export function init() {
  $("add-record").onclick = () => {
    note($("s-msg"), "");
    $("add-dialog").showModal();
    $("s-artist").focus();
  };
  $("close-add").onclick = () => $("add-dialog").close();
  $("search-form").onsubmit = search;
  $("add-manual").onclick = addManually;
  $("close-edit").onclick = () => $("edit-dialog").close();
  $("edit-form").onsubmit = saveEdit;
  $("e-photo").onchange = upload;
  $("e-delete").onclick = remove;
}
