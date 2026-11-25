// @ts-check
/** Shared helpers: escaping, icons, thumbnails, toasts, overlays and theme. */

/** @param {any} s */
export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] || c);

/** @param {string} id @param {string} [cls] */
export const icon = (id, cls = "") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;

/** @param {string} sel @param {ParentNode} [root] @returns {HTMLElement} */
export const $ = (sel, root = document) => /** @type {HTMLElement} */ (root.querySelector(sel));

export function relTime(iso) {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `today, ${time}`;
  const y = new Date(today);
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "yesterday";
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}

const KPIS =
  '<rect class="tbg" x="0" y="0" width="160" height="90" rx="6"/><rect class="t3" x="8" y="8" width="44" height="16" rx="3"/>' +
  '<rect class="t3" x="58" y="8" width="44" height="16" rx="3"/><rect class="t3" x="108" y="8" width="44" height="16" rx="3"/>';
const THUMBS = {
  line: KPIS + '<polyline class="tl" points="10,78 26,70 42,72 58,60 74,63 90,50 106,53 122,42 138,40 150,32"/>',
  bars: KPIS + '<rect class="t1" x="12" y="40" width="16" height="42" rx="2"/><rect class="t1" x="36" y="50" width="16" height="32" rx="2"/>' +
    '<rect class="t2" x="60" y="58" width="16" height="24" rx="2"/><rect class="t2" x="84" y="64" width="16" height="18" rx="2"/>' +
    '<rect class="t3" x="108" y="70" width="16" height="12" rx="2"/><rect class="t3" x="132" y="74" width="16" height="8" rx="2"/>',
  table: '<rect class="tbg" x="0" y="0" width="160" height="90" rx="6"/><rect class="t2" x="8" y="8" width="144" height="10" rx="2"/>' +
    '<rect class="t3" x="8" y="24" width="144" height="8" rx="2"/><rect class="t3" x="8" y="38" width="144" height="8" rx="2"/>' +
    '<rect class="t3" x="8" y="52" width="144" height="8" rx="2"/><rect class="t3" x="8" y="66" width="100" height="8" rx="2"/>',
  empty: '<rect class="tbg" x="0" y="0" width="160" height="90" rx="6"/><rect class="t3" x="8" y="8" width="60" height="10" rx="2"/>',
};
/** @param {string} [kind] */
export const thumb = (kind) =>
  `<svg viewBox="0 0 160 90" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${THUMBS[kind || "empty"] || THUMBS.empty}</svg>`;

export const LINE_CHART =
  '<svg viewBox="0 0 240 110" preserveAspectRatio="none" aria-hidden="true"><path d="M0 96 L240 96" stroke="var(--line)" stroke-width="1" fill="none"/>' +
  '<path d="M0 84 L22 76 L44 78 L66 64 L88 68 L110 54 L132 58 L154 44 L176 46 L198 32 L220 34 L240 22 L240 96 L0 96 Z" fill="var(--accent-soft)"/>' +
  '<path d="M0 84 L22 76 L44 78 L66 64 L88 68 L110 54 L132 58 L154 44 L176 46 L198 32 L220 34 L240 22" fill="none" stroke="var(--viz-1)" stroke-width="2.5"/></svg>';
export const BAR_CHART =
  '<svg viewBox="0 0 120 110" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="10" width="112" height="16" rx="3" fill="var(--viz-1)"/>' +
  '<rect x="0" y="35" width="84" height="16" rx="3" fill="var(--viz-1)"/><rect x="0" y="60" width="58" height="16" rx="3" fill="var(--viz-2)"/>' +
  '<rect x="0" y="85" width="36" height="16" rx="3" fill="var(--viz-2)"/></svg>';

let toastTimer = 0;
/** @param {string} text @param {boolean} [isError] */
export function toast(text, isError = false) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.toggle("err", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (t.hidden = true), isError ? 5000 : 2500);
}

/** Overlays (dialog and sheets) share one scrim; only one is open at a time. */
const overlays = { setupDlg: null, pkgSheet: null, prefSheet: null };
/** @type {string|null} */
let openOverlay = null;
/** @param {string} id @param {() => void} onClose */
export function showOverlay(id, onClose) {
  if (openOverlay && openOverlay !== id) closeOverlay(true);
  Object.keys(overlays).forEach((k) => ($("#" + k).hidden = k !== id));
  overlays[id] = onClose;
  openOverlay = id;
  $("#scrim").hidden = false;
}
/** @param {boolean} [silent] */
export function closeOverlay(silent = false) {
  if (!openOverlay) return;
  const id = openOverlay;
  const cb = overlays[id];
  $("#" + id).hidden = true;
  $("#scrim").hidden = true;
  openOverlay = null;
  if (cb && !silent) cb();
}
export const overlayOpen = () => openOverlay;

/** Theme: "light" | "dark" | "system" */
const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
/** @param {string} pref */
export function applyTheme(pref) {
  const root = document.documentElement;
  if (pref === "light" || pref === "dark") root.setAttribute("data-theme", pref);
  else root.removeAttribute("data-theme");
  const dark = pref === "dark" || (pref !== "light" && !!mq?.matches);
  const btn = $("#themeBtn");
  btn.querySelector("use")?.setAttribute("href", dark ? "#i-sun" : "#i-moon");
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  try { localStorage.setItem("sh-theme", pref); } catch {}
}
export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || (mq?.matches ? "dark" : "light");
}

/** Simple LookML / YAML highlighting for the Files tab. @param {string} text */
export function highlight(text) {
  return text.split("\n").map((line) => {
    const e = esc(line);
    if (/^\s*#/.test(line)) return `<span class="c">${e}</span>`;
    return e
      .replace(/^(\s*-?\s*)([a-z_]+)(:)/, '$1<span class="k">$2</span>$3')
      .replace(/(&quot;[^&]*?&quot;|`[^`]*`)/g, '<span class="s">$1</span>');
  }).join("\n");
}

/** @param {string} diff */
export function highlightDiff(diff) {
  return diff.split("\n").map((l) => {
    const e = esc(l);
    if (l.startsWith("+") && !l.startsWith("+++")) return `<span class="add">${e}</span>`;
    if (l.startsWith("-") && !l.startsWith("---")) return `<span class="del">${e}</span>`;
    return e;
  }).join("\n");
}

/** Copy text, falling back to a message if the browser refuses. @param {string} text @param {HTMLElement} btn */
export async function copyText(text, btn) {
  const label = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied";
  } catch {
    btn.textContent = "Select and copy";
  }
  setTimeout(() => (btn.textContent = label), 1400);
}
