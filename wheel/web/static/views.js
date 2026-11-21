/* View renderers. Each returns the payload it fetched, after painting. */

/* ---------- shared bits ---------- */

function tableShell(title, note, headers, rowsHtml, emptyHtml) {
  const head = headers.map((h) => `<th>${esc(h)}</th>`).join("");
  return `
    <div class="card">
      <div class="card-head">
        <span class="card-title">${esc(title)}</span>
        <span class="card-note">${note || ""}</span>
      </div>
      ${
        rowsHtml
          ? `<table><thead><tr>${head}</tr></thead><tbody>${rowsHtml}</tbody></table>`
          : `<div class="empty">${emptyHtml}</div>`
      }
    </div>`;
}

function stat(label, value, cls = "") {
  return `<div class="stat">
    <div class="stat-label">${esc(label)}</div>
    <div class="stat-value ${cls}">${value}</div>
  </div>`;
}

function reasonsHtml(reasons) {
  const entries = Object.entries(reasons || {});
  if (!entries.length) return "";
  const labels = {
    spread: "wide spread",
    delta: "delta band",
    needs: "needs cash",
    OI: "thin OI",
    no: "no price",
  };
  return `<div class="reasons">${entries
    .map(([k, v]) => `<span class="reason">${esc(labels[k] || k)} <b>${v}</b></span>`)
    .join("")}</div>`;
}

/* ---------- book ---------- */

RENDERERS.book = async () => {
  const data = await api("/api/status");
  paintSidebar(data.book);

  const rows = (data.positions || [])
    .map((r, i) => {
      const actionClass =
        r.urgency === "high" ? "neg" : r.urgency === "idle" ? "dim" : "";
      return `<tr class="clickable" data-row="${i}">
        <td class="tick">${esc(r.label)}</td>
        <td class="num">${r.dte ?? "—"}</td>
        <td class="num">${money(r.spot, 2)}</td>
        <td class="num">${r.mark ? money(r.mark, 2) : "—"}</td>
        <td class="num ${signClass(r.unrealized)}">${moneySigned(r.unrealized)}</td>
        <td class="${actionClass}">${esc(r.action)}</td>
      </tr>`;
    })
    .join("");

  const empty = `<h3>No open positions</h3>
    <p>Press <b>Scan</b> to find a cash-secured put, or add a trade you already made.</p>
    <div style="margin-top:16px"><button class="btn primary" id="empty-add">Add a position</button></div>`;

  document.getElementById("content").innerHTML =
    `<div class="stat-row">
       ${stat("Total (live)", money(data.book.total_market ?? data.book.total))}
       ${stat("Open P/L", moneySigned(data.book.unrealized), signClass(data.book.unrealized))}
       ${stat("Cash", money(data.book.cash))}
       ${stat("Open puts", data.book.open_puts)}
       ${stat("Open calls", data.book.open_calls)}
       ${stat("Premium banked", money(data.book.premium_collected))}
       ${stat("Realized P/L", moneySigned(data.book.realized_pnl), signClass(data.book.realized_pnl))}
     </div>` +
    tableShell(
      "Positions",
      `${data.positions.length} open`,
      ["Position", "DTE", "Spot", "Mark", "P/L", "Next action"],
      rows,
      empty
    );

  const addBtn = document.getElementById("empty-add");
  if (addBtn) addBtn.onclick = () => modals.addPosition();

  document.querySelectorAll("tr[data-row]").forEach((tr) => {
    tr.onclick = () => {
      const row = data.positions[Number(tr.dataset.row)];
      modals.position(row);
    };
  });

  return data;
};

/* ---------- scan ---------- */

