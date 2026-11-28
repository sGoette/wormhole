// Settings tab: renderer name, renderer + OwnTone status; the Software part is software.js.

import {$, call, flash} from "./api.js";

const visRows = {};  // output id -> {el, box, name}
let saved = "";  // renderer name as the server has it
let editing = false;  // don't overwrite what the user is typing

function message(text, cls) {
  const el = $("renderer-msg");
  el.textContent = text || "";
  el.className = "hint" + (cls ? ` ${cls}` : "");
  el.hidden = !text;
}

function updateSaveButton() {
  const value = $("renderer-name").value.trim();
  $("renderer-save").disabled = !value || value === saved;
}

async function save(event) {
  event.preventDefault();
  const name = $("renderer-name").value.trim();
  $("renderer-save").disabled = true;
  message("Saving…");
  try {
    const r = await call("PUT", "/api/renderer", {name});
    saved = r.name;
    $("renderer-name").value = r.name;
    editing = false;
    $("renderer-name").blur();
    message(`Saved. The turntable app now shows "${r.name}".`, "good");
  } catch (e) {
    message(e.message, "err");
  }
  updateSaveButton();
}

let refresh = () => {};

export function onChange(fn) { refresh = fn; }

// The Settings tab was opened: start without an old message.
export function opened() { message(""); }

export function init() {
  $("renderer-form").onsubmit = save;
  $("renderer-name").oninput = () => { editing = true; message(""); updateSaveButton(); };
}

function makeVisRow(o) {
  const el = $("visibility-row").content.firstElementChild.cloneNode(true);
  const row = {el, box: el.querySelector("input"), name: el.querySelector(".vis-name")};
  row.box.setAttribute("aria-label", `Show ${o.name}`);
  row.box.onchange = async () => {
    try {
      await call("PUT", `/api/outputs/${o.id}/hidden`, {hidden: !row.box.checked});
      flash("");
    } catch (e) {
      flash(e.message, true);
    }
    refresh();
  };
  return row;
}

// Same keyed update as the speaker cards: rows stay put while polling.
function renderVisibility(outputs) {
  const list = $("visibility"), seen = new Set();
  if (!outputs.length) {
    list.innerHTML = "<p class='hint'>No speakers found.</p>";
    for (const id of Object.keys(visRows)) delete visRows[id];
    return;
  }
  if (!Object.keys(visRows).length) list.innerHTML = "";
  outputs.forEach((o, i) => {
    seen.add(o.id);
    const row = visRows[o.id] || (visRows[o.id] = makeVisRow(o));
    if (list.children[i] !== row.el) list.insertBefore(row.el, list.children[i] || null);
    row.name.textContent = o.name;
    row.box.checked = !o.hidden;
    row.el.classList.toggle("hidden-spk", o.hidden);
  });
  for (const id of Object.keys(visRows)) {
    if (!seen.has(id)) { visRows[id].el.remove(); delete visRows[id]; }
  }
}

export function render(s) {
  if (!s.error) renderVisibility(s.outputs);
  const r = s.renderer, p = s.player;
  if (r) saved = r.name;
  if (!editing) $("renderer-name").value = saved;
  updateSaveButton();

  const state = !r ? "off: enter a name to start it"
    : r.state === "PLAYING" ? `playing${r.controller ? ` (controlled from ${r.controller})` : ""}`
    : r.state === "PAUSED_PLAYBACK" ? "paused" : "idle";
  $("r-state").textContent = state;
  $("r-state").className = r && r.state === "PLAYING" ? "good" : "";
  $("p-state").textContent = s.error ? "not reachable"
    : p ? `${p.state}, master volume ${p.volume}` : "-";
  $("p-state").className = s.error ? "err" : p && p.state === "play" ? "good" : "";
  $("i-format").textContent = describeInput(s.input);
}

// "FLAC · 24 bit · 48 kHz · lossless", from the codec ffmpeg decodes (feed.py).
function describeInput(i) {
  if (!i) return "-";
  const codec = i.codec.startsWith("pcm_") ? "PCM" : i.codec.toUpperCase();
  return [codec, i.bits ? `${i.bits} bit` : "", `${i.rate / 1000} kHz`,
          i.lossless ? "lossless" : "lossy"].filter(Boolean).join(" · ");
}
