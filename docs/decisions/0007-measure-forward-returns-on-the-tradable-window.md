# 0007 — Measure forward returns on the tradable window

- Date: 2026-08-02
- Status: accepted

## Context

Decision 0002 moved the ledger entry to the open of the session after the
signal, and grading now measures `open[t+1] -> close[t+horizon]`. The forecast
engine was not moved with it. Analog outcomes, baseline up-rates, and
walk-forward targets all still measured `close[t] -> close[t+horizon]`.

The displayed prediction was therefore calibrated on a window that nobody could
trade, and graded on a different one. The measured window excluded the
signal-close-to-next-open gap — the move a reaction signal is most likely to be
anticipating — and ran roughly one session shorter. Every calibration number
(`probability_up`, `edge_points`, `range_21d` coverage, Brier skill, interval
coverage) scored a different random variable than the model produced.

## Decision

`ENTRY_SESSION_OFFSET` fixes one entry convention for the whole engine: a
signal formed from the completed close of session `t` is filled at the open of
session `t + 1` and exited at the close of session `t + horizon`.
`_entry_price_at` is the single place that resolves it, falling back to the
close of that same session when an open is missing so the fill still lands
strictly after the signal bar.

Analog outcomes, intraday excursions, baseline up-rates, walk-forward targets,
and the strategy audit all measure from that fill. Episodes with no usable
session after them are dropped rather than silently priced at their own close.

## Consequences

Every forecast number changes. Realized windows no longer contain the overnight
gap, so measured edge and excursions are smaller and intervals are narrower
relative to the previous close-to-close basis. That reduction is the bias being
removed, not a regression.

The 2,801 forecasts already in the ledger were produced under the old basis and
will be graded under the new one. They carry a null `model_version` and are not
backfilled or deleted; the scorecard now breaks out by model version so that
pre-alignment cohort stays separable from everything recorded afterwards and
cannot silently blend into the first headline.

This is an entry reference, not a fill model. Spread, slippage, market impact,
partial fills, and gap-through stop behaviour remain Phase 2 work.

## Verification

A regression gaps the fill 10% away from the signal close and asserts both the
vectorised and non-vectorised match paths report the return measured from that
fill, with the signal bar flat at index 0. A second test covers the missing-open
fallback and the absent final session.
