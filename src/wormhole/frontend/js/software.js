// Software section of the settings: version, updates from GitHub, roll back (update.py).

import {$, call, get} from "./api.js";

const BUTTONS = ["u-check", "u-apply", "u-rollback"];
const NOTE_KEY = "updateNote";  // survives the reload after an update
let running = null;  // commit currently running
let installCmd = "server/install.sh";

const day = iso => (iso ? iso.slice(0, 10) : "");

function setState(text, cls) {
  $("u-state").textContent = text;
  $("u-state").className = cls || "";
}

function renderVersion(u) {
  if (!u.available) {
    $("u-version").textContent = u.reason || "unknown";
    $("u-check").hidden = true;
    return;
  }
  running = u.commit;
  installCmd = `${u.repo}/server/install.sh`;
  $("u-version").textContent = `${u.short} (${day(u.date)}): ${u.subject}`;
  $("u-rollback").hidden = !u.previous;
  if (u.previous) $("u-rollback").textContent = `Roll back to ${u.previous.short}`;
}

function renderCheck(c) {
  const list = $("u-commits");
  list.innerHTML = "";
  for (const x of c.commits || []) {
    const li = document.createElement("li");
    li.textContent = `${x.short} ${day(x.date)} ${x.subject}`;
    list.appendChild(li);
  }
  if (c.blocked_reason) setState(`Can't update: ${c.blocked_reason}`, "err");
  else if (c.behind) setState(`${c.behind} new commit(s) on GitHub`, "good");
  else setState("Up to date");
  $("u-apply").hidden = !c.can_update;
  $("u-apply").textContent = `Update now (${c.behind})`;
  const note = $("u-note"), files = c.needs_install || [];
  note.hidden = !files.length;
  note.textContent = note.hidden ? "" : `This update changes server files (${files.join(", ")}).`
    + ` After updating, run in the VM console as root:\n${installCmd}`;
}

async function check() {
  $("u-check").disabled = true;
  setState("Checking GitHub…");
  try { renderCheck(await call("POST", "/api/update/check")); }
  catch (e) { setState(e.message, "err"); }
  $("u-check").disabled = false;
}

const sleep = ms => new Promise(res => setTimeout(res, ms));

async function run(kind) {
  for (const b of BUTTONS) $(b).disabled = true;
  const before = running;
  setState(kind === "apply" ? "Updating…" : "Rolling back…");
  try {
    const r = await call("POST", `/api/update/${kind}`);
    let text = `${r.from} → ${r.to}.`;
    if (r.needs_install && r.needs_install.length) {
      text += `\nServer files changed: run ${installCmd} as root in the VM.`;
    }
    setState(`${text} Restarting…`);
    try { sessionStorage.setItem(NOTE_KEY, text); } catch (e) { /* private mode */ }
    // Wait until the new process answers with the new commit, then load its page.
    for (let i = 0; i < 60; i++) {
      await sleep(1000);
      try {
        const u = await get("/api/update");
        if (u.commit && u.commit !== before) { location.reload(); return; }
      } catch (e) { /* restarting */ }
    }
    setState("wormhole did not come back within 60 s; check the VM.", "err");
  } catch (e) {
    setState(e.message, "err");
  }
  for (const b of BUTTONS) $(b).disabled = false;
}

export async function refresh() {
  try { renderVersion(await get("/api/update")); } catch (e) { /* offline */ }
}

export function init() {
  $("u-check").onclick = check;
  $("u-apply").onclick = () => run("apply");
  $("u-rollback").onclick = () => run("rollback");
  let note = null;
  try {
    note = sessionStorage.getItem(NOTE_KEY);
    sessionStorage.removeItem(NOTE_KEY);
  } catch (e) { /* private mode */ }
  if (note) {
    // Just updated: show the result where the update was started.
    setState(note, "good");
    $("settings").showModal();
  }
  refresh();
}
