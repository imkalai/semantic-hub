// @ts-check
import { api } from "./api.js";
import { renderCatalog } from "./catalog.js";
import { renderHealth } from "./health.js";
import { renderHome } from "./home.js";
import { renderSessions, renderSessionDetail } from "./sessions.js";
import { setScope, state } from "./state.js";
import { $, applyTheme, currentTheme, esc } from "./ui.js";

const ROUTES = { "": ["home", renderHome], catalog: ["catalog", renderCatalog], health: ["health", renderHealth],
  sessions: ["sessions", renderSessions] };

function route() {
  const parts = (location.hash || "#/").replace(/^#\/?/, "").split("/");
  const view = $("#view");
  view.scrollTop = 0;
  // Each render gets its own container. A slower, earlier render that finishes late then writes into a
  // container that's no longer on the page, instead of replacing the current one.
  const page = document.createElement("div");
  page.className = "page";
  view.replaceChildren(page);
  let nav = "home";
  if (parts[0] === "sessions" && /^\d+$/.test(parts[1] || "")) {
    nav = "sessions";
    renderSessionDetail(page, Number(parts[1]));
  } else {
    const [n, render] = ROUTES[parts[0]] || ROUTES[""];
    nav = n;
    render(page);
  }
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (/** @type {HTMLElement} */ (a).dataset.nav === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

/** Refresh the session list and the "Working in" picker. */
export async function loadSessions() {
  try {
    state.sessions = await api("/api/sessions");
  } catch {
    state.sessions = [];
  }
  const ready = state.sessions.filter((s) => s.status === "ready");
  if (state.scope !== "catalog" && !ready.some((s) => s.scope === state.scope)) setScope("catalog");
  const sel = /** @type {HTMLSelectElement} */ ($("#scopeSelect"));
  sel.innerHTML = `<option value="catalog">Curated catalog</option>` +
    (ready.length ? `<optgroup label="Sessions">${ready.map((s) => `<option value="${esc(s.scope)}">${esc(s.name)}</option>`).join("")}</optgroup>` : "");
  sel.value = state.scope;
}

async function init() {
  let saved = null;
  try { saved = localStorage.getItem("sh-theme"); } catch {}
  applyTheme(saved || "system");
  $("#themeBtn").addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark"));
  $("#scopeSelect").addEventListener("change", (e) => setScope(/** @type {HTMLSelectElement} */ (e.target).value));
  document.addEventListener("scopechange", () => {
    /** @type {HTMLSelectElement} */ ($("#scopeSelect")).value = state.scope;
    route();
  });
  document.addEventListener("sessionschange", () => loadSessions());
  window.addEventListener("hashchange", route);
  await loadSessions();
  route();
  try {
    const info = await api("/api/app");
    const demo = info.metadata_mode === "demo" || info.cortex_mode === "mock";
    const pill = $("#modePill");
    pill.textContent = demo ? "Development mode" : `v${info.version}`;
    pill.title = `BigQuery: ${info.metadata_mode} · Cortex: ${info.cortex_mode}`;
    pill.classList.toggle("warn", demo);
    pill.hidden = false;
  } catch (e) {
    $("#view").innerHTML = `<div class="home-inner"><p class="err-box">${esc(e.message)}</p></div>`;
  }
}

init();
