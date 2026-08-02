# Playbook Research Charter and Operating Instructions

**Repository:** `sentiment_trader` · **Application:** Playbook
**Document status:** Canonical. This file governs the project. Where this document and any other instruction conflict, this document wins until it is amended by a recorded decision.
**Version:** 1.1 · **Adopted:** 2026-08-01
**Amendment 1.1:** Added Phase 0B (Daily Value Track) — ledger continuity, reward/risk coherence floor, and a site-wide algorithm scorecard with a direction-adjusted average return recomputed on every scan. Added §E.8 and a scorecard row to the vocabulary table governing how that average may be described.

---

## Canonical Goal

Turn Playbook from a promising research prototype into a research-grade forecasting and portfolio system whose central claim is not accuracy but **reproducible, risk-adjusted, net-of-cost portfolio return on data the system has never seen** — established first in a point-in-time historical portfolio backtest with achievable fills, then on a permanently frozen holdout, then in forward paper trading with no backfills, and only then with a small, governed allocation of real capital.

The system must be able to answer, for any published number: *which commit produced it, from which data vintage, over which universe, under which execution assumptions, and how likely is it to be luck?* Any result that cannot answer all five is a research note, not a claim.

Success is defined by all of the following, in priority order:

1. **Net monetary performance.** Positive risk-adjusted return after conservative commissions, spread, slippage, market impact, borrow, and taxes where applicable — measured on a portfolio replay of the actual nightly ranked board, not on per-symbol summaries.
2. **Scientific integrity.** Zero look-ahead, zero same-close execution, zero survivorship bias, dependence-aware statistics, multiple-testing control, and an untouched final holdout. Unfavorable results are published as readily as favorable ones.
3. **Reproducibility.** Any run reconstructable from `(git commit, config hash, data vintage, universe id, seed)` alone.
4. **Risk control.** Predefined limits, kill switches, reconciliation, and manual promotion gates that no automated process can bypass.
5. **Staged live validation.** Paper before pilot, pilot before scale, each with a written, pre-registered gate.

Explicitly out of scope as goals: maximizing headline directional accuracy, maximizing board size, impressive-looking equity curves, and any claim of guaranteed or expected profit to any user. **The project never promises profits.** Scientific honesty is a product feature, not a constraint on one.

---

## Standing Instructions

These apply to every turn, every phase, indefinitely.

### A. Non-negotiable integrity rules

1. **No look-ahead, ever.** Every feature, label, filter, and eligibility decision must be computable from information available strictly before its own signal timestamp. When in doubt, assume the information was not available.
2. **No same-bar execution.** A signal formed from a completed close may never be filled at that close. Entry is the next session at the earliest, at a price that a real order could have received.
3. **The holdout is sacred.** The final holdout is defined once, written down, hashed, and never used for tuning, feature selection, threshold setting, hyperparameter choice, or "just checking." Looking at it burns it. A burned holdout must be replaced by a new one carved only from data arriving after the burn.
4. **Measure the decision actually made.** If direction is defined as edge relative to an asset's base rate, accuracy must be scored against that same boundary — never against a 50% boundary that no rule uses. Metrics follow decisions; decisions do not get rescored to look better.
5. **No cherry-picking.** Universes, periods, symbols, and cost assumptions are fixed before results are viewed. Every excluded symbol or period is logged with a reason that would have been valid ex ante.
6. **Report the full prediction that is displayed.** If current news moves the published probability, then either historical validation replays point-in-time news, or the news adjustment is removed from the audited path and clearly separated in the UI as unaudited. A displayed prediction that differs from the audited prediction is a defect.
7. **Negative results ship.** If a component fails to beat its baseline, that is a finding. Record it in the decisions log and remove or gate the component. Never quietly retune until it passes.
8. **No result is "tradable" without the Strategy Contract.** Any number described as a return, edge, or performance figure must name the contract version it obeys.
9. **Never use real money before the pilot gate.** No exceptions, no manual overrides, no "small test."
10. **Do not make personalized investment recommendations to users** and do not represent published boards as advice. The product publishes research output with stated uncertainty and stated limitations.

### B. Research rules

1. Build the truth-measurement foundation before any model sophistication. A better model measured badly is worth less than a weak model measured correctly.
2. Every new component must beat a stated simple baseline (buy-and-hold, cash, market-neutral, momentum, mean reversion, ridge/logistic, gradient-boosted trees) on frozen data, and survive ablation, cost sensitivity, stability, and capacity tests. **Complexity is not evidence.**
3. Use rolling-origin nested walk-forward: separate training, model-selection, calibration, and evaluation windows. Purge and embargo wherever labels overlap.
4. Treat overlapping horizons as dependent samples. Report effective sample size alongside nominal trade count. Never let overlapping checkpoints inflate a skill score or a validation grade.
5. Use dependence-aware confidence intervals (stationary/block bootstrap or equivalent). Report multiple-testing-adjusted significance, deflated Sharpe or equivalent selection-bias diagnostics, and probability of backtest overfitting where the search space warrants it.
6. Report breakdowns by long/short, sector, liquidity tier, market cap, regime, and subperiod. A result that exists in only one bucket is a hypothesis, not an edge.
7. Sensitivity before celebration: re-run at 1×, 2×, and 3× the modeled cost and slippage. An edge that dies at 2× costs is not an edge.
8. Prefer a smaller honest edge over a larger contaminated one. When a fix reduces measured performance, that is the system getting more truthful; record the before/after and move on.

