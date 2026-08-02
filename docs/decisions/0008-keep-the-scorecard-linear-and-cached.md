# 0008 — Keep the scorecard linear and cached

- Date: 2026-08-02
- Status: accepted

## Context

`build_scorecard` separated still-open forecasts from expired ones with
`item not in expired`, a membership scan over a list of frozen dataclasses, and
`ScorecardService.current` reread and rescored every forecast on every request.

The cost was invisible at launch because nothing had expired yet, so the guard
never ran. It grows with the square of the ledger, and the ledger grows by the
size of the scanned universe every night. Measured at a 10% expired ratio:
10,000 rows took 0.48s, 30,000 took 4.62s, and 50,000 took 13.89s — roughly two
weeks of nightly scans before the page is visibly slow and about a month before
it is unusable.

## Decision

Separate expired forecasts by identity against a set of ids rather than by
membership over a list. Memoise the built report on a cheap ledger fingerprint
— row count, highest id, latest `created_at`, latest `graded_at`, and graded
count — together with the evaluation date and any as-of timestamp.

## Consequences

The same 50,000-row report now builds in 0.009s, and 200,000 rows in 0.035s.
Repeat views reuse the report until the ledger actually changes; any insert,
in-place claim, grade, or delete moves the fingerprint and rebuilds it.

The cache is per process and therefore not a shared production cache. It is
correctness-preserving rather than a staleness window: it never serves a report
built from a different ledger state.

## Verification

Tests assert that a second identical view does not reread storage, that
appending a forecast forces a rebuild, and that the existing point-in-time
as-of reconstruction still suppresses outcomes that had not yet occurred.
