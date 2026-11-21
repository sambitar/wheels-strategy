/* Modals: detail panes and every action that writes to the journal. */

const modals = {};

/* ---------- helpers ---------- */

function kv(pairs) {
  return `<dl class="kv">${pairs
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
    .join("")}</dl>`;
}

function flagList(flags) {
  if (!flags || !flags.length) return '<span class="dim">none</span>';
  return flags.map((f) => `<div style="margin:2px 0">• ${esc(f)}</div>`).join("");
}

/* ---------- add a position by hand ---------- */

modals.addPosition = (prefill = {}) => {
  const body = document.createElement("div");
  body.innerHTML = `
    <div class="grid two">
      <div class="field">
        <label>Symbol</label>
        <input type="text" id="m-symbol" placeholder="FIG" style="text-transform:uppercase"
               value="${esc(prefill.symbol || "")}" autocomplete="off">
      </div>
      <div class="field">
        <label>Type</label>
        <select id="m-kind">
          <option value="put" ${prefill.kind !== "call" ? "selected" : ""}>Sold put</option>
          <option value="call" ${prefill.kind === "call" ? "selected" : ""}>Sold call</option>
        </select>
      </div>
      <div class="field">
        <label>Strike</label>
        <input type="number" step="0.5" id="m-strike" placeholder="17.5" value="${esc(prefill.strike ?? "")}">
      </div>
      <div class="field">
        <label>Contracts</label>
        <input type="number" step="1" min="1" id="m-contracts" value="${esc(prefill.contracts ?? 1)}">
      </div>
      <div class="field">
        <label>Expiry</label>
        <input type="date" id="m-expiry" value="${esc(prefill.expiry || "")}">
      </div>
      <div class="field">
        <label>Premium per share</label>
        <input type="number" step="0.01" id="m-premium" placeholder="1.17"
               value="${esc(prefill.premium ?? "")}">
      </div>
    </div>
    <p class="dim" style="margin:14px 0 0;font-size:12px">
      For a trade you already made. Everything is editable before you confirm.
    </p>`;

  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Cancel</button>
    <button class="btn primary" data-act="ok">Add position</button>`;

  const { close } = openModal({
    title: "Add a position",
    sub: "Record a trade made at your broker",
    body,
    footer,
  });

  const val = (id) => body.querySelector(`#${id}`).value.trim();
  const markErr = (id) => {
    const el = body.querySelector(`#${id}`);
    el.classList.add("err");
    el.focus();
    return el;
  };

  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="ok"]').onclick = async () => {
    body.querySelectorAll(".err").forEach((e) => e.classList.remove("err"));

    const symbol = val("m-symbol").toUpperCase();
    if (!symbol) return markErr("m-symbol");

    const strike = Number(val("m-strike"));
    if (!(strike > 0)) return markErr("m-strike");

    const contracts = Number(val("m-contracts"));
    if (!(contracts >= 1)) return markErr("m-contracts");

    const expiry = val("m-expiry");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return markErr("m-expiry");

    const premium = Number(val("m-premium") || 0);
    if (premium < 0) return markErr("m-premium");

    try {
      const res = await api("/api/trades/open", {
        method: "POST",
        body: { symbol, kind: val("m-kind"), strike, expiry, contracts, premium },
      });
      if (res.duplicate) {
        toast(res.message, "err");
        return;
      }
      toast(`Recorded ${symbol} ${expiry} ${strike} x${contracts}`, "ok");
      if (res.warning) toast(res.warning, "err");
      close();
      go(state.view);
    } catch (err) {
      toast(err.message, "err");
    }
  };
};

/* ---------- candidate detail (from Scan) ---------- */