### C. Engineering rules

1. **Preserve the working UX and scanner behavior.** Replace foundations incrementally behind interfaces. No high-risk rewrite unless a written ADR explains why migration is safer than gradual replacement.
2. Decompose the flat monolith (`playbook.py`, `market_data.py`, `storage.py`, `scanner.py` are each 40–75 KB) into typed packages with explicit interfaces — but only as each area is touched for other reasons. Do not perform a cosmetic mass move.
3. Typed domain models at every boundary. No dictionaries crossing module lines where a dataclass belongs.
4. Versioned, forward-only database migrations. The current ad-hoc `PRAGMA table_info` + `ALTER TABLE` pattern in `storage.py` is replaced, not extended.
5. Test CI on every change: unit, property, integration, data-quality, **leakage**, and golden-snapshot tests. Coverage reported with a threshold that ratchets upward and never downward.
6. Ruff, formatter, and type checking in CI. Deterministic dependency locking with hashes — the current unpinned ranges in `requirements.txt` are not reproducible.
7. Durable background workers separate from the web process. Structured logs, metrics, tracing, alerting. Database backups with rehearsed restore drills. Disk and scanner-health monitoring with alarms.
8. Rate limiting on all public analysis and refresh endpoints. Secrets in headers only, never query strings. Security headers, secret scanning, dependency auditing.
9. The Docker context must exclude databases, secrets, and local runtime artifacts.
10. Every artifact that a claim depends on is content-addressed and retained. Detailed scan history must not be silently pruned out from under a published result.

### D. Risk rules

1. Paper trading precedes capital. A predefined, small initial capital limit precedes scale.
2. Enforce per-position, per-sector, gross, net, liquidity, and drawdown limits in code, not in intent.
3. Kill switches, stale-data protection, duplicate-order protection, and broker reconciliation are prerequisites for any order path — paper or live.
4. Manual approval gates for every promotion. No automatic promotion on a favorable short sample, ever.
5. No averaging down, no leverage expansion, no position sizing outside the written policy.
6. Every operational incident is logged with cause, blast radius, and resolution. Unresolved incidents block promotion.

### E. Communication and reporting rules

1. **Vocabulary is governed.** The words *accurate*, *profitable*, *validated*, *paper-traded*, and *live* may be used only when the corresponding evidence in "Acceptance Gates" exists. Until then use *measured in-sample*, *preliminary*, *unaudited*, or *not yet established*.
2. Product copy must match reality. "Every listed stock" is false while the default universe is Nasdaq + S&P 500; fix the copy or fix the universe.
3. Every report states: sample period, universe, trade count, effective independent sample, cost assumptions, benchmark, and the known limitations that remain.
4. Lead with the limitation that would most embarrass the result if a skeptic found it first.
5. Never present a backtest number without its contract version and data vintage adjacent to it.
6. When reporting progress, state what was verified by execution versus what was only written. Distinguish "tests pass" from "behavior confirmed on real data."
7. Optimize every artifact for a hostile reader: a quantitative researcher, an auditor, or a regulator should be able to attack it without the evidence collapsing.
8. **The scorecard's average return is a per-forecast price move, not a portfolio return.** It excludes costs, position sizing, capital constraints, and overlap. It may never be labeled as profit, as performance, or as "what you would have earned" until a portfolio replay under a named Strategy Contract supports that claim. The page must state its own exclusions inline, next to the number, not in a footnote.

---

## Phased Execution Plan

Phases 0–5 are strictly sequential in their dependencies. The **Engineering Band (Phase ENG)** runs continuously in parallel and gates Phase 6 onward. Model innovation is deliberately late.

### Phase 0 — Containment and Correctness Triage

**Purpose.** Stop the bleeding on defects that silently corrupt evidence, without disturbing the in-flight production scan or the live UX.

**Confirmed defects to fix (verified in the current tree):**

| # | Defect | Location |
|---|---|---|
| 0.1 | Ledger entry price is the same completed close that generated the signal | `market_data.py:727` (`entry_price = float(history["Close"].iloc[-1])`) |
| 0.2 | Same-day after-close scans **delete** their own pending forecast: completeness requires `session.date() < current.date()` | `market_data.py:1005`, delete at `market_data.py:710` |
| 0.3 | `forecasts` table carries no model version, git commit, data vintage, or universe id (`scan_runs` has `algorithm_version`; forecasts do not) | `storage.py:75–90` |
| 0.4 | Reward/risk is computed and displayed but **never enters the opportunity score** — the ranking is blind to it, which is why 33 of 45 strong/moderate signals had R/R below 1.0 | `scanner.py:507` computed; score at `scanner.py:508–530` |
| 0.5 | Docker context ships the local `instance/` SQLite database (currently 705 MB, 5.95 M price bars) | `.dockerignore` |
| 0.6 | No test CI; only a nightly scan trigger workflow exists | `.github/workflows/` |
| 0.7 | Dependencies are unpinned ranges with no lock or hashes | `requirements.txt` |
| 0.8 | Public analysis and forced-refresh endpoints have no rate limiting; scan tokens may be accepted in query strings | `app.py` routes |
| 0.9 | Product copy claims "every listed stock" against a Nasdaq + S&P 500 default universe | templates / README |

