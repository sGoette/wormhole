// Calls to wormhole's JSON API (src/wormhole/web.py) and the message line under the bar.

export const $ = id => document.getElementById(id);

// Errors come back as {ok: false, error, needs_pin?}; they are thrown with `needsPin` set.
export async function call(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.needsPin = !!data.needs_pin;
    throw err;
  }
  return data;
}

export const get = path => call("GET", path);

export function flash(text, isErr) {
  const msg = $("msg");
  msg.textContent = text || "";
  msg.className = "banner" + (isErr ? " err" : "");
  msg.hidden = !text;
}
