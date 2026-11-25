// @ts-check
import { api } from "./api.js";
import { trustPill } from "./sessions.js";
import { scopeLabel, scopeQuery } from "./state.js";
import { esc } from "./ui.js";

let tab = "metrics";
let query = "";
/** @type {any} */
let data = null;

const TABS = [["metrics", "Metrics"], ["dimensions", "Dimensions"], ["tables", "Tables"], ["joins", "Approved joins"]];

/** @param {any} p */
function describeMetric(p) {
  if (p.agg === "ratio") return `${esc(p.numerator)} ÷ ${esc(p.denominator)}`;
  if (p.agg === "count") return `count of ${esc(p.table)} rows`;
  return `${esc(p.agg.replace("_", " "))} of ${esc(p.table)}.${esc(p.column)}`;
}

/** @param {any} c */
function conceptRow(c) {
  const p = c.props || {};
  let what = "";
  if (c.kind === "metric") what = describeMetric(p) + (p.format !== "number" ? ` · ${esc(p.format)}` : "");
  if (c.kind === "dimension") what = `${esc(p.table)}.${esc(p.column)} · ${esc(p.type)}` +
    (p.time_grains?.length ? ` · ${p.time_grains.map(esc).join(", ")}` : "");
  if (c.kind === "table") what = `<span class="mono">${esc(p.table)}</span> · key ${esc(p.primary_key || "none found")}`;
  const pii = p.pii ? ' <span class="pill err">Personal data</span>' : "";
  const trust = p.trust && p.trust !== "certified" ? ` ${trustPill(p.trust)}` : "";
  return `
    <div class="concept">
      <div><strong>${esc(c.label)}${pii}${trust}</strong><small class="mono">${esc(c.id)}</small></div>
      <div><small>${what}</small><small>${esc(p.description || "")}</small></div>
      <div class="tags">${c.synonyms.map((s) => `<span class="pill">${esc(s)}</span>`).join("") || '<small>No synonyms</small>'}</div>
    </div>`;
}

/** @param {any} j */
function joinRow(j) {
  const keys = j.on.map((k) => `${esc(j.left)}.${esc(k.left)} = ${esc(j.right)}.${esc(k.right)}`).join(" and ");
  return `
    <div class="concept">
      <div><strong>${esc(j.left)} → ${esc(j.right)}</strong><small class="mono">${esc(j.name)}</small></div>
      <div><small class="mono">${keys}</small></div>
      <div class="tags"><span class="pill accent">${esc(j.relationship.replaceAll("_", " "))}</span>${
        j.trust && j.trust !== "certified" ? trustPill(j.trust) : ""}</div>
    </div>`;
}

/** @param {HTMLElement} root */
function renderList(root) {
  const list = /** @type {HTMLElement} */ (root.querySelector("#conceptList"));
  if (!list || !data) return;
  const q = query.toLowerCase();
  const items = (data[tab] || []).filter((c) => !q || JSON.stringify(c).toLowerCase().includes(q));
  list.innerHTML = items.length ? items.map(tab === "joins" ? joinRow : conceptRow).join("")
    : `<p class="empty">Nothing matches “${esc(query)}”.</p>`;
}

/** @param {HTMLElement} root */
export async function renderCatalog(root) {
  root.innerHTML = `
    <div class="home-inner">
      <div class="hero"><h1>Catalog: ${esc(scopeLabel())}</h1><p>The governed vocabulary: what can be measured, how it can be split, and which tables may be joined.</p></div>
      <div class="card"><div class="card-body">
        <label class="label" for="resolveIn">Try a business term</label>
        <form class="resolve" id="resolveForm">
          <input id="resolveIn" placeholder="e.g. turnover, AOV, area, churn" autocomplete="off">
          <button class="btn primary sm" type="submit">Resolve</button>
        </form>
        <div class="resolve-out" id="resolveOut" aria-live="polite"></div>
      </div></div>
      <div class="section">
        <div class="section-head">
          <div class="seg" role="tablist" aria-label="Concept type">
            ${TABS.map(([k, l]) => `<button type="button" role="tab" data-tab="${k}" aria-pressed="${tab === k}">${l}</button>`).join("")}
          </div>
          <div class="work-tools"><input id="catSearch" placeholder="Search…" aria-label="Search the catalog" value="${esc(query)}"></div>
        </div>
        <p class="muted small" id="catSource"></p>
        <div class="concepts" id="conceptList"><div class="skeleton" style="height:200px"></div></div>
      </div>
    </div>`;

  root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    tab = /** @type {HTMLElement} */ (b).dataset.tab || "metrics";
    root.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderList(root);
  }));
  root.querySelector("#catSearch")?.addEventListener("input", (e) => { query = /** @type {HTMLInputElement} */ (e.target).value; renderList(root); });
  root.querySelector("#resolveForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const term = /** @type {HTMLInputElement} */ (root.querySelector("#resolveIn")).value.trim();
    const out = /** @type {HTMLElement} */ (root.querySelector("#resolveOut"));
    if (!term) return;
    try {
      const r = await api(`/api/catalog/resolve?term=${encodeURIComponent(term)}&${scopeQuery()}`);
      out.innerHTML = r.matches.length
        ? r.matches.map((m) => `<span class="pill accent">${esc(m.kind)}</span> <strong>${esc(m.label)}</strong> <span class="muted mono">${esc(m.id)}</span>${m.table ? ` <span class="muted">on ${esc(m.table)}</span>` : ""}`).join("<br>")
        : `<span class="muted">“${esc(term)}” isn't a catalog term. Ask it as a question on Home to find concepts with a similar meaning.</span>`;
    } catch (err) {
      out.innerHTML = `<span class="err-msg">${esc(err.message)}</span>`;
    }
  });

  try {
    const fresh = await api(`/api/catalog?${scopeQuery()}`);
    if (!root.isConnected) return;  // the user has moved on; don't overwrite the newer page's data
    data = fresh;
  } catch (e) {
    /** @type {HTMLElement} */ (root.querySelector("#conceptList")).innerHTML = `<p class="err-box">${esc(e.message)}</p>`;
    return;
  }
  const s = data.summary;
  /** @type {HTMLElement} */ (root.querySelector("#catSource")).innerHTML = s.source === "graph_store"
    ? "Read from the graph store."
    : `<span class="pill warn">JSON catalog fallback</span> ${esc(s.note)}`;
  renderList(root);
}