RENDERERS.scan = async () => {
  const q = state.project ? "?project=1" : "";
  const data = await api("/api/scan" + q);
  paintSidebar(data.book);

  const rows = (data.candidates || [])
    .map((c, i) => {
      return `<tr class="clickable" data-row="${i}">
        <td class="tick">${esc(c.ticket)}</td>
        <td class="num">${c.dte}</td>
        <td class="num">${num(c.delta)}</td>
        <td class="num">${money(c.mid, 2)}</td>
        <td class="num pos">${pct(c.annualized_pct, 0, false)}</td>
        <td class="num">${pct(c.cushion_pct, 1, false)}</td>
        <td class="num">${money(c.collateral)}</td>
        <td>${scoreBar(c.score)}</td>
        <td>${flagsHtml(c.flags)}</td>
      </tr>`;
    })
    .join("");

  const cashNote =
    data.capital_mode === "projected"
      ? `projected against full budget ${money(data.capital_used)}`
      : `free cash ${money(data.capital_used)}`;

  const modeNote =
    data.capital_mode === "projected"
      ? `<div class="mode-note proj"><span>◎</span><div>
           <b>Projected ranking — not capacity.</b> Scored against the full
           ${money(data.capital_used)} budget while only ${money(data.free_cash)}
           is actually free. "Affordable" here is a wish. Untick
           <b>Project full budget</b> to see what you can sell today.</div></div>`
      : `<div class="mode-note real"><span>◎</span><div>
           <b>Ranked against real capital</b> — ${money(data.capital_used)} free
           right now. Everything listed is sellable today.</div></div>`;

  let empty = `<h3>No qualifying contract</h3>
    <p>Every contract in the window failed a gate. ${reasonsHtml(data.rejected_reasons)}</p>`;
  if (data.free_cash < 100 && data.capital_mode !== "projected") {
    empty = `<h3>No free cash</h3>
      <p>${money(data.free_cash)} is available — your open puts hold the rest.
      Tick <b>Project full budget</b> above to plan the next put anyway.</p>`;
  }

  document.getElementById("content").innerHTML =
    modeNote +
    `<div class="stat-row">
       ${stat("Capital used", money(data.capital_used), data.capital_mode === "projected" ? "warn" : "")}
       ${stat("Candidates", data.candidates.length)}
       ${stat("Profile", esc(data.profile))}
       ${stat("DTE window", `${data.dte_window[0]}–${data.dte_window[1]}d`)}
     </div>` +
    tableShell(
      "Cash-secured puts",
      cashNote,
      ["Ticket", "DTE", "Delta", "Mid", "Ann%", "Cushion", "Collateral", "Score", "Flags"],
      rows,
      empty
    ) +
    (data.candidates.length ? `<div class="card"><div class="card-head">
        <span class="card-title">Rejected</span><span class="card-note">why contracts were excluded</span>
      </div><div style="padding:14px 16px">${reasonsHtml(data.rejected_reasons)}</div></div>` : "");

  document.querySelectorAll("tr[data-row]").forEach((tr) => {
    tr.onclick = () => modals.candidate(data.candidates[Number(tr.dataset.row)]);
  });

  const errs = (data.errors || []).map((e) => `${e.symbol}: ${e.error}`).join(" · ");
  if (errs) toast(errs, "err");

  return data;
};

/* ---------- calls ---------- */