**Deliverables.** Each fix with a regression test. A `docs/decisions/` log seeded with these entries. Model-version columns added via the first real migration. Scoring change (0.4) is **measurement-only in this phase**: record R/R in the ledger and report how the existing board would rank with and without it; do not retune the formula yet — that is Phase 6, after there is a truth measure to calibrate against.

**Tests.** Leakage test asserting no entry price equals its own signal-bar close. Time-travel test for the after-close path at 21:00 market-local on a session date equal to today. Migration round-trip test. Snapshot test proving board output is unchanged where change was not intended.

**Artifacts.** `docs/decisions/0001-*.md` … ; migration `0001_forecast_provenance`; CI workflow; `.dockerignore`; updated copy.

**Risks.** Touching `market_data.py` while a 3,439-symbol scan is running. Mitigation: no schema-breaking change applied to the live DB while `scan_runs.status = 'running'`; additive columns only; verify against a copy first.

**Exit gate.** All nine defects fixed with tests, CI green on every push, no behavior change in the board other than the intended ones, and the pending forecasts either gradeable or explicitly documented as ungradeable with the reason.

---

### Phase 0B — Daily Value Track

*Runs with Phase 0, deliberately ahead of the heavy research infrastructure.*

**Purpose.** Deliver the improvements a daily user actually feels — a board whose ranking is internally coherent, and a site-wide record of how the algorithm has really performed — without waiting for the portfolio backtester. Nothing in this phase requires calibration against a truth measure that does not yet exist.

**Current ledger state (measured).** All forecasts in the database are a single session: `as_of_date` 2026-07-31, horizon 21 days, `horizon_date` 2026-08-31, ~1,300 rows and growing as the scan runs, **zero matured**. Zero graded is therefore expected, not a grading defect. The real defect is that no earlier history survived, which Phase 0.2 explains and fixes.

**0B.1 — Ledger continuity.** Phase 0.2 is a hard prerequisite. Until same-day after-close forecasts stop being deleted, the ledger cannot accumulate the history every metric below depends on.

**0B.2 — Reward/risk coherence floor.** A signal whose expected move is smaller than its adverse move may not be labeled *strong* or *moderate*. This is a coherence constraint, not a calibration — a "strong" signal that risks more than it targets is incoherent on its face and needs no backtester to reject. Implement as tier eligibility, not as a score weight (weighting is Phase 6). Publish before/after board composition.

**0B.3 — Global scorecard.** A site-wide view answering *"how good has Playbook actually been?"*, aggregated across every graded forecast. Requirements:

- **Direction-adjusted returns.** `signal_return = realized_return` for longs, `−realized_return` for shorts. The stored `realized_return` is the *stock's* move, not the *signal's* — averaging it naively scores every profitable short as a loss. Enforce with a test.
- **Mean and median**, plus dispersion. Means are outlier-dominated; publishing only a mean is a known way to flatter a record.
- **Hit rate at the decision boundary actually used** — the base-rate-relative boundary, never 50% (Standing Instruction A.4).
- **Benchmark comparison.** Average equal-weight return of the same universe over the same holding windows. An average of +2% when the universe returned +3% is *negative* alpha and must be displayed as such. This comparison is the single most informative number on the page.
- **Breakdowns** by side, tier, horizon, and cohort month.
- **Sample size and a bootstrap confidence interval** on every headline figure. Suppress the headline entirely below a minimum sample — *governance choice: 30 matured forecasts.*
- **Maturity accounting.** Show pending, matured, and expired-ungraded counts. Forecasts that never graded because their symbol left the scan universe are a survivorship bias inside your own ledger and must be visible, not silently dropped.

**0B.4 — Per-scan snapshot.** On each completed scan, compute and **append** an immutable scorecard snapshot: metrics, sample counts, cohort boundaries, git commit, config hash, universe id. This satisfies "updated every scan" and creates an audit trail of the metric itself, so a later change in the number can be attributed to new outcomes rather than to a code change. Never overwrite; append only.

**Tests.** Short-side sign test (a short signal on a stock that fell must score as a win). Benchmark alignment test (identical windows and calendar). Snapshot immutability — no UPDATE path. Suppression below minimum sample. A grep-level test asserting no 50% threshold appears in scorecard code. Golden test on a hand-computed fixture of mixed long/short outcomes.

**Artifacts.** `scorecard_snapshots` table + migration; scorecard page; a written definition of every displayed metric and its exclusions.

**Risks.** The dominant risk here is *presentational, not technical*. "Average percentage return" reads to a user as "what I would have earned." It is not: it is an average per-forecast price move, direction-adjusted, with no costs, no sizing, no capital constraint, and no overlap handling. Ten simultaneous signals cannot all receive full capital. Label this inline per §E.8, and never promote the number to a portfolio claim before Phase 4.

**Exit gate.** Scorecard live — direction-adjusted, benchmark-relative, with sample sizes and intervals; a snapshot appended automatically on every completed scan; the first matured cohort (≈2026-08-31) visible; and the page carrying its own limitations in plain language a non-specialist can read.

---

### Phase 1 — Strategy Contract and Typed Domain Core

