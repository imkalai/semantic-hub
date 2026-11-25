// @ts-check
import { api } from "./api.js";
import { scopeLabel, scopeQuery, state } from "./state.js";
import { esc, icon, relTime, toast } from "./ui.js";

const SOURCE = {
  graph_store: ["ok", "Graph store"],
  json_fallback: ["warn", "JSON catalog fallback"],
};
const EXAMPLES = [
  ["Revenue by region", "Monthly turnover by region for web orders"],
  ["Basket size", "Average basket size in the North last quarter"],
  ["Churn", "How many cancellations by churn reason"],
  ["Top categories", "Top 5 product categories by product revenue this year"],
];
const KIND = { metric: "Metric", dimension: "Dimension", table: "Table", column: "Column" };
const STATUS = { ready: ["ok", "Planned"], clarify: ["warn", "Needs detail"], invalid: ["err", "Couldn't plan"],
  blocked: ["err", "Blocked"] };
const CHECK = { pass: ["ok", "Passed"], adjusted: ["accent", "Adjusted"], warn: ["warn", "Warning"], block: ["err", "Blocked"] };

/** @param {any} report */
function guardrails(report) {
  if (!report) return "";
  const order = { block: 0, warn: 1, adjusted: 2, pass: 3 };
  const checks = [...report.checks].sort((a, b) => order[a.status] - order[b.status]);
  const notable = checks.filter((c) => c.status !== "pass").length;
  return `
    <details class="acc guard"${notable ? " open" : ""}>
      <summary><span class="sum-text">Guardrails<small>${notable ? `${notable} to look at` : "All checks passed"}</small></span></summary>
      <div class="acc-body">${checks.map((c) => {
        const [cls, label] = CHECK[c.status] || ["", c.status];
        const actions = (c.actions || []).map((/** @type {any} */ a) =>
          `<button type="button" class="btn sm" data-approve="${esc(a.key)}">${esc(a.label)}</button>`).join(" ");
        return `<div class="check"><span class="pill ${cls}">${label}</span><div><p><strong>${esc(c.label)}</strong></p><p>${esc(c.message)}</p>${
          actions ? `<div class="row-btns">${actions}</div>` : ""}</div></div>`;
      }).join("")}</div>
    </details>`;
}
const GRAIN = { date: "Day", week: "Week", month: "Month", quarter: "Quarter", year: "Year" };
const OPS = { equals: "=", not_equals: "≠", in: "in", not_in: "not in", contains: "contains", gt: ">", gte: "≥",
  lt: "<", lte: "≤", between: "between", is_null: "is empty", is_not_null: "is not empty" };

let lastQuestion = "";
/** @type {any} */
let lastOutcome = null;
let lastScope = state.scope;

/** @param {any} c */
function candidate(c) {
  const flags = (c.governed ? "" : ' <span class="pill warn">Not in catalog</span>') +
    (c.pii ? ' <span class="pill err">Personal data</span>' : "");
  return `
    <div class="cand">
      <span class="pill ${c.kind === "metric" ? "accent" : ""}">${KIND[c.kind] || esc(c.kind)}</span>
      <div><strong>${esc(c.label)}</strong>${flags}
        <small class="mono">${esc(c.id)}${c.table && c.kind !== "table" ? ` · on ${esc(c.table)}` : ""}</small>
        <small>${c.reasons.map(esc).join(" · ")}</small></div>
      <span class="score" title="Match strength"><i style="width:${Math.round(c.score * 100)}%"></i></span>
    </div>`;
}

/** @param {any} r */
function retrievalBody(r) {
  const unmatched = r.unmatched.length
    ? `<p class="small muted">Not recognised: ${r.unmatched.map((w) => `<span class="pill">${esc(w)}</span>`).join(" ")}</p>` : "";
  const note = r.note ? `<p class="small muted">${esc(r.note)}</p>` : "";
  return (r.candidates.length ? `<div class="cands">${r.candidates.map(candidate).join("")}</div>`
    : '<p class="muted small">Nothing in the catalog matched this question.</p>') + unmatched + note;
}

/** @param {string} label @param {string[]} items */
const part = (label, items) => `<div class="plan-part"><span class="label">${label}</span>${
  items.length ? items.map((i) => `<span class="pill">${i}</span>`).join(" ") : '<span class="muted small">None</span>'}</div>`;