RENDERERS.calls = async () => {
  const data = await api("/api/calls");
  // The Calls view is where the assign modal lands; if the payload ever
  // lacks a fresh book, fall back to /api/book so the sidebar cannot keep
  // showing stale free cash after collateral is released.
  paintSidebar(data.book || (await api("/api/book")));

  if (data.message) {
    document.getElementById("content").innerHTML = `<div class="card"><div class="empty">
      <h3>No shares held</h3>
      <p>${esc(data.message)}</p>
      <div style="margin-top:16px"><button class="btn primary" id="empty-plan">Open Plan</button></div>
    </div></div>`;
    document.getElementById("empty-plan").onclick = () => go("plan");
    return data;
  }

  let html = "";
  const flat = [];
  for (const pos of data.positions || []) {
    if (pos.error) {
      html += `<div class="card"><div class="empty"><h3>${esc(pos.symbol)}</h3><p>${esc(pos.error)}</p></div></div>`;
      continue;
    }
    const sold = pos.sold_calls || [];
    const soldNote = sold.length
      ? `<div class="mode-note real" style="margin:4px 0 10px"><span>◎</span><div>
           <b>You already sold:</b> ${sold.map((c) => `${esc(c.label)} ×${c.contracts}`).join(" · ")} —
           those are your <em>positions</em> (Book shows the live marks).
           ${pos.pledged_shares ?? 0} of your shares pledged, ${pos.free_shares ?? pos.shares} free.</div></div>`
      : "";
    const rows = (pos.candidates || [])
      .map((c) => {
        const idx = flat.length;
        flat.push({ pos, cand: c });
        return `<tr class="clickable" data-row="${idx}">
          <td class="tick">${esc(c.ticket)}</td>
          <td class="num">${c.dte}</td>
          <td class="num">${num(c.delta)}</td>
          <td class="num">${money(c.mid, 2)}</td>
          <td class="num pos">${pct(c.return_on_shares_pct, 2, false)}</td>
          <td class="num pos">${moneySigned(c.called_away_total ?? c.called_away_gain)}</td>
          <td>${flagsHtml(c.flags)}</td>
        </tr>`;
      })
      .join("");

    const free = pos.free_shares ?? pos.shares;
    const emptyHtml = free < 100
      ? `<h3>Nothing left to cover</h3><p>All ${pos.shares} shares already stand behind
         ${sold.length} sold call(s). Buy one back or let it finish before selling another.</p>`
      : `<h3>No strikes above basis</h3><p>Nothing in the DTE window clears your ${money(pos.basis, 2)} basis.</p>`;

    html +=
      `<div class="stat-row" style="margin-top:4px">
         ${stat("Basis", money(pos.basis, 2))}
         ${stat("Shares held", pos.shares)}
         ${stat("Unpledged", free)}
         ${stat("Spot", money(pos.price, 2))}
         ${stat("Unrealized", moneySigned(pos.unrealized), signClass(pos.unrealized))}
       </div>` + soldNote +
      tableShell(
        `Sellable candidates · ${pos.symbol}`,
        `not positions — new calls you could sell, backed by ${free} unpledged shares · strikes at or above ${money(pos.basis, 2)} basis`,
        ["Ticket", "DTE", "Delta", "Mid", "On shares", "If called", "Flags"],
        rows,
        emptyHtml
      );
  }

  const soldCount = (data.book && data.book.open_calls) || 0;
  document.getElementById("content").innerHTML =
    `<div class="mode-note real"><span>◎</span><div>
       <b>Sellable candidates — not positions.</b>
       ${soldCount
         ? `You have ${soldCount} call(s) already sold; those live in <b>Book</b> with live marks. `
         : "You have not sold any calls yet. "}
       Everything listed below is a <em>new</em> call you could sell, sized only to
       shares you actually hold and haven't pledged yet.</div></div>` + html;
  document.querySelectorAll("tr[data-row]").forEach((tr) => {
    tr.onclick = () => modals.coverCall(flat[Number(tr.dataset.row)]);
  });

  return data;
};

/* ---------- plan ---------- */

