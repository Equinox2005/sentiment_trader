# 0005 — Govern the global forecast scorecard

- Date: 2026-08-01
- Status: accepted

## Context

The site had no honest aggregate record. Raw stock returns invert successful shorts, overlapping cohorts are dependent, and the only current cohort has not matured.

## Decision

The scorecard:

- converts stock returns to signal returns by preserving longs and negating shorts;
- evaluates direction at each forecast's stored base-rate boundary;
- shows mean, median, sample standard deviation, and a cohort-cluster bootstrap interval;
- direction-adjusts the exact same-window equal-weight universe benchmark for each side;
- compares benchmark and signal means only on paired forecasts and displays benchmark sample and constituent coverage;
- breaks out side, tier, horizon, and cohort;
- separates pending, matured, and expired-ungraded records;
- suppresses all headline metrics below 30 matured long/short forecasts;
- appends at most one immutable snapshot per completed or partial scan; and
- reconstructs delayed snapshots at the scan completion timestamp rather than admitting future ledger state.

## Consequences

The current display correctly contains counts but no return headline. The page states inline that the metric is an average per-forecast price move, not a portfolio return, and excludes costs, sizing, capital limits, and overlap.

## Verification

Tests cover the short sign, a hand-computed mixed fixture, exact benchmark dates, paired benchmark coverage, retained universe membership, the base-rate boundary, suppression, due-date maturity accounting, delayed as-of reconstruction, all breakdowns, and snapshot immutability.