**Purpose.** Make "what the strategy actually is" a version-controlled, machine-readable object rather than an emergent property of four large modules.

**Deliverables.** `contracts/strategy/v1.yaml` (hashed, versioned) specifying every one of: signal timestamp · data cutoff · eligible universe · entry convention · exit convention · holding period · stop and target behavior · position sizing · maximum positions · gross and net exposure · sector and factor constraints · cash treatment · long rules · short rules · borrow assumptions · commissions · spread and slippage model · market-impact model · corporate-action handling · rebalancing · failure and missing-data behavior · benchmark · primary and secondary metrics. A typed loader, a validator that rejects incomplete contracts, and a resolver that stamps the contract hash onto every signal and every result.

**Tests.** Contract validation rejects each missing field. Hash stability. Every produced signal carries a resolvable contract hash.

**Artifacts.** Contract v1 + hash; ADR on contract-versus-code authority.

**Risks.** Contract drifts from implementation. Mitigation: implementation reads the contract; divergence is a test failure, not a comment.

**Exit gate.** No signal, backtest, or report can be produced without a resolvable contract hash. Attempting to do so raises.

---

### Phase 2 — Executable Fill Model

**Purpose.** Replace the achievable-fill fiction. This is the single highest-value correctness change in the project.

**Deliverables.** Next-session execution with overnight gap modeling; spread model by liquidity tier; commission model; slippage as a function of participation and volatility; market-impact model; stop handling that models gap-through (a stop at −8% that gaps to −14% fills at −14%, not −8%); partial fills; liquidity-based rejection and resizing; forced exits; symmetric short handling with borrow availability and cost. Retire `_strategy_audit`'s same-close, long-only, cost-free convention (`playbook.py:960`) or clearly relabel it as an illustrative non-tradable diagnostic.

**Tests.** Golden fill scenarios: gap-through stop, halt, limit-up, illiquid resize, hard-to-borrow rejection, dividend/split on holding date. Property test: modeled fill is never better than the session's achievable price range.

**Artifacts.** Fill-model spec; scenario fixture library; before/after report quantifying how much measured performance the honesty cost.

**Risks.** Measured performance drops sharply. That is the expected, correct outcome — publish it.

**Exit gate.** Zero same-bar fills anywhere in the codebase, enforced by test. All historical performance numbers regenerated under the new model and the deltas published.

---

### Phase 3 — Point-in-Time Data and Provenance

**Purpose.** Remove survivorship bias and make data reproducible. yfinance may remain a convenience source for the public prototype but **must not silently underwrite commercial-grade claims**.

**Deliverables.** A data layer supporting point-in-time constituent universes; delisted securities with delisting returns; split and dividend history; symbol and corporate-identity changes; historical OHLCV and liquidity; exchange calendars and early closes; exact event timestamps and availability times; short availability and borrow cost where obtainable; historical fundamentals with filing-availability timestamps; historical news only where publication timestamps and licensing are reliable. Plus: data-quality validation, anomaly detection, source lineage, immutable content-addressed snapshots, and a `data_vintage` identifier stamped on every downstream artifact.

**Tests.** As-of universe reconstruction matches known historical index membership on spot-check dates. Delisted names appear in past universes and disappear correctly. Anomaly detectors catch injected bad ticks, stale bars, and impossible ranges. Reproducibility test: same vintage → byte-identical features.

**Artifacts.** Data-source register with license status per source; vintage catalog; quality dashboard; ADR on prototype-versus-research data separation.

**Risks.** Cost and licensing for quality vendor data; scope explosion. Mitigation: define the minimum viable point-in-time set (universe membership, delisting returns, corporate actions, calendars) and defer the rest behind stubs with explicit `NotImplemented` provenance rather than silent fallbacks.

**Exit gate.** A backtest can be run over a historical window whose universe contains names that no longer exist, with delisting returns applied, and the run's `data_vintage` reproduces byte-identically on re-execution.

---

### Phase 4 — Portfolio Backtester

**Purpose.** Replay the actual nightly ranked board as a portfolio. This is what makes monetary claims possible at all.

**Deliverables.** An event-driven (or rigorously vectorized) simulator that: generates signals only after the configured data cutoff; enters at achievable next-session prices; applies the Phase 2 fill model in full; tracks orders, fills, positions, cash, exposure, and NAV; supports longs and shorts symmetrically; handles stops, targets, gap-through, partial fills, and forced exits; **prevents the same capital from being counted twice across overlapping signals**; enforces portfolio, sector, and liquidity constraints; and emits a fully reconcilable trade ledger where NAV is derivable from fills alone.

**Tests.** Cash-conservation invariant. NAV reconciliation from the ledger to the penny. Overlap test: two simultaneous signals cannot both consume 100% of capital. Short-side parity test. Replay determinism under fixed seed.

**Artifacts.** Trade ledger schema; reconciliation report; first honest portfolio equity curve with full cost attribution.

**Risks.** Subtle double-counting; look-ahead reintroduced through convenience joins. Mitigation: the simulator receives data only through an as-of accessor that physically cannot return future rows.

**Exit gate.** A complete historical replay of the nightly board over a multi-year window, reconciling exactly, with a published cost decomposition (commission / spread / slippage / impact / borrow) and comparison against buy-and-hold and cash.