RENDERERS.plan = async () => {
  const data = await api("/api/plan");
  paintSidebar(await api("/api/book"));

  if (data.message) {
    document.getElementById("content").innerHTML = `<div class="card"><div class="empty">
      <h3>Nothing to plan</h3>
      <p>${esc(data.message)}</p>
    </div></div>`;
    return data;
  }

  const totals = data.totals || {};
  let html = `<div class="mode-note proj">
      <span>◎</span>
      <div><b>Projection, not a book.</b> The shares and calls below do not exist yet —
      they are what your open puts <em>become</em> if assigned. Sellable calls live in
      <b>Calls</b>; the only real figure on this page is marked <b>real</b>.
      Recording an assignment here is correct only once your broker confirms it.</div>
    </div>
    <div class="stat-row">
      ${stat("Real · free cash", money(totals.free_cash_now))}
      ${stat("If assigned · cash", money(totals.cash_needed_if_all_assigned))}
      ${stat("If assigned · shares", totals.shares_if_all_assigned)}
      ${stat("If assigned · shortfall", money(totals.cash_shortfall),
            totals.cash_shortfall > 0 ? "neg" : "")}
    </div>`;

  const flat = [];
  for (const pos of data.positions || []) {
    if (pos.error) {
      html += `<div class="card"><div class="empty"><h3>${esc(pos.symbol)}</h3><p>${esc(pos.error)}</p></div></div>`;
      continue;
    }
    const rows = (pos.calls || [])
      .map((c) => {
        const idx = flat.length;
        flat.push({ pos, cand: c, projected: true });
        return `<tr class="clickable" data-row="${idx}">
          <td class="tick">${esc(c.ticket)}</td>
          <td class="num">${c.dte}</td>
          <td class="num">${num(c.delta)}</td>
          <td class="num">${money(c.mid, 2)}</td>
          <td class="num pos">${pct(c.return_on_shares_pct, 2, false)}</td>
          <td class="num pos">${moneySigned(c.called_away_total)}</td>
          <td>${flagsHtml(c.flags)}</td>
        </tr>`;
      })
      .join("");

    const under = pos.unrealized_at_assignment;
    html +=
      `<div class="card"><div class="card-head">
         <div>
           <div class="card-title"><span class="pill amber">if assigned</span> From ${esc(pos.from)}</div>
           <div class="card-note">hypothetical: ${pos.shares} shares at ${money(pos.basis, 2)} basis
             (strike ${money(pos.strike, 2)}) · would need ${money(pos.cash_needed)} — already
             pledged as collateral · P/L then ${moneySigned(under)}</div>
         </div>
         <button class="btn primary" data-assign="${esc(pos.from)}">Record assignment</button>
       </div>` +
      tableHtml(
        ["Ticket", "DTE", "Delta", "Mid", "On shares", "If called", "Flags"],
        rows,
        `<div class="empty"><h3>No calls above basis</h3><p>Nothing clears ${money(pos.basis, 2)} in this window.</p></div>`
      ) +
      `<div class="legend">
         <b>On shares</b> = premium ÷ what you paid per share, for this contract.
         <b>If called</b> = total profit if the shares are taken at the strike:
         (strike − ${money(pos.basis, 2)} basis) × 100 + the call premium —
         the whole wheel from here, put premium already included in the basis.
       </div>` +
      `</div>`;
  }

  document.getElementById("content").innerHTML = html;

  document.querySelectorAll("tr[data-row]").forEach((tr) => {
    tr.onclick = () => modals.coverCall(flat[Number(tr.dataset.row)]);
  });
  document.querySelectorAll("[data-assign]").forEach((btn) => {
    btn.onclick = () => modals.assignFromPlan(btn.dataset.assign, data);
  });

  return data;
};

