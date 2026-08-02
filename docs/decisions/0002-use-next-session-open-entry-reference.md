# 0002 — Use the next session open as the ledger entry reference

- Date: 2026-08-01
- Status: accepted

## Context

The completed close both generated the signal and served as its entry. Grading independently recreated the same biased close even when the payload contained another value.

## Decision

At signal time the ledger records the signal close only as context, leaves the fill price unset, and declares `{session_offset: 1, price_field: Open}` as its entry reference. When enough history arrives, grading uses that exact next-session open and records its date and price.

## Consequences

No forecast is filled on its own signal bar. Spread, slippage, market impact, partial fills, holidays, and gap-aware order behavior remain Phase 2 work; this reference is not a complete fill model.

## Verification

Leakage tests separately cover persistence and realized-return grading.
