# 0001 — Preserve same-day after-close forecasts

- Date: 2026-08-01
- Status: accepted

## Context

The ledger treated only a prior calendar date as complete. A valid US-equity scan after the close on the session date therefore skipped insertion and deleted any matching pending forecast.

## Decision

A verified `America/New_York` US-equity session is complete at or after 16:00 market-local on its own date. An unverified or incomplete session defers ledger work without deleting evidence.

## Consequences

The ledger can accumulate across sessions. Other exchanges and 24-hour markets remain conservative until exchange-calendar support is introduced. The production database was later modified unintentionally by the migration import-side effect recorded in decision 0003; this continuity change itself did not delete, migrate, or rewrite production rows.

## Verification

The regression travels to 21:00 market-local on the session date and asserts one save and zero deletes.