modals.candidate = (c) => {
  const body = `
    <div class="stat-row">
      ${stat("Score", c.score)}
      ${stat("Annualized", pct(c.annualized_pct, 0, false), "pos")}
      ${stat("Cushion", pct(c.cushion_pct, 1, false))}
      ${stat("Delta", num(c.delta))}
    </div>
    ${kv([
      ["Spot", money(c.spot, 2)],
      ["Mid", money(c.mid, 2)],
      ["Bid / ask", `${num(c.bid)} / ${num(c.ask)}`],
      ["Spread", `${num(c.spread_pct, 1)}%`],
      ["Implied vol", `${num(c.iv * 100, 0)}%`],
      ["Open interest", Number(c.oi).toLocaleString()],
      ["DTE", c.dte],
      ["Breakeven", money(c.breakeven, 2)],
      ["Collateral / contract", money(c.collateral)],
      ["Affordable", `${c.contracts_affordable} contract(s)`],
      ["IV vs realized", `${num(c.iv_hv)}×`],
    ])}
    <div style="margin-top:16px">
      <div class="stat-label" style="margin-bottom:6px">Flags</div>
      ${flagList(c.flags)}
    </div>
    <div style="margin-top:16px">
      <div class="stat-label" style="margin-bottom:6px">Score breakdown</div>
      ${kv(Object.entries(c.scores || {}).map(([k, v]) => [k, v]))}
    </div>`;

  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Close</button>
    <button class="btn primary" data-act="record">Record this trade</button>`;
  const { close } = openModal({
    title: c.ticket,
    sub: "cash-secured put",
    body,
    footer,
    wide: true,
  });
  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="record"]').onclick = () => {
    close();
    modals.addPosition({
      symbol: c.symbol,
      kind: c.kind,
      strike: c.strike,
      expiry: c.expiry,
      contracts: Math.max(1, c.contracts_affordable),
      premium: c.mid,
    });
  };
};

/* ---------- covered call detail ---------- */

modals.coverCall = ({ pos, cand, projected = false }) => {
  const warnBox = projected ? `
    <div style="margin-top:16px;padding:10px 14px;border-radius:8px;
                background:var(--amber-dim);border:1px solid var(--amber);
                font-size:13px;line-height:1.5;color:var(--amber)">
      <b>These shares do not exist yet.</b> This call is sellable only <em>after</em>
      the assignment is real. Recording it now books an uncovered leg — the
      ledger will warn you, exactly as it warns about over-commitment.
    </div>` : "";
  const body = `
    <div class="stat-row">
      ${stat("Return on shares", pct(cand.return_on_shares_pct, 2, false), "pos")}
      ${stat("If called", moneySigned(cand.called_away_total ?? cand.called_away_gain), "pos")}
      ${stat("Delta", num(cand.delta))}
      ${stat("DTE", cand.dte)}
    </div>
    ${kv([
      ["Cost basis", money(pos.basis, 2)],
      ["Spot", money(pos.price, 2)],
      ["Unrealized", moneySigned(pos.unrealized)],
      ["Shares", pos.shares],
      ["Strike", money(cand.strike, 2)],
      ["Above basis by", money(cand.strike - pos.basis, 2)],
      ["Mid", money(cand.mid, 2)],
      ["Premium", money(cand.mid * 100 * Math.max(1, cand.contracts_affordable))],
      ["Contracts", Math.max(1, cand.contracts_affordable)],
    ])}
    <div style="margin-top:16px">
      <div class="stat-label" style="margin-bottom:6px">Flags</div>
      ${flagList(cand.flags)}
    </div>${warnBox}`;

  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Close</button>
    <button class="btn ${projected ? "" : "primary"}" data-act="record">${projected ? "Record anyway (uncovered)" : "Record this call"}</button>`;
  const { close } = openModal({
    title: cand.ticket,
    sub: projected
      ? `candidate IF assigned — these shares do not exist yet`
      : `candidate — you would sell this against your ${pos.shares} ${pos.symbol} shares (${pos.free_shares ?? pos.shares} unpledged)`,
    body,
    footer,
    wide: true,
  });
  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="record"]').onclick = () => {
    close();
    modals.addPosition({
      symbol: pos.symbol,
      kind: "call",
      strike: cand.strike,
      expiry: cand.expiry,
      contracts: Math.max(1, cand.contracts_affordable),
      premium: cand.mid,
    });
  };
};

/* ---------- edit what really happened (append-only fixes) ---------- */

