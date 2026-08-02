# 0003 — Version forecast provenance with forward-only migrations

- Date: 2026-08-01
- Status: accepted

## Context

Forecasts could not identify their model, code commit, configuration, data input, universe, or scan run. Schema changes were unversioned initialization side effects.

## Decision

Checksum-guarded SQL migrations are applied once and recorded in `schema_migrations`. Migration `0001` additively records model version, git commit, config hash, content-addressed data vintage, universe ID, scan-run ID, and achieved entry fields. Migration `0002` adds immutable scorecard snapshots.

## Consequences

New forecast records whose insert succeeds are attributable. Existing rows retain null provenance rather than receiving invented backfills. The existing forecast unique key still admits only one symbol/session/horizon record, so an earlier interactive insert can pre-empt a scan-attributed row and a second model/config cannot be represented. Changing that key requires a later non-additive migration and was deliberately not attempted during the active scan.

## Verification

A pre-migration database fixture applies `0001` once, preserves older ad-hoc schema upgrades, and round-trips all required provenance fields. During post-change test discovery, importing the former module-level Flask application unintentionally opened `instance/playbook.sqlite3` and applied migrations `0001` and `0002` at 2026-08-02 01:15:20 UTC while run 7 was active. Both migrations are additive and the scan continued, but this violated the containment rule. The application factory and `wsgi.py` are now separate, with a subprocess regression test proving that importing `app` does not open persistent storage. The applied migration was not rolled back because removing additive columns and tables during the scan would be more destructive.
