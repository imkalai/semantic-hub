// @ts-check
/** Sessions: pick BigQuery tables, build an ontology for them, review what was inferred. */
import { api } from "./api.js";
import { setScope, state } from "./state.js";
import { esc, relTime, toast } from "./ui.js";

const TRUST = { certified: ["ok", "Certified"], declared: ["accent", "Declared"], inferred: ["warn", "Inferred"] };
const KIND = { metric: "Metric", dimension: "Dimension", join: "Join", table: "Table" };
const STATUS = { ready: ["ok", "Ready"], failed: ["err", "Failed"], new: ["", "Not built"] };

/** @param {string} t */
export const trustPill = (t) => { const [c, l] = TRUST[t] || ["", t]; return `<span class="pill ${c}">${l}</span>`; };

/** @param {any} counts */
function countsLine(counts) {
  if (!counts) return "";
  const sum = (/** @type {string} */ t) => Object.values(counts).reduce((n, /** @type {any} */ x) => n + (x[t] || 0), 0);
  return `${sum("certified")} certified · ${sum("declared")} declared · ${sum("inferred")} inferred`;
}

// ---------- list + new session ----------

/** @type {Set<string>} */
let picked = new Set();

/** @param {HTMLElement} root */
export async function renderSessions(root) {
  root.innerHTML = `
    <div class="home-inner">
      <div class="hero"><h1>Sessions</h1>
        <p>Pick BigQuery tables and Semantic Hub builds an ontology for them from their metadata: the curated catalog
        where it covers them, keys declared in BigQuery, and sensible guesses for the rest, which you can confirm.</p></div>
      <div class="card"><div class="card-body">
        <h2>New session</h2>
        <div class="fields picker">
          <div class="field"><label for="pProject">Project</label><select id="pProject"><option value="">Loading…</option></select></div>
          <div class="field"><label for="pDataset">Dataset</label><select id="pDataset" disabled></select></div>
        </div>
        <div id="pTables" class="tables-pick"><p class="muted small">Choose a project and dataset to see its tables.</p></div>
        <div id="pPicked" class="picked"></div>
        <div class="fields"><div class="field wide"><label for="pName">Session name</label>
          <input id="pName" maxlength="200" placeholder="e.g. Sales and churn"></div></div>
      </div>
      <div class="card-foot"><span class="small muted" id="pHint">Only table details are read, never table data.</span>
        <button class="btn primary" type="button" id="pCreate" disabled>Build session</button></div></div>
      <div class="section"><h2>Your sessions</h2><div id="sList"><div class="skeleton" style="height:120px"></div></div></div>
    </div>`;

  const project = /** @type {HTMLSelectElement} */ (root.querySelector("#pProject"));
  const dataset = /** @type {HTMLSelectElement} */ (root.querySelector("#pDataset"));
  const tablesBox = /** @type {HTMLElement} */ (root.querySelector("#pTables"));
  const pickedBox = /** @type {HTMLElement} */ (root.querySelector("#pPicked"));
  const create = /** @type {HTMLButtonElement} */ (root.querySelector("#pCreate"));
  const name = /** @type {HTMLInputElement} */ (root.querySelector("#pName"));

  const showPicked = () => {
    pickedBox.innerHTML = picked.size ? `<span class="label">Selected (${picked.size})</span> ` + [...picked].map((t) =>
      `<button type="button" class="chip" data-unpick="${esc(t)}" title="Remove">${esc(t)} ✕</button>`).join(" ") : "";
    create.disabled = picked.size === 0;
    tablesBox.querySelectorAll("input[data-t]").forEach((cb) => {
      /** @type {HTMLInputElement} */ (cb).checked = picked.has(/** @type {HTMLElement} */ (cb).dataset.t || "");
    });
  };
  pickedBox.addEventListener("click", (e) => {
    const b = /** @type {HTMLElement} */ (e.target).closest("[data-unpick]");
    if (b) { picked.delete(/** @type {HTMLElement} */ (b).dataset.unpick || ""); showPicked(); }
  });

  try {
    const projects = await api("/api/bq/projects");
    project.innerHTML = `<option value="">Choose…</option>` + projects.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.id)})</option>`).join("");
  } catch (e) {
    project.innerHTML = `<option value="">Couldn't load projects</option>`;
    toast(e.message, true);
  }
  project.addEventListener("change", async () => {
    dataset.disabled = true;
    dataset.innerHTML = "";
    tablesBox.innerHTML = '<p class="muted small">Choose a dataset.</p>';
    if (!project.value) return;
    try {
      const ds = await api(`/api/bq/projects/${encodeURIComponent(project.value)}/datasets`);
      dataset.innerHTML = `<option value="">Choose…</option>` + ds.map((d) => `<option value="${esc(d.id)}">${esc(d.name)} (${esc(d.id)})</option>`).join("");
      dataset.disabled = false;
    } catch (e) { toast(e.message, true); }
  });
  dataset.addEventListener("change", async () => {
    if (!dataset.value) return;
    tablesBox.innerHTML = '<div class="skeleton" style="height:80px"></div>';
    try {
      const ts = await api(`/api/bq/projects/${encodeURIComponent(project.value)}/datasets/${encodeURIComponent(dataset.value)}/tables`);
      const full = (/** @type {any} */ t) => `${project.value}.${dataset.value}.${t.name}`;
      tablesBox.innerHTML = `<div class="tables-head"><span class="label">Tables in ${esc(dataset.value)}</span>
        <button type="button" class="btn quiet sm" id="pAll">Select all</button></div>
        <div class="tables-grid">${ts.map((t) => `<label class="tcheck"><input type="checkbox" data-t="${esc(full(t))}">
          <span><strong>${esc(t.name)}</strong><small>${esc(t.description || "")}</small></span></label>`).join("")}</div>`;
      tablesBox.querySelectorAll("input[data-t]").forEach((cb) => cb.addEventListener("change", () => {
        const t = /** @type {HTMLElement} */ (cb).dataset.t || "";
        if (/** @type {HTMLInputElement} */ (cb).checked) picked.add(t); else picked.delete(t);
        showPicked();
      }));
      tablesBox.querySelector("#pAll")?.addEventListener("click", () => { ts.forEach((t) => picked.add(full(t))); showPicked(); });
      showPicked();
    } catch (e) { tablesBox.innerHTML = `<p class="err-box">${esc(e.message)}</p>`; }
  });
  create.addEventListener("click", async () => {
    create.disabled = true;
    create.textContent = "Building…";
    try {
      const s = await api("/api/sessions", { body: { name: name.value.trim(), tables: [...picked] } });
      picked = new Set();
      document.dispatchEvent(new CustomEvent("sessionschange"));
      toast(`Session built: ${countsLine(s.report.counts)}`);
      location.hash = `#/sessions/${s.id}`;
    } catch (e) {
      toast(e.message, true);
      create.disabled = false;
      create.textContent = "Build session";
    }
  });
  showPicked();

  const list = /** @type {HTMLElement} */ (root.querySelector("#sList"));
  try {
    const sessions = await api("/api/sessions");
    list.innerHTML = sessions.length ? `<div class="history">${sessions.map((/** @type {any} */ s) => {
      const [cls, label] = STATUS[s.status] || ["", s.status];
      return `<a class="hist" href="#/sessions/${s.id}"><span class="pill ${cls}">${label}</span>
        <span><strong>${esc(s.name)}</strong><small>${s.tables.length} table${s.tables.length === 1 ? "" : "s"} · ${esc(countsLine(s.report?.counts) || s.error)}</small></span>
        <small class="muted">${esc(relTime(s.built_at || s.created_at))}</small></a>`;
    }).join("")}</div>` : '<p class="empty">No sessions yet. Pick some tables above to build the first one.</p>';
  } catch (e) { list.innerHTML = `<p class="err-box">${esc(e.message)}</p>`; }
}

