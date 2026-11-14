// Fake turntable web UI: pick a UPnP renderer and a source, Play / Pause / Stop.

const $ = id => document.getElementById(id);
let renderers = [], sourcesKey = "";

async function api(path, body) {
  const opts = body === undefined ? {} : {method: "POST", body: JSON.stringify(body)};
  const res = await fetch(path, opts);
  return res.json();
}

async function loadRenderers(refresh) {
  $("refresh").disabled = true;
  $("renderer").innerHTML = "<option value=''>Searching&hellip;</option>";
  try { renderers = await api("/api/renderers" + (refresh ? "?refresh=1" : "")); }
  finally { $("refresh").disabled = false; }
  const playable = renderers.filter(r => r.can_play);
  const sel = $("renderer"), prev = localStorage.getItem("renderer");
  sel.innerHTML = "";
  if (!playable.length) sel.innerHTML = "<option value=''>No UPnP renderer found</option>";
  for (const r of playable) {
    const o = new Option(`${r.name} (${r.host})` + (r.model ? ` - ${r.model}` : ""), r.key);
    o.selected = r.key === prev; sel.add(o);
  }
}

function fillSources(state) {
  const key = state.sources.join("|");
  if (key === sourcesKey) return;
  sourcesKey = key;
  const sel = $("source"), prev = localStorage.getItem("source") || state.source;
  sel.innerHTML = "";
  for (const s of state.sources)
    sel.add(new Option(s === "tone" ? "Test tone (440 Hz)" : s, s, false, s === prev));
  const fsel = $("format");
  if (!fsel.options.length) {
    for (const f of state.formats) fsel.add(new Option(f.toUpperCase(), f));
    fsel.value = localStorage.getItem("format") || state.format;
  }
}

function render(state) {
  fillSources(state);
  const t = state.transport, a = state.active;
  $("s-renderer").textContent = a ? `${a.name} (${a.host})` : "-";
  const st = $("s-state");
  if (t && t.error) { st.textContent = t.error; st.className = "state err"; }
  else if (t) {
    st.textContent = t.CurrentTransportState || "?";
    st.className = "state " + (t.CurrentTransportState || "");
  } else { st.textContent = "-"; st.className = "state"; }
  $("s-url").textContent = state.stream_url || "-";
  $("s-conns").textContent = state.connections.length
    ? state.connections.map(c => `#${c.id} ${c.client}: ${c.format}, ` +
        `${Math.round(Date.now() / 1000 - c.started)}s, ${(c.bytes / 1e6).toFixed(1)} MB`
      ).join("\n")
    : "no renderer connected to the stream";
  const ev = $("events");
  ev.innerHTML = "";
  for (const e of state.events.slice().reverse()) {
    const line = document.createElement("div");
    line.className = e.level; line.textContent = `${e.t}  ${e.msg}`; ev.appendChild(line);
  }
}

async function poll() {
  try { render(await api("/api/state")); } catch (e) { $("msg").textContent = "Server gone?"; }
}

async function action(name, body) {
  $("msg").textContent = "..."; $("msg").className = "";
  const res = await api("/api/" + name, body || {});
  $("msg").textContent = res.ok ? "" : res.error;
  $("msg").className = res.ok ? "" : "err";
  poll();
}

$("refresh").onclick = () => loadRenderers(true);
$("renderer").onchange = e => localStorage.setItem("renderer", e.target.value);
$("source").onchange = e => {
  localStorage.setItem("source", e.target.value);
  const ext = e.target.value.split(".").pop().toLowerCase();
  if (ext === "mp3" || ext === "flac") {
    $("format").value = ext; localStorage.setItem("format", ext);
  }
};
$("format").onchange = e => localStorage.setItem("format", e.target.value);
$("play").onclick = () => {
  if (!$("renderer").value) { $("msg").textContent = "Pick a speaker first"; return; }
  action("play", {renderer: $("renderer").value, source: $("source").value,
                  format: $("format").value});
};
$("pause").onclick = () => action("pause");
$("stop").onclick = () => action("stop");

loadRenderers(false); poll(); setInterval(poll, 2000);