/** @param {any} o */
function planParts(o) {
  const p = o.plan;
  const name = (/** @type {string} */ id) => esc(o.labels?.[id] || id.split(":")[1]);
  const tr = p.time_range;
  const range = !tr ? "" : tr.kind === "absolute" ? `${esc(tr.start)} → ${esc(tr.end)}`
    : tr.include_current ? `This ${esc(tr.unit)}${tr.count > 1 ? ` + ${tr.count - 1} before` : " so far"}`
    : `Last ${tr.count > 1 ? `${tr.count} ${esc(tr.unit)}s` : esc(tr.unit)}`;
  const time = p.time_dimension
    ? [`${name("dimension:" + p.time_dimension)}${p.time_grain ? ` by ${GRAIN[p.time_grain].toLowerCase()}` : ""}`, ...(range ? [range] : [])] : [];
  const order = p.order_by.map((/** @type {any} */ x) =>
    `${x.field === "time" ? "Time" : esc(o.labels?.["metric:" + x.field] || o.labels?.["dimension:" + x.field] || x.field)} ${x.direction === "desc" ? "↓" : "↑"}`);
  return `
    <div class="plan-grid">
      ${part("Measures", p.metrics.map((/** @type {string} */ m) => name("metric:" + m)))}
      ${part("Group by", p.dimensions.map((/** @type {string} */ d) => name("dimension:" + d)))}
      ${part("Time", time)}
      ${part("Filters", p.filters.map((/** @type {any} */ f) =>
        `${name("dimension:" + f.dimension)} ${OPS[f.operator] || esc(f.operator)} ${f.values.map(esc).join(", ")}`))}
      ${part("Order and rows", [...order, `${p.limit} rows max`])}
    </div>`;
}

