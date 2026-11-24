// Player bar: what plays (source name, "Lossless" badge), play/pause and master volume.

import {$, call, flash} from "./api.js";

let refresh = () => {};
let dragging = false, timer = null, playing = false;

export function onChange(fn) { refresh = fn; }

function showVolume(value) {
  const range = $("pl-volume");
  range.value = value;
  $("pl-vol").textContent = value;
  range.style.setProperty("--fill", `${value}%`);  // filled part of the track (WebKit)
}

async function sendVolume() {
  try {
    await call("PUT", "/api/player/volume", {volume: Number($("pl-volume").value)});
    flash("");
  } catch (e) { flash(e.message, true); }
  refresh();
}

async function send(path, btn) {
  btn.disabled = true;
  try {
    await call("POST", path);
    flash("");
  } catch (e) { flash(e.message, true); }
  refresh();
}

export function init() {
  const range = $("pl-volume");
  // Sent while dragging (debounced) and once more on release, like the speaker sliders.
  range.oninput = () => {
    dragging = true;
    showVolume(range.value);
    clearTimeout(timer);
    timer = setTimeout(sendVolume, 200);
  };
  range.onchange = () => {
    clearTimeout(timer);
    sendVolume();
    dragging = false;
  };
  $("pl-toggle").onclick = () => send(playing ? "/api/player/pause" : "/api/player/play",
                                      $("pl-toggle"));
  // The turntable keeps streaming after the record ends (until its sleep timer): Stop lets
  // the speakers and the Apple TV go. Play, or a new start on the turntable, resumes.
  $("pl-stop").onclick = () => send("/api/player/stop", $("pl-stop"));
}

// `s` is /api/state: renderer (the turntable's stream) and player (OwnTone).
export function render(s) {
  const r = s.renderer || {}, p = s.player || {};
  const streaming = r.state === "PLAYING";
  const state = p.state || "stop";
  playing = state === "play";

  const stopped = !streaming && state === "stop";
  const resumable = !!r.uri;  // the turntable sent a stream once: Play reconnects to it
  document.querySelector(".player").classList.toggle("idle", stopped);
  $("pl-source").textContent = streaming || (stopped && resumable) ? r.source || "Turntable"
    : state !== "stop" ? "OwnTone" : "Nothing playing";
  // From what ffmpeg actually decodes, not from what the turntable announces.
  $("pl-lossless").hidden = !(streaming && s.input && s.input.lossless);
  $("pl-state").textContent = stopped
    ? resumable ? "Stopped" : "Start a record in the turntable app"
    : playing ? "Playing" : state === "pause" ? "Paused" : "Starting…";

  const btn = $("pl-toggle");
  btn.classList.toggle("playing", playing);
  btn.setAttribute("aria-label", playing ? "Pause" : "Play");
  btn.disabled = stopped && !resumable;
  $("pl-stop").disabled = stopped;

  $("pl-volume").disabled = !!s.error;
  if (!dragging && typeof p.volume === "number") showVolume(p.volume);
}
