// @ts-check
import { api } from "./api.js";
import { esc, icon, toast } from "./ui.js";

const STATUS = { ok: ["ok", "OK"], warn: ["warn", "Warning"], fail: ["err", "Failed"] };

/** @param {any} c */
const checkRow = (c) => {
  const [cls, label] = STATUS[c.status] || ["", c.status];
  return `
    <div class="check">
      <span class="pill ${cls}">${label}</span>
      <div><p><strong>${esc(c.name)}</strong></p><p>${esc(c.detail)}</p>${c.fix && c.status !== "ok" ? `<p class="fix">${esc(c.fix)}</p>` : ""}</div>
      <span class="ms">${c.ms} ms</span>
    </div>`;
};

/** @param {HTMLElement} box */
async function load(box) {
  box.innerHTML = '<div class="skeleton" style="height:240px"></div>';
  let r;
  try {
    r = await api("/api/health/full");
  } catch (e) {
    box.innerHTML = `<p class="err-box">${esc(e.message)}</p>`;
    return;
  }
  /** @type {Record<string, any[]>} */
  const groups = {};
  r.checks.forEach((c) => (groups[c.group] ||= []).push(c));
  box.innerHTML = `
    <div class="banner ${r.ready ? "ok" : "fail"}"><span class="dot"></span>
      <div class="grow"><strong>${esc(r.verdict)}</strong><small>Same checks as <span class="mono">python -m app.cli healthcheck</span></small></div>
    </div>
    <div class="card"><div class="card-body checks">
      ${Object.entries(groups).map(([g, cs]) => `<div class="check-group"><h3>${esc(g)}</h3>${cs.map(checkRow).join("")}</div>`).join("")}
    </div></div>`;
}

/** @param {HTMLElement} root */
export function renderHealth(root) {
  root.innerHTML = `
    <div class="home-inner">
      <div class="section-head">
        <div class="hero"><h1>Health</h1><p>Configuration, storage, Cortex, BigQuery and whether the ontology holds everything the catalog defines.</p></div>
        <div class="work-tools">
          <button class="btn sm" type="button" id="rerun">${icon("refresh")} Run again</button>
          <button class="btn sm" type="button" id="reindex">Rebuild index</button>
          <button class="btn primary sm" type="button" id="boot">Rerun bootstrap</button>
        </div>
      </div>
      <div class="section" id="healthBox"></div>
      <div class="section" id="bootBox" hidden></div>
    </div>`;
  const box = /** @type {HTMLElement} */ (root.querySelector("#healthBox"));
  const bootBox = /** @type {HTMLElement} */ (root.querySelector("#bootBox"));
  root.querySelector("#rerun")?.addEventListener("click", () => load(box));
  root.querySelector("#reindex")?.addEventListener("click", async (e) => {
    const btn = /** @type {HTMLButtonElement} */ (e.currentTarget);
    btn.disabled = true;
    try {
      const r = await api("/api/retrieval/index", { method: "POST" });
      toast(`Index updated: ${r.documents} documents, ${r.embedded} embedded`);
      await load(box);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  root.querySelector("#boot")?.addEventListener("click", async (e) => {
    const btn = /** @type {HTMLButtonElement} */ (e.currentTarget);
    btn.disabled = true;
    try {
      const r = await api("/api/bootstrap", { method: "POST" });
      bootBox.hidden = false;
      bootBox.innerHTML = `<h2>Bootstrap result</h2><div class="card"><div class="card-body"><ul class="steps">${r.steps.map((s) => `
        <li><span class="mark">${icon("check", "i")}</span><span>${esc(s.name)}${s.detail ? ` <span class="muted small">· ${esc(s.detail)}</span>` : ""}</span>
        <span class="mono small">+${s.added} ~${s.updated} −${s.removed}</span></li>`).join("")}</ul></div></div>`;
      toast("Bootstrap finished");
      await load(box);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  load(box);
}