function tableHtml(headers, rowsHtml, emptyHtml) {
  if (!rowsHtml) return emptyHtml;
  const head = headers.map((h) => `<th>${esc(h)}</th>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${rowsHtml}</tbody></table>`;
}

/* ---------- journal ---------- */

RENDERERS.journal = async () => {
  const data = await api("/api/journal");
  const book = await api("/api/book");
  paintSidebar(book);

  const rows = (data.events || [])
    .slice()
    .reverse()
    .slice(0, 200)
    .map((ev) => {
      const tone =
        ev.type === "expire" || ev.type === "called_away"
          ? "pos"
          : ev.type === "open"
          ? "warn"
          : "dim";
      return `<tr>
        <td class="num dim">${esc(String(ev.ts || "").slice(0, 16).replace("T", " "))}</td>
        <td class="${tone}">${esc(ev.type)}</td>
        <td class="dim">${esc(describeEvent(ev))}</td>
      </tr>`;
    })
    .join("");

  const stats = data.book || {};
  document.getElementById("content").innerHTML =
    `<div class="stat-row">
       ${stat("Events", data.event_count)}
       ${stat("Closed", stats.closed_positions)}
       ${stat("Wins", stats.wins, "pos")}
       ${stat("Losses", stats.losses, "neg")}
       ${stat("Realized", moneySigned(stats.realized_pnl), signClass(stats.realized_pnl))}
     </div>` +
    tableShell(
      "Events",
      "append-only · newest first",
      ["When", "Type", "Detail"],
      rows,
      `<h3>Empty ledger</h3><p>Record a trade and it appears here.</p>`
    );

  return data;
};

function describeEvent(ev) {
  const t = ev.type;
  if (t === "open") {
    const leg = ev.kind === "put" ? "P" : "C";
    return `SELL ${ev.symbol} ${ev.expiry} ${ev.strike}${leg} x${ev.contracts} @ ${ev.premium}`;
  }
  if (t === "close") return `BTC ${ev.parent} @ ${ev.price}`;
  if (t === "roll") return `ROLL ${ev.parent} -> ${ev.expiry} ${ev.strike} @ ${ev.premium}`;
  if (t === "fix") {
    if (ev.set) {
      const parts = Object.entries(ev.set).map(([k, v]) => `${k} = ${v}`).join(", ");
      return `ACTUALS ${ev.target}: ${parts}`;
    }
    return `fix: ${ev.note}`;
  }
  return `${t} ${ev.parent || ""}`.trim();
}

/* ---------- settings ---------- */

RENDERERS.settings = async () => {
  const data = await api("/api/settings");
  const s = data.settings;

  const fieldNum = (key, label, step = "1") => `
    <div class="field">
      <label>${esc(label)}</label>
      <input type="number" step="${step}" data-key="${key}" value="${esc(s[key])}">
    </div>`;

  document.getElementById("content").innerHTML = `
    <div class="card">
      <div class="card-head">
        <span class="card-title">Account</span>
        <span class="card-note">changes save immediately</span>
      </div>
      <div style="padding:16px">
        <div class="grid two">
          ${fieldNum("budget", "Budget ($)", "100")}
          <div class="field">
            <label>Risk profile</label>
            <select data-key="profile">
              ${["conservative", "balanced", "aggressive"]
                .map((p) => `<option ${p === s.profile ? "selected" : ""}>${p}</option>`)
                .join("")}
            </select>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><span class="card-title">Scan</span></div>
      <div style="padding:16px">
        <div class="grid three">
          ${fieldNum("dte_min", "DTE min (days)")}
          ${fieldNum("dte_max", "DTE max (days)")}
          ${fieldNum("top_n", "Candidates shown")}
          ${fieldNum("min_open_interest", "Min open interest")}
          ${fieldNum("max_spread_pct", "Max spread (% of mid)", "0.5")}
          ${fieldNum("risk_free_rate", "Risk-free rate", "0.005")}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="card-title">Watchlist</span>
        <span class="card-note">${s.symbols.length} symbol(s)</span>
      </div>
      <div style="padding:16px">
        <div class="chips" id="chips"></div>
        <div class="grid two" style="margin-top:14px">
          <div class="field">
            <label>Add symbol</label>
            <input type="text" id="new-symbol" placeholder="SOFI" autocomplete="off">
          </div>
          <div class="field">
            <label>&nbsp;</label>
            <button class="btn primary" id="add-symbol">Add to watchlist</button>
          </div>
        </div>
      </div>
    </div>`;

  const chips = document.getElementById("chips");
  const paintChips = (symbols) => {
    chips.innerHTML = symbols.length
      ? symbols
          .map(
            (sym) =>
              `<span class="chip">${esc(sym)}<button data-sym="${esc(sym)}" aria-label="Remove">&times;</button></span>`
          )
          .join("")
      : '<span class="dim">No symbols yet.</span>';
    chips.querySelectorAll("button").forEach((b) => {
      b.onclick = async () => {
        const next = symbols.filter((x) => x !== b.dataset.sym);
        await saveSettings({ symbols: next });
        paintChips(next);
        toast(`${b.dataset.sym} removed`, "ok");
      };
    });
  };
  paintChips(s.symbols);

  document.querySelectorAll("[data-key]").forEach((input) => {
    input.onchange = async () => {
      const key = input.dataset.key;
      let value = input.value;
      if (input.type === "number") value = Number(value);
      try {
        await saveSettings({ [key]: value });
        toast(`${key} saved`, "ok");
      } catch (err) {
        input.classList.add("err");
        toast(err.message, "err");
      }
    };
  });

  const addSymbol = async () => {
    const input = document.getElementById("new-symbol");
    const sym = input.value.trim().toUpperCase();
    if (!sym) return;
    if (s.symbols.includes(sym)) {
      toast(`${sym} is already on the watchlist`, "err");
      return;
    }
    const next = [...s.symbols, sym];
    try {
      await saveSettings({ symbols: next });
      s.symbols = next;
      paintChips(next);
      input.value = "";
      toast(`${sym} added`, "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  };
  document.getElementById("add-symbol").onclick = addSymbol;
  document.getElementById("new-symbol").onkeydown = (e) => {
    if (e.key === "Enter") addSymbol();
  };

  return data;
};

async function saveSettings(patch) {
  const res = await api("/api/settings", { method: "POST", body: patch });
  await api("/api/book").then(paintSidebar);
  return res;
}
