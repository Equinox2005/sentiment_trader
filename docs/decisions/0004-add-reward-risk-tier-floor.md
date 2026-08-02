# 0004 — Add a reward/risk tier-eligibility floor

- Date: 2026-08-01
- Status: accepted

## Context

Reward/risk was displayed but did not constrain strong or moderate tiers. The score was indirectly risk-sensitive through expected and adverse moves, so changing score weights would have been a recalibration without a portfolio truth measure.

## Decision

Keep the opportunity-score formula byte-for-byte equivalent in its arithmetic. Require raw reward/risk of at least 1.0 for strong or moderate eligibility; otherwise retain the name as speculative when other board gates pass.

## Consequences

Published partial run 6 would demote 33 of its 45 strong/moderate names, matching the charter snapshot. Ordering scores do not change. Weight calibration remains deferred until the portfolio backtester exists.

## Verification

Golden tests hold low-R/R candidate scores at 67.0 and 62.0 while requiring speculative tiers.