/** @param {any} o */
function outcome(o) {
  const [cls, label] = STATUS[o.status] || ["", o.status];
  const p = o.plan;
  let head = "", body = "";
  if (o.status === "ready" || o.status === "blocked") {
    const r = o.resolved;
    head = `<h2 class="plan-title">${esc(r.summary)}</h2>` + (o.status === "blocked"
      ? `<div class="err-box"><strong>This plan can't run.</strong><ul class="errs">${o.errors.map((/** @type {string} */ e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "");
    body = planParts(o) + `
      <p class="small muted">Tables: ${r.tables.map((/** @type {string} */ t) => `<span class="mono">${esc(t)}</span>`).join(", ")}${
        r.joins.length ? ` · joins: ${r.joins.map((/** @type {any} */ j) => `<span class="mono">${esc(j.name)}</span>`).join(", ")}` : ""}</p>`;
  } else if (o.status === "clarify") {
    head = `<h2 class="plan-title">${esc(o.clarification)}</h2>`;
    body = '<p class="small muted">Try naming a measure from the catalog, e.g. revenue, orders or churned accounts.</p>';
  } else {
    head = '<h2 class="plan-title">This question couldn\'t be turned into a valid plan</h2>';
    body = `<ul class="errs">${o.errors.map((/** @type {string} */ e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
  }
  const assumptions = p?.assumptions?.length
    ? `<div class="assume"><span class="label">Assumed</span><ul>${p.assumptions.map((/** @type {string} */ a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : "";
  const tries = o.attempts.length > 1 ? ` · ${o.attempts.length} attempts` : "";
  return `
    <div class="card">
      <div class="card-body">
        <div class="section-head"><span class="pill ${cls}">${label}</span>
          <span class="small muted">Planned by Cortex (${esc(o.cortex)})${tries}</span></div>
        ${head}${body}${assumptions}
        ${guardrails(o.guardrails)}
        <details class="acc"><summary><span class="sum-text">Why these concepts<small>What metadata retrieval found for the question</small></span></summary>
          <div class="acc-body">${retrievalBody(o.retrieval)}</div></details>
        ${p ? `<details class="acc"><summary><span class="sum-text">Plan JSON<small>Exactly what Cortex returned, after checks</small></span></summary>
          <div class="acc-body"><pre class="code">${esc(JSON.stringify(p, null, 2))}</pre></div></details>` : ""}
      </div>
      <div class="card-foot"><span class="small muted">Running the plan on BigQuery comes in the next phase.</span></div>
    </div>`;
}

/** @param {HTMLElement} box */
async function loadHistory(box) {
  try {
    const items = await api(`/api/questions?limit=8&${scopeQuery()}`);
    box.innerHTML = items.length ? `
      <h2>Recent questions</h2>
      <div class="history">${items.map((/** @type {any} */ q) => {
        const [cls, label] = STATUS[q.status] || ["", q.status];
        return `<button type="button" class="hist" data-q="${q.id}"><span class="pill ${cls}">${label}</span>
          <span><strong>${esc(q.question)}</strong><small>${esc(q.summary)}</small></span>
          <small class="muted">${esc(relTime(q.created_at))}</small></button>`;
      }).join("")}</div>` : "";
  } catch {
    box.innerHTML = "";
  }
}

/** @param {HTMLElement} root */
export async function renderHome(root) {
  if (lastScope !== state.scope) {  // a different catalog or session: start fresh
    lastScope = state.scope;
    lastOutcome = null;
  }
  root.innerHTML = `
    <div class="home-inner">
      <div class="hero">
        <h1>Ask your data in business terms</h1>
        <p>Semantic Hub maps your words to governed metrics, dimensions and approved joins, then plans and runs the query on BigQuery.</p>
        <form class="prompt" id="askForm">
          <label for="ask" class="label" style="padding:4px 10px 0">Your question · asking in <strong>${esc(scopeLabel())}</strong>
            ${state.scope === "catalog" ? '· <a href="#/sessions">use your own tables</a>' : ""}</label>
          <textarea id="ask" rows="2" maxlength="1000" placeholder="e.g. Monthly turnover by region for web orders">${esc(lastQuestion)}</textarea>
          <div class="prompt-foot">
            <div class="chips">${(state.scope === "catalog" ? EXAMPLES : []).map(([l, t]) => `<button class="chip" type="button" data-ex="${esc(t)}">${esc(l)}</button>`).join("")}</div>
            <button class="btn primary" type="submit" id="askBtn">Plan ${icon("send")}</button>
          </div>
        </form>
      </div>
      <div class="section" id="answer" aria-live="polite">${lastOutcome ? outcome(lastOutcome) : ""}</div>
      <div class="section" id="history"></div>
      <div class="section" id="homeStatus"><div class="skeleton" style="height:120px"></div></div>
    </div>`;

  const form = /** @type {HTMLFormElement} */ (root.querySelector("#askForm"));
  const ask = /** @type {HTMLTextAreaElement} */ (root.querySelector("#ask"));
  const btn = /** @type {HTMLButtonElement} */ (root.querySelector("#askBtn"));
  const answer = /** @type {HTMLElement} */ (root.querySelector("#answer"));
  const history = /** @type {HTMLElement} */ (root.querySelector("#history"));
  const run = async () => {
    const question = ask.value.trim();
    if (!question || btn.disabled) return;
    lastQuestion = question;
    btn.disabled = true;
    answer.innerHTML = '<div class="skeleton" style="height:200px"></div>';
    try {
      lastOutcome = await api("/api/questions", { body: { question, scope: state.scope } });
      answer.innerHTML = outcome(lastOutcome);
      loadHistory(history);
    } catch (e) {
      answer.innerHTML = `<p class="err-box">${esc(e.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
  ask.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); } });
  root.querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
    ask.value = /** @type {HTMLElement} */ (b).dataset.ex || "";
    run();
  }));
  // "Confirm …" buttons in the guardrails: record the decision, then plan the question again
  answer.addEventListener("click", async (e) => {
    const b = /** @type {HTMLButtonElement | null} */ (/** @type {HTMLElement} */ (e.target).closest("[data-approve]"));
    if (!b) return;
    b.disabled = true;
    try {
      await api("/api/approvals", { body: { key: b.dataset.approve, decision: "approved" } });
      toast("Confirmed. Planning again…");
      ask.value = lastOutcome?.question || ask.value;
      await run();
    } catch (err) {
      toast(err.message, true);
      b.disabled = false;
    }
  });
  history.addEventListener("click", async (e) => {
    const item = /** @type {HTMLElement} */ (e.target).closest("[data-q]");
    if (!item) return;
    try {
      lastOutcome = await api(`/api/questions/${/** @type {HTMLElement} */ (item).dataset.q}`);
      lastQuestion = ask.value = lastOutcome.question;
      answer.innerHTML = outcome(lastOutcome);
      answer.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      answer.innerHTML = `<p class="err-box">${esc(err.message)}</p>`;
    }
  });
  loadHistory(history);

  let cat, health;
  try {
    [cat, health] = await Promise.all([api(`/api/catalog?${scopeQuery()}`), api("/api/health/full")]);
  } catch (e) {
    /** @type {HTMLElement} */ (root.querySelector("#homeStatus")).innerHTML = `<p class="err-box">${esc(e.message)}</p>`;
    return;
  }
  const s = cat.summary;
  const [cls, label] = SOURCE[s.source] || ["", s.source];
  /** @type {HTMLElement} */ (root.querySelector("#homeStatus")).innerHTML = `
    <div class="banner ${health.ready ? "ok" : "fail"}">
      <span class="dot"></span>
      <div class="grow"><strong>${esc(health.verdict)}</strong>
        <small>${health.checks.filter((c) => c.status === "fail").length} failing · ${health.checks.filter((c) => c.status === "warn").length} warnings</small></div>
      <a class="btn sm" href="#/health">See health checks</a>
    </div>
    <div class="section-head">
      <div><h2>Ontology: ${esc(scopeLabel())}</h2><p class="muted small">Read from <span class="pill ${cls}">${esc(label)}</span>${s.note ? ` · ${esc(s.note)}` : ""}</p></div>
      <a class="btn sm quiet" href="#/catalog">Browse it</a>
    </div>
    <div class="kpis">
      ${[["Metrics", s.metrics], ["Dimensions", s.dimensions], ["Tables", s.tables], ["Approved joins", s.joins], ["Synonyms", s.synonyms]]
        .map(([k, v]) => `<div class="kpi"><span>${k}</span><strong style="color:var(--ink)">${v}</strong></div>`).join("")}
    </div>`;
}
