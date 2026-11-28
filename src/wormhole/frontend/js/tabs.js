// Tab bar under the player: Speakers, Records, Settings. The choice survives a reload
// (#records in the address, and the browser's storage for the Home Screen app, which always
// opens at /).

const TABS = ["speakers", "records", "settings"];
const KEY = "wormhole.tab";
let onShow = () => {};

function remembered() {
  const hash = location.hash.slice(1);
  if (TABS.includes(hash)) return hash;
  try {
    const saved = localStorage.getItem(KEY);
    if (TABS.includes(saved)) return saved;
  } catch (e) { /* private mode, blocked storage: start at the speakers */ }
  return TABS[0];
}

export function show(name) {
  for (const tab of TABS) {
    const active = tab === name;
    document.getElementById(`tab-${tab}`).hidden = !active;
    const btn = document.getElementById(`tabbtn-${tab}`);
    btn.setAttribute("aria-selected", String(active));
    btn.classList.toggle("active", active);
  }
  history.replaceState(null, "", name === TABS[0] ? location.pathname : `#${name}`);
  try { localStorage.setItem(KEY, name); } catch (e) { /* see remembered() */ }
  onShow(name);
}

// `fn(name)` runs whenever a tab is shown (records and update status load when opened).
export function init(fn) {
  onShow = fn;
  for (const btn of document.querySelectorAll(".tab")) {
    btn.onclick = () => {
      show(btn.dataset.tab);
      window.scrollTo(0, 0);
    };
  }
  show(remembered());
}
