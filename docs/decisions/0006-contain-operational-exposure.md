# 0006 — Contain operational exposure in Phase 0

- Date: 2026-08-01
- Status: accepted

## Context

The Docker context included the local database, tests had no push CI, dependencies floated, expensive public analysis was unbounded, a scan secret was accepted in URLs, and product copy overstated the default universe.

## Decision

Exclude runtime databases, secrets, and logs from Docker; run the full unittest discovery on pushes and pull requests; install a Python 3.12-resolved hash lock in CI, Docker, and hosted builds; accept the scan secret only through its header; apply per-process sliding-window limits to public analysis with a stricter refresh bucket; trust forwarded client addresses only behind an explicitly configured number of proxy hops; and name the default universe as Nasdaq-listed common stocks plus current S&P 500 constituents.

## Consequences

The immediate exposure is reduced. Rate limits are process-local and therefore not a substitute for a shared production limiter. Container base-image digests and GitHub Action SHAs remain future hardening items.

## Verification

Repository tests enforce Docker patterns, workflow triggers and command, exact/hash-locked dependencies, and scope-correct copy. App tests enforce `429` behavior, configured-proxy client isolation, scorecard throttling, and rejection of query-string scan secrets.
