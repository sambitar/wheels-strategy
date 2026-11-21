/* Core: api, state, routing, modal, toast, formatting helpers. */

const state = {
  view: "book",
  project: false,
  data: null,
  loading: false,
};

const VIEW_META = {
  book: { title: "Book", sub: "Open positions and what to do next" },
  scan: { title: "Scan", sub: "Cash-secured puts ranked inside your DTE window" },
  calls: { title: "Calls", sub: "Covered calls against shares you actually hold" },
  plan: { title: "Plan", sub: "Projection only — what your open puts become IF assigned" },
  journal: { title: "Ledger", sub: "Append-only event log and realized P/L" },
  settings: { title: "Settings", sub: "Saved to config.toml immediately" },
};

/* ---------- api ---------- */

async function api(path, { method = "GET", body = null } = {}) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`bad response from ${path}`);
  }
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

/* ---------- formatting ---------- */

const money = (v, dec = 0) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Number(v);
  const s = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
  return (n < 0 ? "-$" : "$") + s;
};

const moneySigned = (v, dec = 0) => {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return (n > 0 ? "+" : "") + money(n, dec);
};

const pct = (v, dec = 1, signed = true) => {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return (signed && n > 0 ? "+" : "") + n.toFixed(dec) + "%";
};

const num = (v, dec = 2) =>
  v === null || v === undefined ? "—" : Number(v).toFixed(dec);

const signClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "dim");

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

/* Short flag codes keep table columns tight; the modal spells them out. */
function shortFlags(flags = []) {
  return flags.map((f) => {
    if (f.startsWith("earnings")) return ["earn", "amber"];
    if (f.startsWith("ex-div")) return ["exdiv", "red"];
    if (f.startsWith("falling knife")) return ["knife", "red"];
    if (f.startsWith("near 52-week low")) return ["52lo", "amber"];
    if (f.startsWith("near 52-week high")) return ["52hi", "amber"];
    if (f.startsWith("thin cushion")) return ["thin", "amber"];
    if (f.startsWith("strike close")) return ["atspot", "grey"];
    if (f.startsWith("IV much richer")) return ["ivrich", "grey"];
    return [f.split(" ")[0].slice(0, 7), "grey"];
  });
}

function flagsHtml(flags) {
  if (!flags || !flags.length) return '<span class="dim">—</span>';
  return shortFlags(flags)
    .map(([label, tone]) => `<span class="pill ${tone}">${esc(label)}</span>`)
    .join(" ");
}

function scoreBar(score) {
  const pctFill = Math.max(0, Math.min(100, (score / 100) * 220));
  const tone = score >= 45 ? "" : score >= 25 ? "warm" : "cool";
  return `<span class="bar ${tone}"><i style="width:${pctFill}%"></i></span>`;
}

/* ---------- toast ---------- */

function toast(message, kind = "") {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .25s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 260);
  }, 3200);
}

/* ---------- modal ---------- */

let modalStack = [];

function openModal({ title, sub, body, footer, wide = false }) {
  const root = document.getElementById("modal-root");
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML = `
    <div class="modal" ${wide ? 'style="width:min(880px,100%)"' : ""}>
      <div class="modal-head">
        <div>
          <h2>${esc(title)}</h2>
          ${sub ? `<p>${esc(sub)}</p>` : ""}
        </div>
        <button class="x-btn" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body"></div>
      ${footer ? '<div class="modal-foot"></div>' : ""}
    </div>`;

  if (typeof body === "string") {
    overlay.querySelector(".modal-body").innerHTML = body;
  } else if (body instanceof Node) {
    overlay.querySelector(".modal-body").appendChild(body);
  }
  if (footer instanceof Node) {
    overlay.querySelector(".modal-foot").appendChild(footer);
  }

  const close = () => {
    overlay.remove();
    modalStack = modalStack.filter((m) => m !== close);
  };
  overlay.querySelector(".x-btn").onclick = close;
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) close();
  });

  root.appendChild(overlay);
  modalStack.push(close);
  return { overlay, close };
}