---

### Phase 5 — Validation Harness and the Frozen Holdout

**Purpose.** Establish whether any measured edge is distinguishable from luck.

**Deliverables.** Rolling-origin nested walk-forward with purging and embargo; separate train/select/calibrate/evaluate windows; a **permanently frozen final holdout**, defined and hashed before use and never touched for tuning; baseline suite (buy-and-hold, cash, market-neutral, momentum, mean reversion, linear, tree); feature and component ablations; breakdowns by long/short, sector, liquidity, cap, regime, subperiod; block-bootstrap confidence intervals; multiple-testing control; deflated Sharpe or equivalent; PBO analysis; cost/slippage sensitivity sweeps; capacity analysis; stress and adverse-regime testing.

**Primary metrics.** Net CAGR, annualized volatility, Sharpe, Sortino, Calmar, maximum drawdown, turnover, hit rate, average win/loss, profit factor, exposure, tail loss (CVaR), capacity, beta, factor exposures, alpha vs declared benchmark.
**Secondary metrics (never substitutes).** Brier score and skill, log loss, interval coverage, calibration curves, directional accuracy measured at the decision boundary actually used.

**Tests.** Purge/embargo correctness on synthetic overlapping labels. A deliberately leaked feature must be caught by the leakage suite. Holdout access is instrumented and logged; unauthorized access fails the build.

**Artifacts.** Holdout definition + hash + access log; validation report template; baseline comparison table.

**Risks.** Accidental holdout contamination through shared caches or a careless notebook. Mitigation: physical separation, access instrumentation, and a burn protocol.

**Exit gate.** Gate 3 evidence (below) is producible on demand from a single command.

---

### Phase ENG — Engineering Band (continuous, gates Phase 6+)

**Purpose.** Make the system operable, secure, and trustworthy enough to carry money.

**Deliverables.** Modular typed packages; versioned migrations; CI with unit/property/integration/data-quality/leakage/golden tests; coverage thresholds that ratchet; ruff + format + type checks; hash-pinned dependency lock; structured logs, metrics, tracing, alerting; durable workers separate from web; backups with rehearsed restore; disk and scanner-health monitoring; API rate limiting and abuse controls; header-only secrets; security headers; secret scanning; dependency audit; clean Docker context.

**Exit gate.** A restore drill succeeds from backup; a full nightly 3,439-symbol scan completes within its window with monitoring and alerting proven by an induced failure; no critical or high findings outstanding in the security audit.

---

### Phase 6 — Ranking Calibration and Short Parity

**Purpose.** Replace the hand-authored conviction formula with something calibrated against realized cross-sectional outcomes, and give shorts genuine first-class treatment.

**Deliverables.** Cross-sectional calibration of conviction against realized forward returns under the portfolio backtester; explicit inclusion of reward/risk in ranking (currently absent — `scanner.py:507`); a complete executable short trade plan (borrow, locate, cost, recall risk, hard-to-borrow exclusion) with its own historical short-specific audit; tier thresholds derived from data rather than authored constants (62/40 today); abstention where calibration is weak.

**Tests.** Calibration reliability diagrams by tier. Short-side audit parity with long side. Monotonicity test: higher tiers must show higher realized risk-adjusted return out-of-sample or the tiering is rejected.

**Exit gate.** Calibrated ranking beats the hand-authored formula on walk-forward data under full costs, on both sides, without touching the holdout.

---

### Phase 7 — Model Innovation (gated on Phases 4–6)

**Purpose.** Only now, pursue sophistication.

**Permitted lines.** Regime-conditioned ensembles; analog, momentum, mean-reversion, fundamental, event, volatility, and cross-sectional models; meta-labeling and abstention; probabilistic return and drawdown forecasts; conditional conformal prediction; point-in-time news embeddings; fundamentals and earnings-event models; options or positioning data when licensed and historically reliable; portfolio-aware ranking; uncertainty-aware sizing.

**Standing constraint.** Each component must beat simpler baselines on frozen data and survive ablation, cost, stability, and capacity tests. Each addition consumes multiple-testing budget, which is tracked explicitly.

**Exit gate.** At least one component demonstrably improves net risk-adjusted portfolio return under full costs with dependence-aware significance and no holdout contamination — or the phase concludes with a documented negative result and the simpler model stands.

---

### Phase 8 — Paper Trading and the Live Evidence Spine

**Purpose.** Prove the pipeline works forward in time, with no backfills.

**Deliverables.** Immutable, versioned records for: model versions · data versions · universes · signals · intended orders · broker orders · fills · positions · portfolio snapshots · forecast outcomes · operational incidents. Every signal carries git commit, model/config hash, data vintage, universe id, prediction timestamp, intended execution time, and full risk policy. Paper-broker integration that records **predicted fills and actual broker fills separately**, with slippage attribution between them. Reconciliation, kill switches, stale-data protection, duplicate-order protection.

**Tests.** Immutability (no UPDATE path on evidence tables). Reconciliation catches an injected mismatch. Kill switch halts within one cycle. Duplicate submission is rejected.

**Exit gate.** Gate 4 (below) satisfied over the full pre-registered paper window.

---

### Phase 9 — Controlled Real-Capital Pilot, then Scale