// ---------- one session: report + review ----------

/** @param {any} item */
function editForm(item) {
  const syn = item.synonyms ? `<div class="field"><label>Also called (comma-separated)</label>
    <input data-f="synonyms" value="${esc((item.synonyms || []).join(", "))}"></div>` : "";
  const label = item.kind !== "join" ? `<div class="field"><label>Label</label><input data-f="label" value="${esc(item.label)}"></div>` : "";
  let extra = "";
  if (item.kind === "metric" && item.agg !== "count") {
    extra = `<div class="field"><label>Calculation</label><select data-f="agg">${["sum", "avg", "count_distinct", "min", "max"]
      .map((a) => `<option value="${a}"${a === item.agg ? " selected" : ""}>${a.replace("_", " ")}</option>`).join("")}</select></div>`;
  }
  if (item.kind === "join") {
    extra = `<div class="field"><label>Relationship</label><select data-f="relationship">${["many_to_one", "one_to_one", "one_to_many"]
      .map((r) => `<option value="${r}"${r === item.relationship ? " selected" : ""}>${r.replaceAll("_", " ")}</option>`).join("")}</select></div>`;
  }
  if (item.kind === "dimension") {
    extra = `<label class="check"><input type="checkbox" data-f="pii"${item.pii ? " checked" : ""}> Personal data</label>`;
  }
  if (item.kind === "table") {
    extra = `<div class="field"><label>Primary key column</label><input data-f="primary_key" value="${esc(item.primary_key || "")}"></div>`;
  }
  return `<div class="edit" hidden><div class="fields">${label}${syn}${extra}</div>
    <div class="row-btns"><button type="button" class="btn primary sm" data-save>Save and confirm</button></div></div>`;
}