function confirmModal({ title, sub, body, confirmLabel = "Confirm", danger = false, onConfirm }) {
  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Cancel</button>
    <button class="btn ${danger ? "danger" : "primary"}" data-act="ok">${esc(confirmLabel)}</button>`;
  const { close } = openModal({ title, sub, body, footer });
  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="ok"]').onclick = async () => {
    const btn = footer.querySelector('[data-act="ok"]');
    btn.disabled = true;
    try {
      await onConfirm(close);
    } catch (err) {
      toast(err.message, "err");
      btn.disabled = false;
    }
  };
}

/* ---------- sidebar ---------- */

function paintSidebar(book) {
  if (!book) return;
  const committed = Number(book.committed || 0);
  const budget = Number(book.budget || 0);
  document.getElementById("sb-budget").textContent = money(budget);
  // Live when the payload carries a marked book; cost-basis until quotes load.
  const totalLive = book.total_market ?? book.total;
  const totalEl = document.getElementById("sb-total");
  totalEl.textContent = money(totalLive);
  totalEl.className = book.unrealized ? signClass(book.unrealized) : "good";
  document.getElementById("sb-free").textContent = money(book.free_cash);
  document.getElementById("sb-prem").textContent = money(book.premium_collected);
  const real = document.getElementById("sb-real");
  real.textContent = moneySigned(book.realized_pnl);
  real.className = signClass(book.realized_pnl);

  const used = budget > 0 ? Math.min(100, (committed / budget) * 100) : 0;
  const meter = document.getElementById("sb-meter");
  meter.style.width = used + "%";
  meter.style.background = used > 95 ? "var(--red)" : used > 70 ? "var(--amber)" : "var(--accent)";
  // Say where the money is: cash vs collateral vs shares, and what the
  // shares would fetch right now if it all ended today.
  const parts = [];
  if (Number(book.cash || 0) !== 0 || committed > 0) parts.push(`${money(book.cash)} cash`);
  if (Number(book.collateral || 0) > 0) parts.push(`${money(book.collateral)} collateral`);
  if (Number(book.shares_cost || 0) > 0) {
    parts.push(book.shares_live !== undefined && book.shares_live !== null
      ? `${money(book.shares_live)} in shares`
      : `${money(book.shares_cost)} in shares`);
    if (book.unrealized) parts.push(`${moneySigned(book.unrealized)} open`);
  }
  document.getElementById("sb-note").textContent =
    parts.length ? parts.join(" · ") : "no capital committed";
}

/* ---------- router ---------- */

const RENDERERS = {}; // filled by views.js

async function go(view, { push = true } = {}) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view)
  );
  document.getElementById("view-title").textContent = VIEW_META[view].title;
  document.getElementById("view-sub").textContent = VIEW_META[view].sub;
  document.getElementById("project-toggle").hidden = view !== "scan";
  document.getElementById("project-checkbox").checked = state.project;

  if (push) history.replaceState({ view }, "", `#${view}`);

  const content = document.getElementById("content");
  content.innerHTML = '<div class="loading"><span class="spinner"></span>Loading…</div>';

  try {
    const data = await RENDERERS[view]();
    if (state.view !== view) return;
    state.data = data;
  } catch (err) {
    content.innerHTML = `<div class="card"><div class="empty">
      <h3>Could not load ${esc(view)}</h3>
      <p>${esc(err.message)}</p></div></div>`;
  }
}

/* ---------- boot ---------- */

function bindChrome() {
  document.getElementById("nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-item");
    if (btn) go(btn.dataset.view);
  });

  document.getElementById("refresh-btn").onclick = () => go(state.view);

  document.getElementById("project-checkbox").onchange = (e) => {
    state.project = e.target.checked;
    go("scan");
  };

  // keyboard: same letters as the TUI, but nothing depends on them
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;

    if (e.key === "Escape") {
      if (modalStack.length) {
        modalStack[modalStack.length - 1]();
        e.preventDefault();
      }
      return;
    }
    if (modalStack.length) return;

    const map = { b: "book", s: "scan", c: "calls", p: "plan", l: "journal", ",": "settings" };
    const view = map[e.key.toLowerCase()];
    if (view) {
      go(view);
      e.preventDefault();
      return;
    }
    if (e.key.toLowerCase() === "r") {
      go(state.view);
      e.preventDefault();
    }
  });
}

async function boot() {
  bindChrome();

  // paint the sidebar early so the account is visible while a view loads
  try {
    paintSidebar(await api("/api/book"));
  } catch {
    /* sidebar is cosmetic; a failure here must not block the app */
  }

  const fromHash = location.hash.replace("#", "");
  await go(VIEW_META[fromHash] ? fromHash : "book", { push: false });
}

window.addEventListener("DOMContentLoaded", boot);
window.addEventListener("hashchange", () => {
  const v = location.hash.replace("#", "");
  if (VIEW_META[v] && v !== state.view) go(v, { push: false });
});