**Purpose.** Small, governed, reversible exposure — then, only on evidence, growth.

**Deliverables.** Predefined capital cap; per-position, sector, gross, net, liquidity, and drawdown limits enforced in code; manual approval for each promotion step; incident log; scheduled reconciliation; a written wind-down procedure.

**Exit gates.** Gates 5 and 6 (below).

---

## Acceptance Gates and Definitions of Done

### Vocabulary control — what licenses each word

| Word | May be used only when |
|---|---|
| **"accurate"** | Directional accuracy is measured at the decision boundary actually used (not 50% when the rule is base-rate-relative), on out-of-sample data, with dependence-aware intervals, and reported alongside calibration and the portfolio result it did or did not produce. |
| **"profitable"** | Net-of-cost portfolio return is positive on a full portfolio replay obeying a named Strategy Contract version, over a pre-registered period, with costs at or above the conservative default, and with the cost decomposition published. Per-symbol or cost-free results never license this word. |
| **"validated"** | Gate 3 is satisfied: frozen-holdout evidence, dependence-aware significance, multiple-testing control, baseline superiority, and stability across regimes. |
| **"paper-traded"** | Gate 4 is satisfied: a pre-registered forward window with zero backfills, actual broker paper fills recorded separately from predicted fills, and no unresolved reconciliation incidents. |
| **"live"** | Gate 5 is satisfied: real capital within the written limit, reconciled, with incidents logged and the pilot's results reported in full including drawdowns. |
| **"average return" / "the algorithm returned X%"** | The figure is direction-adjusted, computed only over matured forecasts, shown with sample size and a bootstrap interval, and displayed beside the same-window benchmark. It must be described as an average per-forecast price move and never as profit, portfolio return, or earnings — those require Gate 2. Below the minimum sample it is suppressed, not shown with a caveat. |

### Gate 1 — Research prototype (honest)

- All Phase 0 defects fixed with regression tests; CI green.
- Every public number carries contract hash and data vintage.
- Product copy matches actual universe and actual capability.
- Known limitations published.

### Gate 2 — Reproducible historical portfolio backtest

- **Zero known look-ahead or same-close execution bias**, enforced by an automated leakage suite.
- Point-in-time universe with delisted names and delisting returns.
- Full portfolio replay reconciling NAV from fills exactly.
- A run reproduced from `(commit, config hash, data vintage, universe id, seed)` alone.
- Conservative costs applied and decomposed.

### Gate 3 — Frozen-holdout validation

- Positive net risk-adjusted performance on the untouched holdout under conservative costs.
- Dependence-aware confidence that the edge is not random; multiple-testing adjusted; deflated Sharpe or equivalent reported.
- Beats every declared baseline net of costs.
- Acceptable drawdown and tail risk against pre-registered limits.
- Stable across at least two distinct market regimes and multiple subperiods.
- Sufficient trade count **and** effective independent sample.
- Capacity above intended capital with margin.
- Edge survives 2× cost sensitivity.

### Gate 4 — Paper-trading candidate → paper-traded

- Pre-registered forward window completed with no backfills, no mid-window parameter changes.
- Forward performance within the modeled cost and risk bands.
- Predicted-versus-actual fill slippage within tolerance.
- Zero unresolved reconciliation, data-quality, or operational incidents.
- Full evidence spine populated and immutable.

### Gate 5 — Controlled real-capital pilot

- All of Gate 4, plus: limits enforced in code, kill switch drill passed, broker reconciliation automated, wind-down procedure written and rehearsed, manual approval recorded.
- Capital at or below the predefined cap.

### Gate 6 — Scaled production strategy

- Pilot results consistent with paper and backtest within stated tolerance.
- Capacity analysis supports the larger allocation.
- Operational maturity: backups, restore drill, monitoring, on-call, incident history clean.
- Explicit written approval per scale step. **No automatic scaling.**

### Recommended initial thresholds — *governance choices, not scientific constants*

These are defensible starting points, chosen **before** results are viewed. They are policy, not findings, and the project should expect to defend or revise them.

| Quantity | Initial value |
|---|---|
| Minimum backtest span | ≥ 10 years including at least one severe drawdown regime |
| Minimum closed trades (Gate 3) | ≥ 300, with effective independent sample ≥ 100 |
| Minimum net Sharpe on holdout | ≥ 0.7 after conservative costs |
| Deflated Sharpe | > 0 at 95% after multiple-testing adjustment |
| Maximum drawdown limit | ≤ 25% in backtest; ≤ 15% triggers pilot review |
| Cost sensitivity | Edge survives 2× modeled costs |
| Capacity margin | ≥ 10× intended pilot capital |
| Paper window | ≥ 3 months and ≥ 100 closed trades, whichever is later |
| Pilot capital cap | ≤ 1% of the operator's investable assets, and a fixed absolute cap |
| Per-position limit | ≤ 5% gross NAV |
| Sector limit | ≤ 25% gross NAV |

**Amendment protocol.** Thresholds may be revised only by a dated entry in `docs/decisions/`, justified without reference to holdout results, and recorded before the next evaluation. If a threshold is changed after seeing holdout results, the holdout is **burned**: it is retired, the change is disclosed, and a replacement holdout is carved only from data arriving after the burn date.

---

## First Goal Payload

Paste the block below into the coding agent's persistent goal mechanism.