/** @param {any} item */
function reviewRow(item) {
  return `
    <div class="review" data-key="${esc(item.key)}">
      <div class="review-main">
        <span class="pill">${KIND[item.kind]}</span>
        <div><strong>${esc(item.label)}</strong> ${trustPill(item.trust)}${item.pii ? ' <span class="pill err">Personal data</span>' : ""}
          <small class="mono">${esc(item.detail)}</small>
          ${item.synonyms?.length ? `<small>Also called: ${item.synonyms.map(esc).join(", ")}</small>` : ""}</div>
        <div class="row-btns">
          <button type="button" class="btn sm primary" data-decide="approved">Confirm</button>
          <button type="button" class="btn sm" data-edit>Edit</button>
          ${item.kind !== "table" ? '<button type="button" class="btn sm danger" data-decide="rejected">Reject</button>' : ""}
        </div>
      </div>
      ${editForm(item)}
    </div>`;
}

/** @param {HTMLElement} root @param {number} id */
export async function renderSessionDetail(root, id) {
  root.innerHTML = '<div class="home-inner"><div class="skeleton" style="height:240px"></div></div>';
  let data;
  try {
    data = await api(`/api/sessions/${id}/review`);
  } catch (e) {
    root.innerHTML = `<div class="home-inner"><a href="#/sessions" class="btn quiet sm">← Sessions</a><p class="err-box">${esc(e.message)}</p></div>`;
    return;
  }
  const s = data.session;
  const r = s.report || {};
  const kinds = ["tables", "metrics", "dimensions", "joins"];
  const active = state.scope === s.scope;
  root.innerHTML = `
    <div class="home-inner">
      <div><a href="#/sessions" class="btn quiet sm">← Sessions</a></div>
      <div class="section-head">
        <div class="hero"><h1>${esc(s.name)}</h1><p>${s.tables.length} table${s.tables.length === 1 ? "" : "s"} ·
          built ${esc(relTime(s.built_at || s.created_at))}</p></div>
        <div class="work-tools">
          <button class="btn sm" type="button" id="sRebuild">Check for changes</button>
          <button class="btn sm danger" type="button" id="sDelete">Delete</button>
          <button class="btn primary sm" type="button" id="sUse"${active ? " disabled" : ""}>${active ? "In use" : "Ask questions here"}</button>
        </div>
      </div>
      <div class="card"><div class="card-body">
        <table class="trust-table"><thead><tr><th></th><th>Certified</th><th>Declared</th><th>Inferred</th></tr></thead>
          <tbody>${kinds.map((k) => `<tr><th>${k[0].toUpperCase() + k.slice(1)}</th>${["certified", "declared", "inferred"]
            .map((t) => `<td>${r.counts?.[k]?.[t] ?? 0}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted">Sources: ${(r.sources || []).map((x) => `${esc(x.name)} ${x.status ? `(${esc(x.status)})` : `(${x.items})`}`).join(" · ")}</p>
        ${(r.notes || []).map((n) => `<p class="small">${esc(n)}</p>`).join("")}
        <details class="small"><summary>Tables</summary><p class="mono">${s.tables.map(esc).join("<br>")}</p></details>
      </div></div>
      <div class="section">
        <div class="section-head"><div><h2>To review (${data.items.length})</h2>
          <p class="muted small">Confirm what's right, correct or reject the rest. Decisions apply to every session using these tables.
          Guessed joins must be confirmed before questions can use them.</p></div></div>
        ${data.items.length ? `<div class="reviews">${data.items.map(reviewRow).join("")}</div>`
          : '<p class="empty">Everything in this session is certified.</p>'}
      </div>
    </div>`;

  root.querySelector("#sUse")?.addEventListener("click", () => { setScope(s.scope); location.hash = "#/"; });
  root.querySelector("#sRebuild")?.addEventListener("click", async (e) => {
    const b = /** @type {HTMLButtonElement} */ (e.currentTarget);
    b.disabled = true;
    try {
      await api(`/api/sessions/${id}/build`, { method: "POST" });
      toast("Session is up to date");
      renderSessionDetail(root, id);
    } catch (err) { toast(err.message, true); b.disabled = false; }
  });
  root.querySelector("#sDelete")?.addEventListener("click", async () => {
    if (!confirm(`Delete session "${s.name}" and its questions? Your confirmations are kept.`)) return;
    try {
      await api(`/api/sessions/${id}`, { method: "DELETE" });
      if (state.scope === s.scope) setScope("catalog");
      document.dispatchEvent(new CustomEvent("sessionschange"));
      location.hash = "#/sessions";
    } catch (err) { toast(err.message, true); }
  });
  root.querySelectorAll(".review").forEach((row) => {
    const key = /** @type {HTMLElement} */ (row).dataset.key || "";
    const send = async (/** @type {string} */ decision, /** @type {any} */ changes = {}) => {
      try {
        await api("/api/approvals", { body: { key, decision, changes } });
        toast(decision === "rejected" ? "Rejected" : "Confirmed");
        renderSessionDetail(root, id);
      } catch (err) { toast(err.message, true); }
    };
    row.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", () => send(/** @type {HTMLElement} */ (b).dataset.decide || "")));
    row.querySelector("[data-edit]")?.addEventListener("click", () => {
      const edit = /** @type {HTMLElement} */ (row.querySelector(".edit"));
      edit.hidden = !edit.hidden;
    });
    row.querySelector("[data-save]")?.addEventListener("click", () => {
      /** @type {any} */
      const changes = {};
      row.querySelectorAll("[data-f]").forEach((el) => {
        const f = /** @type {HTMLElement} */ (el).dataset.f || "";
        const input = /** @type {HTMLInputElement} */ (el);
        if (f === "synonyms") changes.synonyms = input.value.split(",").map((x) => x.trim()).filter(Boolean);
        else if (f === "pii") changes.pii = input.checked;
        else if (input.value.trim()) changes[f] = input.value.trim();
      });
      send("approved", changes);
    });
  });
}
