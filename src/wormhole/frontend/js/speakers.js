// Speaker cards: on/off, volume per speaker, PIN pairing (Apple TV).

import {$, call, flash} from "./api.js";

const cards = {};
let refresh = () => {};

export function onChange(fn) { refresh = fn; }

function askPin(c, message) {
  c.asking = true;
  c.pin.hidden = false;
  c.note.textContent = message;
  c.note.className = "note ask";
  c.pinInput.value = "";
  c.pairBtn.disabled = true;
  c.pinInput.focus();
  flash("");
}

function showVolume(c, value) {
  c.range.value = value;
  c.vol.textContent = value;
  c.range.style.setProperty("--fill", `${value}%`);  // filled part of the track (WebKit)
}

async function sendVolume(c, id) {
  try { await call("PUT", `/api/outputs/${id}`, {volume: Number(c.range.value)}); flash(""); }
  catch (e) { flash(e.message, true); }
  refresh();
}

function makeCard(o) {
  const el = $("speaker-card").content.firstElementChild.cloneNode(true);
  const q = sel => el.querySelector(sel);
  const c = {el, box: q(".switch input"), range: q("input[type=range]"),
             name: q(".name"), meta: q(".meta"), vol: q(".vol"), pin: q(".pin"),
             note: q(".pin .note"), pinForm: q(".pin form"), pinInput: q(".pin input"),
             pairBtn: q(".pin button"), asking: false, dragging: false, timer: null};
  c.box.setAttribute("aria-label", `${o.name} on`);
  c.range.setAttribute("aria-label", `${o.name} volume`);

  // Switching on a PIN device makes it show a PIN: ask for it instead of showing an error.
  c.box.onchange = async () => {
    try { await call("PUT", `/api/outputs/${o.id}`, {selected: c.box.checked}); flash(""); }
    catch (e) {
      if (e.needsPin) askPin(c, e.message); else flash(e.message, true);
    }
    refresh();
  };

  c.pinInput.oninput = () => { c.pairBtn.disabled = !c.pinInput.value.trim(); };
  // The PIN goes only to the pair endpoint (sends it alone, then switches on: OwnTone #1967).
  c.pinForm.onsubmit = async event => {
    event.preventDefault();
    if (!c.pinInput.value.trim()) return;
    c.pairBtn.disabled = true;
    c.note.textContent = "Pairing…";
    c.note.className = "note";
    try {
      await call("POST", `/api/outputs/${o.id}/pair`, {pin: c.pinInput.value});
      c.asking = false;
      c.note.textContent = "Paired and switched on.";
      c.pinInput.value = "";
      flash("");
    } catch (e) {
      if (e.needsPin) askPin(c, e.message);
      else { c.note.textContent = e.message; c.note.className = "note err"; }
    }
    c.pairBtn.disabled = !c.pinInput.value.trim();
    refresh();
  };

  // Sent while dragging (debounced) and once more on release.
  c.range.oninput = () => {
    c.dragging = true;
    showVolume(c, c.range.value);
    clearTimeout(c.timer);
    c.timer = setTimeout(() => sendVolume(c, o.id), 200);
  };
  c.range.onchange = () => {
    clearTimeout(c.timer);
    sendVolume(c, o.id);
    c.dragging = false;
  };
  return c;
}

// `outputs`: the visible speakers; `total` includes hidden ones (for the empty message).
export function render(outputs, total) {
  const list = $("speakers"), seen = new Set();
  if (!outputs.length) {
    list.innerHTML = total
      ? "<p class='empty'>All speakers are hidden. Show them again in the settings.</p>"
      : "<p class='empty'>No speakers found. They appear here as soon as "
        + "OwnTone sees them on the network.</p>";
    for (const id of Object.keys(cards)) delete cards[id];
    return;
  }
  if (!Object.keys(cards).length) list.innerHTML = "";
  outputs.forEach((o, i) => {
    seen.add(o.id);
    const c = cards[o.id] || (cards[o.id] = makeCard(o));
    // Move a card only when it is out of place: moving a node in the DOM drops the focus of
    // any input inside it (e.g. the PIN field being typed into).
    if (list.children[i] !== c.el) list.insertBefore(c.el, list.children[i] || null);
    c.el.classList.toggle("off", !o.selected);
    c.box.checked = o.selected;
    c.name.textContent = o.name;
    const needsPin = o.needs_auth_key || o.requires_auth;
    c.meta.textContent = [o.type].concat(needsPin ? ["needs PIN"] : []).join(" · ");
    c.pin.hidden = !(needsPin || c.asking);
    if (!c.dragging) showVolume(c, o.volume);
  });
  for (const id of Object.keys(cards)) {
    if (!seen.has(id)) { cards[id].el.remove(); delete cards[id]; }
  }
}