function fixModal({ title, sub, fields, onSubmit }) {
  const body = document.createElement("div");
  body.innerHTML = `
    <div class="grid two">
      ${fields.map(([name, label, value, step, type]) => `
        <div class="field">
          <label>${esc(label)}</label>
          <input type="${type || "number"}" step="${step || "any"}"
                 data-f="${esc(name)}" value="${esc(value)}">
        </div>`).join("")}
    </div>
    <p class="dim" style="margin:12px 0 0;font-size:12px">
      Enter what your broker <b>actually</b> filled. The journal keeps the
      original ticket and layers this correction on top — collateral, basis,
      cost, realized P/L and your total all recompute from the truth.
    </p>`;
  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Cancel</button>
    <button class="btn primary" data-act="ok">Save actuals</button>`;
  const { close } = openModal({ title, sub, body, footer });
  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="ok"]').onclick = async () => {
    const vals = {};
    body.querySelectorAll("[data-f]").forEach((el) => { vals[el.dataset.f] = el.value.trim(); });
    const btn = footer.querySelector('[data-act="ok"]');
    btn.disabled = true;
    try {
      await onSubmit(vals);
      close();
      go(state.view);
    } catch (err) {
      toast(err.message, "err");
      btn.disabled = false;
    }
  };
}

modals.fixPosition = (row) => fixModal({
  title: "Edit actual values",
  sub: row.label,
  fields: [
    ["premium", "Premium per share (actual)", row.premium, "0.01"],
    ["strike", "Strike (actual)", row.strike, "0.01"],
    ["contracts", "Contracts", row.contracts, "1"],
    ["expiry", "Expiry (actual)", row.expiry, null, "date"],
  ],
  onSubmit: async (v) => {
    await api("/api/trades/fix", {
      method: "POST",
      body: { id: row.id, set: {
        premium: Number(v.premium), strike: Number(v.strike),
        contracts: Number(v.contracts), expiry: v.expiry,
      } },
    });
    toast("Actuals saved — book recomputed", "ok");
  },
});

modals.fixLot = (row) => fixModal({
  title: "What you really paid",
  sub: row.label,
  fields: [
    ["cost", "Cost per share (actual)", row.cost ?? row.basis, "0.0001"],
    ["shares", "Shares actually received", row.shares ?? row.contracts * 100, "1"],
  ],
  onSubmit: async (v) => {
    if (!row.ids || row.ids.length !== 1) throw new Error("lot came from several puts; fix one at a time");
    await api("/api/trades/fix", {
      method: "POST",
      body: { id: row.ids[0], set: { cost: Number(v.cost), shares: Number(v.shares) } },
    });
    toast("Actual purchase saved", "ok");
  },
});

/* ---------- open position actions ---------- */

modals.position = (row) => {
  if (row.kind === "shares") {
    modals.shares(row);
    return;
  }
  const body = `
    <div class="stat-row">
      ${stat("DTE", row.dte ?? "—")}
      ${stat("Spot", money(row.spot, 2))}
      ${stat("Mark", row.mark ? money(row.mark, 2) : "—")}
      ${stat("P/L", moneySigned(row.unrealized), signClass(row.unrealized))}
    </div>
    ${kv([
      ["Contracts", row.contracts],
      ["Premium received", money(row.premium, 2)],
      ["Strike", money(row.strike, 2)],
      ["Expiry", row.expiry],
      ["In the money", row.itm ? "yes" : "no"],
    ])}
    <div style="margin-top:16px;padding:12px 14px;border-radius:8px;
                background:var(--panel-2);border:1px solid var(--line)">
      <div class="stat-label" style="margin-bottom:4px">Next action</div>
      <div class="${row.urgency === "high" ? "neg" : ""}">${esc(row.action)}</div>
    </div>
    <p class="dim" style="margin:12px 0 0;font-size:12.5px">
      Numbers off from your broker? <a href="#" id="m-fix-pos">Edit actual values</a> —
      the original ticket stays, the fix rides on top.
    </p>`;

  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="btn-close">Buy to close</button>
    <button class="btn" data-act="expire">Expired</button>
    <button class="btn" data-act="called">Called away</button>
    <button class="btn primary" data-act="assign">Assigned</button>`;

  const { overlay, close } = openModal({ title: row.label, sub: "open position", body, footer });

  overlay.querySelector("#m-fix-pos").onclick = (e) => {
    e.preventDefault();
    close();
    modals.fixPosition(row);
  };

  footer.querySelector('[data-act="btn-close"]').onclick = () => {
    close();
    modals.closePosition(row);
  };
  footer.querySelector('[data-act="expire"]').onclick = () => {
    close();
    confirmModal({
      title: "Expired worthless?",
      sub: row.label,
      body: `<p>Mark this position as expired. You keep the ${money(row.premium * 100 * row.contracts)} premium.</p>`,
      confirmLabel: "Mark expired",
      onConfirm: async (done) => {
        await api("/api/trades/expire", { method: "POST", body: { id: row.id } });
        toast(`${row.label} expired`, "ok");
        done();
        go(state.view);
      },
    });
  };
  footer.querySelector('[data-act="called"]').onclick = () => {
    close();
    confirmModal({
      title: "Shares called away?",
      sub: row.label,
      body: `<p>Deliver ${row.contracts * 100} shares against this call.</p>`,
      confirmLabel: "Called away",
      onConfirm: async (done) => {
        await api("/api/trades/called-away", { method: "POST", body: { id: row.id } });
        toast("Shares called away", "ok");
        done();
        go(state.view);
      },
    });
  };
  footer.querySelector('[data-act="assign"]').onclick = () => {
    close();
    modals.assign(row);
  };
};

