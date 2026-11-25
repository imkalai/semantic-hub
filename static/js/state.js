// @ts-check
/** Shared app state: which scope questions and the catalog page work in. */

const KEY = "sh-scope";

export const state = {
  /** "catalog" (the curated catalog) or "session:<id>" */
  scope: (() => { try { return localStorage.getItem(KEY) || "catalog"; } catch { return "catalog"; } })(),
  /** @type {any[]} sessions from /api/sessions */
  sessions: [],
};

/** @param {string} scope */
export function setScope(scope) {
  state.scope = scope;
  try { localStorage.setItem(KEY, scope); } catch {}
  document.dispatchEvent(new CustomEvent("scopechange"));
}

export const scopeQuery = () => `scope=${encodeURIComponent(state.scope)}`;

export function scopeLabel() {
  if (state.scope === "catalog") return "Curated catalog";
  const s = state.sessions.find((x) => x.scope === state.scope);
  return s ? s.name : "Session";
}