```text
GOAL: Build the truth-measurement foundation for Playbook (repo: sentiment_trader) AND ship the daily-value track that makes the board and its track record trustworthy to a daily user — so that the project can eventually demonstrate credible, reproducible, risk-adjusted, net-of-cost portfolio returns on genuinely unseen data, and can never accidentally overstate what it has shown.

STAGE ONE PRIORITY (Phase 0B — do this alongside containment, before the heavy infrastructure):

A. LEDGER CONTINUITY. Fix the bug that deletes same-day after-close forecasts, so the forecast ledger finally accumulates history. Today it holds a single session: as_of_date 2026-07-31, 21-day horizon, resolving 2026-08-31, ~1,300 rows, ZERO matured. Zero graded is expected at this point, not a grading defect — the defect is that no earlier history survived.

B. REWARD/RISK COHERENCE FLOOR. A signal whose expected move is smaller than its adverse move may not be tiered "strong" or "moderate". This is a coherence constraint, not a calibration, and needs no backtester. Implement as tier eligibility, NOT as a score weight — reweighting the formula is Phase 6, after a portfolio truth measure exists. Publish before/after board composition.

C. GLOBAL SCORECARD. A site-wide page answering "how good has the algorithm actually been?", aggregated across all graded forecasts, recomputed and APPENDED as an immutable snapshot on every completed scan. It must:
   - Use DIRECTION-ADJUSTED returns: signal_return = realized_return for longs, -realized_return for shorts. The stored realized_return is the stock's move, not the signal's; averaging it naively scores every profitable short as a loss. Enforce with a test.
   - Report mean AND median plus dispersion, never a lone mean.
   - Compute hit rate at the base-rate-relative decision boundary actually used, never at 50%.
   - Display the same-window equal-weight universe benchmark beside the average. +2% when the universe returned +3% is negative alpha and must read as such.
   - Break down by side, tier, horizon, and cohort month.
   - Show sample size and a bootstrap confidence interval, and SUPPRESS the headline below 30 matured forecasts rather than showing it with a caveat.
   - Show pending / matured / expired-ungraded counts. Forecasts that never matured because their symbol left the universe are survivorship bias inside the ledger and must be visible.
   - State inline that this is an average per-forecast price move — NOT a portfolio return. It excludes costs, sizing, capital constraints, and overlap. Ten simultaneous signals cannot all receive full capital. Never label it profit or earnings before Gate 2.

Then deliver, in dependency order:

This goal is the first stage of a larger mission defined in PLAYBOOK_CHARTER.md, which is canonical and governs all work. Read it before acting and follow its Standing Instructions on every turn.

1. CONTAINMENT. Fix the confirmed correctness defects that silently corrupt evidence, each with a regression test:
   - Ledger entry price equals the signal's own completed close (market_data.py:727). Entry must be next-session and achievable.
   - Same-day after-close scans delete their own pending forecast because completeness requires session.date() < current.date() (market_data.py:1005; delete at market_data.py:710).
   - The forecasts table stores no model version, git commit, data vintage, or universe id (storage.py:75-90), while scan_runs stores algorithm_version.
   - Reward/risk is computed but never enters the opportunity score (computed scanner.py:507; score scanner.py:508-530) — the reason 33 of 45 strong/moderate signals showed R/R below 1.0. In this stage, MEASURE and RECORD this; do not retune the formula until a portfolio-level truth measure exists.
   - .dockerignore ships the local instance/ SQLite database (currently ~705 MB).
   - No test CI; dependencies unpinned; public analysis and refresh endpoints unrate-limited; scan tokens acceptable in query strings; product copy claims "every listed stock" against a Nasdaq + S&P 500 default universe.

2. STRATEGY CONTRACT. A versioned, hashed, machine-readable specification covering signal timestamp, data cutoff, universe, entry, exit, holding period, stops and targets, sizing, max positions, gross/net exposure, sector and factor constraints, cash treatment, long and short rules, borrow, commissions, spread, slippage, market impact, corporate actions, rebalancing, failure behavior, benchmark, and metrics. No signal, backtest, or report may be produced without a resolvable contract hash.

3. EXECUTABLE FILL MODEL. Next-session execution with overnight gaps, spread, commissions, slippage, market impact, borrow, liquidity-based rejection and resizing, partial fills, and stops that model gap-through. Retire or clearly relabel the same-close, long-only, cost-free strategy audit at playbook.py:960.

4. PORTFOLIO BACKTESTER. An event-driven replay of the actual nightly ranked board: orders, fills, positions, cash, exposure, NAV; longs and shorts symmetric; overlapping signals cannot consume the same capital twice; portfolio and sector constraints enforced; a trade ledger from which NAV reconciles exactly.

5. VALIDATION HARNESS. Rolling-origin nested walk-forward with purging and embargo; a permanently frozen holdout defined, hashed, and never used for tuning; baselines (buy-and-hold, cash, market-neutral, momentum, mean reversion, linear, tree); ablations; long/short/sector/liquidity/cap/regime/period breakdowns; block-bootstrap intervals; multiple-testing control; deflated Sharpe; cost sensitivity; capacity analysis.

CONSTRAINTS:
- Preserve the existing working UX and scanner behavior. Replace foundations incrementally behind interfaces; no rewrite without a written ADR.
- Never use a 50% probability boundary to score a decision made at a base-rate-relative boundary.
- Never describe any result as tradable, profitable, accurate, or validated except as licensed by the Acceptance Gates in PLAYBOOK_CHARTER.md.
- Report unfavorable results as readily as favorable ones. Expect measured performance to FALL as execution realism improves; that is success, not regression.
- No real money. No promises of profit. Ever.

DONE WHEN:
- Stage one: the ledger accumulates history across sessions; no signal is tiered strong or moderate with reward/risk below 1.0; and the global scorecard is live with direction-adjusted returns, a same-window benchmark, sample sizes, intervals, maturity accounting, and an immutable snapshot appended on every completed scan.
- Overall: Gate 2 in PLAYBOOK_CHARTER.md is satisfied — zero known look-ahead or same-close execution, point-in-time universe with delisted names, a full portfolio replay reconciling exactly, reproducible from (commit, config hash, data vintage, universe id, seed) alone, under conservative costs with the cost decomposition published.
```

