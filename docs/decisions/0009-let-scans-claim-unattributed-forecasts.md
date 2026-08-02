# 0009 — Let scans claim unattributed forecasts

- Date: 2026-08-02
- Status: accepted

## Context

Decision 0003 recorded a known contamination path and deferred it. The
`forecasts` unique key admits one row per symbol, session, and horizon, and
inserts ignore conflicts. An interactive lookup earlier in the day therefore
kept its record and the nightly scan's attributed row was silently discarded.

The consequence is that casually browsing the site changes what the scorecard
measures. The retained row carries no scan run, no universe, and no model
version, so it is both unattributable and unrepresentative of the board the
record is supposed to describe.

## Decision

Keep the unique key — rebuilding it would require a non-additive migration
against a multi-hundred-megabyte production table. Instead, give scans
precedence over unattributed records. When an insert conflicts and the caller
supplies a scan run, the existing row is claimed only if it is still pending
and has no scan run of its own. Its payload, horizon date, and full provenance
are replaced with the scan's.

An interactive lookup never overwrites a scan's record, and no path rewrites a
graded one.

## Consequences

The nightly board and the scorecard now describe the same forecasts. Browsing
before a scan no longer changes the record.

One row per symbol, session, and horizon remains the limit, so two models or
configurations still cannot be represented for the same slot. Separating those
requires the non-additive migration that decision 0003 deferred.

## Verification

Tests cover a scan claiming an unattributed interactive row, an interactive
save failing to overwrite a scan's row, and a scan failing to rewrite graded
evidence.
