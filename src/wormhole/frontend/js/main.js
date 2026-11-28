// Entry point: polls /api/state while the page is visible and hands it to the parts.
// The speaker list updates itself this way, so there is no refresh button.

import {$, get} from "./api.js";
import * as player from "./player.js";
import * as records from "./records.js";
import * as settings from "./settings.js";
import * as software from "./software.js";
import * as speakers from "./speakers.js";
import * as tabs from "./tabs.js";

const POLL_MS = 1500;
let busy = false, timer = null;

async function poll() {
  if (busy) return;
  busy = true;
  try {
    const s = await get("/api/state");
    $("conn").hidden = !s.error;
    $("conn").textContent = s.error ? `OwnTone: ${s.error}` : "";
    if (!s.error) speakers.render(s.outputs.filter(o => !o.hidden), s.outputs.length);
    player.render(s);
    records.render(s);
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
player.onChange(poll);
player.init();
records.onChange(poll);
records.init();
tabs.init(name => {
  if (name === "records") records.load();
  if (name === "settings") { settings.opened(); software.refresh(); }
});
settings.onChange(poll);
settings.init();
software.init();
document.addEventListener("visibilitychange", schedule);
schedule();