---

## First-Turn Instructions

Paste the block below as the coding agent's first message.

```text
Read PLAYBOOK_CHARTER.md first; it is canonical and governs everything you do.

Before changing anything:

1. Inspect the working tree exactly as you find it. Run git status and git diff. There is an untracked HANDOFF_PROMPT.md and there may be other uncommitted user work. PRESERVE IT. Do not stash, reset, checkout over, or clean anything. Do not commit user changes without asking.

2. Confirm the active scan state WITHOUT disturbing it. A production-sized scan was in progress at last check: scan_runs id=7, algorithm_version 'local-full-friday-20260801', session_date 2026-07-31, 3439 symbols, ~1159 completed / 250 skipped / 3 failed, status 'running'. Query instance/playbook.sqlite3 READ-ONLY (sqlite URI mode=ro). Do not write to that database, do not run migrations against it, do not restart the local Flask process, and do not kill the scan. If it is still running, note the completion rate and the ~18% skip rate as an open data-quality question. Work against a COPY of the database for any schema experimentation.

3. Verify the charter's Phase 0 defect list against the current code yourself rather than trusting it. Each entry cites a file and line. Confirm or correct each one, and say plainly which you confirmed, which you could not reproduce, and which you found to be worse or different than described.

4. Produce a short plan for Phase 0 — ordered, with the highest-risk correctness work first — and then START EXECUTING IT in the same session. Do not stop after producing another report. The project already has enough analysis; it needs verified change.

Begin with the highest-risk correctness work, in this order:

   a. LEDGER CONTINUITY (highest priority — everything else depends on it). The after-close completeness check at market_data.py:1005 returns session.date() < current.date(), and the caller at market_data.py:710 responds by calling delete_pending_forecast. A scan run after the close on the same session therefore ERASES its own signals. Write a time-travel test at 21:00 market-local with session_date equal to the local date, prove the deletion happens, then fix it so the forecast is retained. This is why the ledger holds only one session of history.

   b. The same-close entry price in the live ledger (market_data.py:727 uses history["Close"].iloc[-1], the very close that generated the signal). Write the failing test first. Record an achievable next-session entry reference alongside it; full fill modeling is later.

   c. Forecast provenance columns (model version, git commit, config hash, data vintage, universe id) via a real versioned migration — additive only, and NOT applied to the live database while a scan is running.

   d. REWARD/RISK COHERENCE FLOOR. reward_risk is computed at scanner.py:507 and then never referenced by the score at scanner.py:508-530 — the ranking is structurally blind to it. Do NOT reweight the formula. Add a tier-eligibility rule: reward/risk below 1.0 cannot be tiered strong or moderate. Report how the current board's composition changes.

   e. GLOBAL SCORECARD. Aggregate the forecasts table site-wide. Direction-adjusted returns (longs +realized_return, shorts -realized_return) — write that test FIRST, because getting the sign wrong silently scores every winning short as a loss. Mean and median, hit rate at the base-rate-relative boundary (never 50%), same-window equal-weight benchmark shown beside the average, breakdowns by side/tier/horizon/cohort, sample size with bootstrap interval, headline suppressed below 30 matured forecasts, and pending/matured/expired-ungraded counts displayed. Append an immutable snapshot per completed scan; never overwrite. Label inline that this is an average per-forecast price move, not a portfolio return, and that it excludes costs, sizing, capital limits, and overlap.

   f. .dockerignore excluding instance/, *.sqlite3*, .env, and local logs.
   g. Test CI running the existing 107 tests plus your new ones on every push.

Expect the scorecard to show NOTHING but pending counts at first. The only cohort in the ledger matures around 2026-08-31. That is correct behavior — build it so it is honest and empty rather than padded with a number it has not earned.

Rules for this turn:
- Every fix lands with a test that fails before and passes after. Show the failing output.
- Run the full existing test suite before and after; report the real numbers, including any pre-existing failures.
- Do not reweight the conviction/opportunity score formula. The eligibility floor in (d) is the only scoring-adjacent change permitted; recalibration requires the portfolio backtester.
- Do not begin the portfolio backtester this turn.
- At the end, report: what you verified by execution versus what you only wrote; what you changed; what you deliberately deferred; and the single most likely place you are still wrong.
```
