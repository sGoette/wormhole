// Entry point: polls /api/state while the page is visible and hands it to the parts.
// The speaker list updates itself this way, so there is no refresh button.

import {$, get} from "./api.js";
import * as settings from "./settings.js";
import * as software from "./software.js";
import * as speakers from "./speakers.js";

const POLL_MS = 1500;
let busy = false, timer = null;

function renderNow(s) {
  const r = s.renderer, now = $("now");
  const playing = r && r.state === "PLAYING";
  now.hidden = !playing;
  now.textContent = playing ? "Playing" : "";
}

async function poll() {
  if (busy) return;
  busy = true;
  try {
    const s = await get("/api/state");
    $("conn").hidden = !s.error;
    $("conn").textContent = s.error ? `OwnTone: ${s.error}` : "";
    if (!s.error) speakers.render(s.outputs.filter(o => !o.hidden), s.outputs.length);
    renderNow(s);
    settings.render(s);
  } catch (e) {
    $("conn").hidden = false;
    $("conn").textContent = "Wormhole is not reachable. Is the server running?";
  } finally {
    busy = false;
  }
}

// No polling in the background (Home Screen app, locked phone); catch up when shown again.
function schedule() {
  clearInterval(timer);
  timer = null;
  if (document.visibilityState === "visible") {
    poll();
    timer = setInterval(poll, POLL_MS);
  }
}

speakers.onChange(poll);
settings.onChange(poll);
settings.init(() => { poll(); software.refresh(); });
software.init();
document.addEventListener("visibilitychange", schedule);
schedule();