modals.assign = (row) => {
  const shares = row.contracts * 100;
  const basis = row.strike - row.premium;
  confirmModal({
    title: "Assignment",
    sub: row.label,
    body: `
      <p>You will own <b>${shares} shares</b> at <b>${money(basis, 2)}</b> cost basis
      (${money(row.strike, 2)} strike less ${money(row.premium, 2)} premium).</p>
      <p class="dim" style="font-size:12.5px">
        Covered calls become available immediately, filtered to strikes at or above this basis.
      </p>`,
    confirmLabel: `Own ${shares} shares`,
    onConfirm: async (done) => {
      const res = await api("/api/trades/assign", {
        method: "POST",
        body: { id: row.id, symbol: row.symbol },
      });
      toast(`Assigned — basis ${money(res.basis, 2)}`, "ok");
      done();
      go("calls");
    },
  });
};

modals.closePosition = (row) => {
  const body = document.createElement("div");
  body.innerHTML = `
    <div class="field">
      <label>Price paid per share to buy back</label>
      <input type="number" step="0.01" id="c-price" placeholder="0.40" value="${num(row.mark || 0)}">
    </div>
    <p class="dim" style="margin:12px 0 0;font-size:12px">
      You received ${money(row.premium, 2)}; buying back at that price realizes
      the difference × 100 × ${row.contracts}.
    </p>`;
  const footer = document.createElement("div");
  footer.innerHTML = `
    <button class="btn ghost" data-act="cancel">Cancel</button>
    <button class="btn primary" data-act="ok">Close position</button>`;
  const { close } = openModal({ title: "Buy to close", sub: row.label, body, footer });

  footer.querySelector('[data-act="cancel"]').onclick = close;
  footer.querySelector('[data-act="ok"]').onclick = async () => {
    const price = Number(body.querySelector("#c-price").value);
    if (!(price >= 0)) {
      toast("Enter a valid price", "err");
      return;
    }
    try {
      await api("/api/trades/close", { method: "POST", body: { id: row.id, price } });
      toast(`Closed at ${money(price, 2)}`, "ok");
      close();
      go(state.view);
    } catch (err) {
      toast(err.message, "err");
    }
  };
};

modals.shares = (row) => {
  const body = `
    <div class="stat-row">
      ${stat("Shares", row.shares ?? row.contracts * 100)}
      ${stat("Cost (actual)", money(row.cost ?? row.basis, 2))}
      ${stat("Basis (net)", money(row.basis, 2))}
      ${stat("Spot", money(row.spot, 2))}
      ${stat("Unrealized", moneySigned(row.unrealized), signClass(row.unrealized))}
    </div>
    <div style="padding:12px 14px;border-radius:8px;background:var(--panel-2);border:1px solid var(--line)">
      <div class="stat-label" style="margin-bottom:4px">Next action</div>
      <div>${esc(row.action)}</div>
    </div>`;
  const footer = document.createElement("div");
  const fixable = row.ids && row.ids.length === 1;
  footer.innerHTML = `
    <button class="btn ghost" data-act="fix">Edit actual purchase</button>
    <button class="btn primary" data-act="calls">Sell a covered call</button>`;
  const { close } = openModal({ title: row.label, sub: "shares held", body, footer });
  const fixBtn = footer.querySelector('[data-act="fix"]');
  if (!fixable) {
    fixBtn.disabled = true;
    fixBtn.title = "lot blends several assignments — edit via the ledger";
  } else {
    fixBtn.onclick = () => {
      close();
      modals.fixLot(row);
    };
  }
  footer.querySelector('[data-act="calls"]').onclick = () => {
    close();
    go("calls");
  };
};

/* ---------- assign straight from the plan screen ---------- */

modals.assignFromPlan = (label, planData) => {
  const pos = (planData.positions || []).find((p) => p.from === label);
  if (!pos) return;
  const shares = pos.shares;
  confirmModal({
    title: "Already assigned?",
    sub: label,
    body: `<p>This records the assignment now: <b>${shares} shares</b> at
      <b>${money(pos.basis, 2)}</b> basis, ${money(pos.cash_needed)} of cash.</p>
      <p class="dim" style="font-size:12.5px">Only do this once your broker confirms it.</p>`,
    confirmLabel: `Own ${shares} shares`,
    onConfirm: async (done) => {
      await api("/api/trades/assign", {
        method: "POST",
        body: { id: pos.id || pos.from, symbol: pos.symbol },
      });
      toast("Assigned — covered calls ready", "ok");
      done();
      go("calls");
    },
  });
};
