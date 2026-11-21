---
name: Wheel Strategy
description: Scan cash-secured puts and covered calls, read the trade journal, and decide the next action in a wheel position. Use for any options-selling question.
---

# Wheel Strategy

The engine is the source of truth. Your job is judgement and prose, never
arithmetic. Every number must come from a `uv run wheel --json …` call.

## Commands

```bash
uv run wheel --json status    # open positions + one next action each + free capital
uv run wheel --json scan      # ranked cash-secured puts for the watchlist
uv run wheel --json calls     # covered calls for shares held, at/above basis
uv run wheel --json journal   # full event history and realized P/L
uv run wheel --json sync      # classify expired positions — DRY RUN, writes nothing
```

Journal writes, only when the user asks for them explicitly:

```bash
uv run wheel --json open   --symbol FIG --kind put --strike 17.5 --expiry 2026-12-18 --contracts 1 --premium 1.17
uv run wheel --json close  --parent <id> --price 0.40
uv run wheel --json expire --parent <id>
uv run wheel --json assign --parent <id>
uv run wheel --json called_away --parent <id>
uv run wheel --json roll   --parent <id> --strike 15 --expiry 2027-01-15 --premium 0.62 --price 2.10
uv run wheel --json fix    --parent <id or label> --set '{"cost": 19.42}'   # actual fills
```

`sync --apply` writes events. **Never run it without the user confirming each
proposal.** Show the preview first.

All settings (budget, DTE window, profile, watchlist) live in `config.toml` and
are edited in the web UI's Settings view. Do not pass flags to change them; read
the current values from the engine output.

The user works in the web UI (`wheel --web`): the same engine behind a sidebar.
Refer to views by name — Book, Scan, Calls, Plan, Ledger, Settings — not by key.

## Reading results

`candidates` is already ranked and already gated. Do not re-sort it. If it is
empty, quote `rejected_reasons` and `per_symbol` — that is the honest answer,
not a failure. A one-name watchlist producing zero or one candidate is normal.

Weighting order: cushion_pct (room to fall), annualized_pct (pay), delta
(assignment odds), flags (event risk), iv_hv (is the vol rich vs realised).

## Rules

- Run `status` before any new recommendation.
- Never propose a put on a symbol with existing shares or an open put without
  flagging the added exposure in one sentence.
- On assignment the covered call is mandatory output, not optional.
- Free capital is `book.free_cash`. Never recommend collateral beyond it.
- When the user reports a fill price that differs from a recorded number,
  that is journal drift, not a math debate: offer to `fix` it with actual
  values (append-only) and re-read the book afterwards.
- Plan is a projection: calls listed there cover shares you do NOT own
  yet. Anything sellable today must come from `calls` / `scan` (mode
  `actual`). Never present a projected call as immediately sellable.
- In `calls` output, `sold_calls` are positions the user holds;
  `candidates` are things they could sell. Say which is which. Candidates
  are already sized to `free_shares` (unpledged) — never offer a second
  call over the same shares.
- `falling knife` / `near 52-week low` on a put means high yield is priced
  fear. Say it.
- `ex-div … early-assignment risk` on a call is urgent. Say it first.

## Output

**Pick** · **Why** · **Ticket** · **If assigned** · **Risk** · **Passed**

Then: *Not financial advice — verify with your broker before trading.*

See `references/scoring.md` for the exact weights, delta bands, and gate
thresholds if the user asks how ranking works.
