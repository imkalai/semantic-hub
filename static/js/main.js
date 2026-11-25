// @ts-check
import { api } from "./api.js";
import { renderCatalog } from "./catalog.js";
import { renderHealth } from "./health.js";
import { renderHome } from "./home.js";
import { $, applyTheme, currentTheme, esc } from "./ui.js";

const ROUTES = { "": ["home", renderHome], catalog: ["catalog", renderCatalog], health: ["health", renderHealth] };

function route() {
  const key = (location.hash || "#/").replace(/^#\/?/, "").split("/")[0];
  const [nav, render] = ROUTES[key] || ROUTES[""];
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (/** @type {HTMLElement} */ (a).dataset.nav === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const view = $("#view");
  view.scrollTop = 0;
  render(view);
}

async function init() {
  let saved = null;
  try { saved = localStorage.getItem("sh-theme"); } catch {}
  applyTheme(saved || "system");
  $("#themeBtn").addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark"));
  window.addEventListener("hashchange", route);
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
