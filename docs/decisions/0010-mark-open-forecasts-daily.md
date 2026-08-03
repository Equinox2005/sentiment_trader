# 0010 — Mark open forecasts to market every trading day

- Date: 2026-08-02
- Status: accepted

## Context

The scorecard only speaks once a forecast reaches its horizon. With a 21-session
horizon and a single scan in the ledger, the entire published record is
`pending` and the site has no answer at all to "how is the current cohort
doing?" for a full trading month after each scan. The forward-return data needed
to answer it is already on disk — the nightly scan refreshes `price_bars` for
every symbol in the universe — but nothing read it between the signal date and
the horizon date.

The absence pushed users toward the board's `expected_move`, which is a
prediction, as if it were a result.

## Decision

`performance.py` marks every ledger forecast against the most recent stored
close and publishes the running average at `/performance`, alongside four
leaderboards of up to 50 names each: best and worst longs, best and worst
shorts. A leaderboard only ever holds names of its own sign, so a top table is
never padded with losses to reach a fixed length, and a truncated table states
the full population it was drawn from.

The mark uses the identical convention the grader commits to in decision 0007:
buy the open of the session after the signal, measure to a session close.
A running mark therefore converges on the graded number rather than contradicting
it, and a forecast already graded is read back from the ledger instead of
recomputed, so this page and the scorecard can never quote two different numbers
for the same settled forecast.

Three things are deliberately kept out of the average rather than hidden:

- **Neutral forecasts.** They assert no direction, so signing their price move
  would invent a call the model never made. They are counted separately.
- **Split artifacts.** Cached bars are not retroactively split-adjusted, so a
  reverse split appears as an overnight jump — a 1-for-40 split reads as a
  +3,900% gain. A single such name moves an average over a thousand forecasts
  more than the signal does. A position whose holding window contains a
  session-to-session move beyond +300% or −75% is withheld and listed by name.
- **Thin sessions.** The evaluation session is the latest date in which at least
  a fifth of tracked symbols traded, so one crypto ticker's weekend bar cannot
  advance the board a day early and mark every equity flat.

The comparator holds every symbol in the ledger over the identical window, which
answers "did picking these names beat owning all of them" rather than comparing
against a different period.

## Consequences

The site now publishes an unrealised number that moves daily and can look bad
before it looks good; the page states that open positions are marked at an
unrealised price, states the equal-weight and cost-free basis, and makes no
profit claim. It reports a mean and a median together, because a
few-thousand-name microcap universe produces a mean the median does not support.

The withheld count is a standing signal about price-cache integrity. A rising
count means stale unadjusted history, not market volatility.

The report is derived, never stored, and is cached against the forecast and
price fingerprints together, so a scan that writes new bars invalidates it
without a second write path that could disagree with the ledger.

## Verification

Unit tests pin the entry convention against a gapped open, short direction
adjustment, the open-to-matured transition, the pre-entry state, the
reverse-split withholding, stored-grade precedence including after price history
is pruned, and thin-session coverage. Service tests cover cache reuse and
invalidation on new prices. Route tests cover the populated page, the
before-first-entry state, the withheld section, and missing storage.
