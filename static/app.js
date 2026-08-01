const state = {
  analysis: null,
  request: null,
  trackRequest: null,
  timeRequest: null,
  watchRefresh: null,
  selectedTwin: null,
  showAllReceipts: false,
  watchlist: readStoredList(),
  watchData: readStoredInsights(),
};

const $ = (id) => document.getElementById(id);
const pct = (value, digits = 1) =>
  `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
const tone = (value) =>
  value > 0.001 ? "positive" : value < -0.001 ? "negative" : "neutral";

const money = (value, code = "USD", digits) => {
  const places = digits !== undefined ? digits : (Number(value) < 1 ? 4 : 2);
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: code,
      minimumFractionDigits: places,
      maximumFractionDigits: places,
    }).format(value);
  } catch {
    return `${Number(value).toFixed(places)} ${code}`;
  }
};

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* optional storage */ }
}

function readStoredList() {
  try {
    const value = JSON.parse(storageGet("playbook-watchlist") || "[]");
    return Array.isArray(value)
      ? value.filter((item) => typeof item === "string").slice(0, 10)
      : [];
  } catch {
    return [];
  }
}

function readStoredInsights() {
  try {
    const value = JSON.parse(storageGet("playbook-watch-data") || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}

function saveWatchlist() {
  storageSet("playbook-watchlist", JSON.stringify(state.watchlist));
}

function saveWatchInsights() {
  storageSet("playbook-watch-data", JSON.stringify(state.watchData));
}

function startLoading() {
  $("loadingStrip").hidden = false;
  setLoadingStage("Fetching adjusted prices and current headlines…", 8);
}

function setLoadingStage(message, progress) {
  $("loadingText").textContent = message;
  $("loadingBar").style.width = `${Math.max(0, Math.min(100, progress))}%`;
}

function stopLoading() {
  setLoadingStage("Forecast and audit ready.", 100);
  $("loadingStrip").hidden = true;
}

async function loadSymbol(rawSymbol) {
  const symbol = String(rawSymbol || "")
    .trim()
    .toUpperCase()
    .replace(/^\$/, "");
  if (!symbol) {
    $("symbolInput").focus();
    return;
  }

  if (state.request) state.request.abort();
  if (state.trackRequest) state.trackRequest.abort();
  if (state.timeRequest) state.timeRequest.abort();
  const controller = new AbortController();
  state.request = controller;
  state.selectedTwin = null;
  $("statusMessage").textContent = "";
  $("symbolInput").value = symbol;
  $("trackSummary").replaceChildren();
  $("trackRecordList").replaceChildren();
  $("trackRecordEmpty").hidden = false;
  $("timeMachineResult").hidden = true;
  $("timeMachineStatus").textContent = "";
  startLoading();
  let quickRendered = false;

  try {
    const quickResponse = await fetch(`/api/analyze/${encodeURIComponent(symbol)}/quick`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const quick = await quickResponse.json();
    if (!quickResponse.ok) {
      throw new Error(quick.error || "Playbook could not build this forecast.");
    }
    if (state.request !== controller) return;

    state.analysis = quick;
    quickRendered = true;
    state.showAllReceipts = false;
    setLoadingStage("Historical twins found. Painting the preliminary forecast…", 58);
    render(quick);
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", quick.symbol);
    window.history.replaceState({}, "", url);
    storageSet("playbook-last-symbol", quick.symbol);

    setLoadingStage("Running untouched walk-forward audit checkpoints…", 72);
    const auditUrl = `/api/analyze/${encodeURIComponent(symbol)}/audit?snapshot=${encodeURIComponent(quick.snapshot_id)}`;
    const auditResponse = await fetch(auditUrl, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const audited = await auditResponse.json();
    if (!auditResponse.ok) {
      throw new Error(audited.error || "The forecast loaded, but its audit did not.");
    }
    if (state.request !== controller) return;
    if (audited.snapshot_id !== quick.snapshot_id) {
      throw new Error(
        "Market data changed while the audit was running; search again for one coherent snapshot.",
      );
    }
    state.analysis = audited;
    setLoadingStage("Calibrating evidence and finalizing the forecast…", 94);
    render(audited);
    loadTrackRecord(audited.symbol);
  } catch (error) {
    if (error.name !== "AbortError" && state.request === controller) {
      $("statusMessage").textContent = quickRendered
        ? `Preliminary forecast shown. Audit unavailable: ${error.message}`
        : error.message;
    }
  } finally {
    if (state.request === controller) {
      stopLoading();
      state.request = null;
    }
  }
}

function render(data) {
  const play = data.playbook;
  $("workspace").hidden = false;
  $("assetSymbol").textContent = data.symbol;
  $("assetName").textContent = [data.name, data.sector].filter(Boolean).join(" · ");
  $("assetPrice").textContent = money(data.quote.price, data.currency);
  $("dailyChange").textContent = `${pct(data.quote.daily_change, 2)} today`;
  $("dailyChange").dataset.tone = tone(data.quote.daily_change);

  if (play.available) {
    renderForecast(play);
    renderFingerprint(play);
    renderRange(play);
    renderReliability(play.validation);
    renderAudit(play.validation);
    renderWaterfall(play.forecast);
    renderAgreement(play.forecast.agreement);
    renderReceipts(play);
    renderTwinExplorer(play);
    renderPlan(data);
    renderCatalyst(play.catalyst);
  } else {
    renderUnavailable(play);
  }

  renderStory(data.story);
  renderNews(data);
  renderChart();
  setTimeMachineBounds(data);
  rememberWatchInsight(data);
  updateWatchButton();
  const warnings = data.warnings.length
    ? ` Data notes: ${data.warnings.join(" ")}`
    : "";
  $("methodologyText").textContent =
    `${data.methodology} This request used ${data.history_years} years of available data. ` +
    `Match scores measure closeness, not probability. The evidence interval reflects a ` +
    `small number of independent historical episodes, and future events can break every pattern.` +
    warnings;
}

function renderForecast(play) {
  const verdict = play.verdict;
  const forecast = play.forecast;
  const card = $("verdictCard");
  card.dataset.direction = verdict.direction;
  $("verdictArrow").textContent =
    verdict.direction === "bullish" ? "↗" : verdict.direction === "bearish" ? "↘" : "⇄";
  $("verdictHeadline").textContent = verdict.headline;
  $("verdictExplanation").textContent = verdict.explanation;
  $("horizonKicker").textContent =
    `${play.preliminary ? "Preliminary · " : ""}${forecast.horizon_label} analog forecast`;
  $("setupLine").textContent = play.setup;
  $("setupLine").hidden = false;

  $("probabilityValue").textContent = `${forecast.probability_up}%`;
  $("probabilityInterval").textContent =
    `Analog evidence range: ${forecast.probability_low}–${forecast.probability_high}%`;
  $("gaugeFill").style.width = `${forecast.evidence_score}%`;
  $("confidenceLabel").textContent = `Evidence ${forecast.evidence_score}/100`;
  $("analogProbability").textContent = `${forecast.analog_probability_up}%`;
  $("baselineProbability").textContent = `${forecast.baseline_up_rate}%`;
  $("newsAdjustment").textContent =
    `${forecast.news_adjustment_points >= 0 ? "+" : ""}${forecast.news_adjustment_points} pts`;
  $("newsAdjustment").className =
    forecast.news_adjustment_points > 0
      ? "positive"
      : forecast.news_adjustment_points < 0
        ? "negative"
        : "";
  $("analogEdge").textContent =
    `${forecast.edge_points >= 0 ? "+" : ""}${forecast.edge_points} pts`;
  $("analogEdge").className =
    forecast.edge_points > 0 ? "positive" : forecast.edge_points < 0 ? "negative" : "";
}

function renderFingerprint(play) {
  $("profilePill").textContent = `${play.matching.profile} weights`;
  const cards = play.fingerprint.cards.map((item) => {
    const card = document.createElement("article");
    card.className = "fingerprint-card";
    const label = document.createElement("span");
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = item.value;
    const detail = document.createElement("small");
    detail.textContent = item.detail;
    card.append(label, value, detail);
    return card;
  });
  $("fingerprintGrid").replaceChildren(...cards);

  const facts = [
    `${play.matching.features_used.length} signals`,
    `${play.matching.shape_window_days}-day chart shape`,
    `${play.matching.candidate_years} years searched`,
    `${play.matching.match_count} independent twins`,
    `${play.matching.independence_days}-day episode spacing`,
  ];
  $("matchingStrip").replaceChildren(...facts.map((fact, index) => {
    const item = document.createElement("span");
    const number = document.createElement("i");
    number.textContent = String(index + 1).padStart(2, "0");
    const text = document.createElement("b");
    text.textContent = fact;
    item.append(number, text);
    return item;
  }));
  $("chartSub").textContent =
    `${play.matching.match_count} independent historical setups were found. ` +
    `The chart shows the ${play.ghost_paths.length} closest paths; the band uses every match.`;
}

function renderRange(play) {
  const forecast = play.forecast;
  const stats = play.stats;
  $("rangeLow").textContent = pct(forecast.range_21d.low);
  $("rangeTypical").textContent = pct(forecast.range_21d.typical);
  $("rangeHigh").textContent = pct(forecast.range_21d.high);
  const fullRange = forecast.range_21d.high - forecast.range_21d.low || 1;
  const marker = ((forecast.range_21d.typical - forecast.range_21d.low) / fullRange) * 100;
  $("rangeMedianMarker").style.left = `${Math.max(0, Math.min(100, marker))}%`;

  const facts = [
    ["Raw wins", `${stats.wins_21d} of ${stats.count}`],
    ["Effective independent evidence", `${stats.effective_matches} matches`],
    ["Different calendar years", String(stats.distinct_years)],
    ["Median match score", `${play.matching.median_quality}/100`],
  ];
  $("evidenceFacts").replaceChildren(...facts.map(([label, value]) => {
    const row = document.createElement("div");
    const key = document.createElement("span");
    const result = document.createElement("strong");
    key.textContent = label;
    result.textContent = value;
    row.append(key, result);
    return row;
  }));

  $("horizonGrid").replaceChildren(...forecast.horizons.map((horizon) => {
    const card = document.createElement("article");
    card.dataset.direction = horizon.direction;
    const label = document.createElement("span");
    label.textContent = horizon.label;
    const probability = document.createElement("strong");
    probability.textContent = `${horizon.probability_up}% up`;
    const detail = document.createElement("small");
    detail.textContent =
      `${pct(horizon.median_return)} typical · ` +
      `${pct(horizon.low_return)} to ${pct(horizon.high_return)}`;
    card.append(label, probability, detail);
    return card;
  }));
  const conformal = play.validation?.conformal;
  $("intervalNote").textContent = conformal?.available
    ? `The endpoint range expands the raw 20th–80th percentile band by ` +
      `${conformal.adjustment_points} points per side. It covered ` +
      `${conformal.adjusted_coverage}% of untouched outcomes versus a ` +
      `${conformal.target_coverage}% target.`
    : "Preliminary ranges are raw matched-path percentiles until the coverage audit completes.";
}

function renderReliability(validation) {
  if (!validation.available) {
    $("reliabilityGrade").textContent = validation.pending
      ? "Audit loading"
      : "Not enough checks";
    $("reliabilityGrade").dataset.grade = "limited";
    $("validationAccuracy").textContent = "—";
    $("validationAccuracyLabel").textContent = "Reliability is not established yet";
    $("playbookBar").style.width = "0%";
    $("baselineBar").style.width = "0%";
    $("playbookAccuracy").textContent = "—";
    $("baselineAccuracy").textContent = "—";
    $("validationNote").textContent = validation.reason;
    return;
  }

  $("reliabilityGrade").textContent = validation.label;
  $("reliabilityGrade").dataset.grade = validation.grade;
  $("validationAccuracy").textContent = `${validation.accuracy}%`;
  $("validationAccuracyLabel").textContent =
    `${validation.correct} of ${validation.sample_size} untouched forecasts correct ` +
    `(95% range ${validation.accuracy_low}–${validation.accuracy_high}%)`;
  $("playbookBar").style.width = `${validation.accuracy}%`;
  $("baselineBar").style.width = `${validation.baseline_accuracy}%`;
  $("playbookAccuracy").textContent = `${validation.accuracy}%`;
  $("baselineAccuracy").textContent = `${validation.baseline_accuracy}%`;
  const actionable = validation.actionable_count
    ? ` Stronger-edge checkpoints: ${validation.actionable_accuracy}% correct across ` +
      `${validation.actionable_count} signals.`
    : " No checkpoint cleared the stronger-edge threshold.";
  $("validationNote").textContent = validation.explanation + actionable;
}

function renderAudit(validation) {
  if (!validation.available) {
    $("auditFrequency").textContent = validation.pending ? "Audit running" : "Unavailable";
    [
      "auditBrierSkill",
      "auditSample",
      "auditIndependent",
      "auditBrier",
      "auditCoverage",
      "auditExpansion",
    ]
      .forEach((id) => { $(id).textContent = "—"; });
    $("auditCoverageTarget").textContent = "Untouched forecasts";
    $("auditPeriod").textContent = validation.reason || "No evaluation period";
    $("calibrationList").replaceChildren();
    $("strategySummary").replaceChildren();
    $("strategyChart").replaceChildren();
    $("strategyNote").textContent = validation.reason || "";
    $("edgeStrataBody").replaceChildren();
    $("regimeStrataBody").replaceChildren();
    $("evaluationBody").replaceChildren();
    return;
  }

  $("auditFrequency").textContent =
    `Every ${validation.evaluation_frequency_sessions} sessions`;
  $("auditBrierSkill").textContent =
    `${validation.brier_skill >= 0 ? "+" : ""}${validation.brier_skill}%`;
  $("auditBrierSkill").className = tone(validation.brier_skill);
  $("auditSample").textContent = String(validation.sample_size);
  $("auditIndependent").textContent = String(validation.independent_sample_size);
  $("auditBrier").textContent =
    `${Number(validation.brier).toFixed(3)} / ${Number(validation.baseline_brier).toFixed(3)}`;
  $("auditPeriod").textContent =
    `${formatDate(validation.evaluation_period.start)} – ${formatDate(validation.evaluation_period.end)}`;
  const conformal = validation.conformal;
  $("auditCoverage").textContent = conformal?.available
    ? `${conformal.adjusted_coverage}%`
    : "—";
  $("auditCoverageTarget").textContent = conformal?.available
    ? `${conformal.target_coverage}% target · ${conformal.raw_coverage}% raw`
    : "Coverage unavailable";
  $("auditExpansion").textContent = conformal?.available
    ? `±${conformal.adjustment_points}`
    : "—";

  $("calibrationList").replaceChildren(...validation.calibration.map((bucket) => {
    const row = document.createElement("div");
    row.className = "calibration-row";
    const heading = document.createElement("div");
    const label = document.createElement("strong");
    label.textContent = bucket.label;
    const count = document.createElement("span");
    count.textContent = `${bucket.count} checks`;
    heading.append(label, count);
    const bars = document.createElement("div");
    bars.className = "calibration-bars";
    const predicted = document.createElement("i");
    predicted.style.width = `${bucket.predicted_up}%`;
    predicted.title = `Predicted ${bucket.predicted_up}%`;
    const observed = document.createElement("b");
    observed.style.width = `${bucket.observed_up}%`;
    observed.title = `Observed ${bucket.observed_up}%`;
    bars.append(predicted, observed);
    const values = document.createElement("small");
    values.textContent =
      `${bucket.predicted_up}% predicted · ${bucket.observed_up}% observed · ` +
      `${bucket.gap_points >= 0 ? "+" : ""}${bucket.gap_points} pt gap`;
    row.append(heading, bars, values);
    return row;
  }));

  renderStrategyAudit(validation.strategy);
  renderStrataTable("edgeStrataBody", validation.edge_strata, false);
  renderStrataTable("regimeStrataBody", validation.regime_strata, true);
  renderEvaluationRecords(validation.records);
}

function renderWaterfall(forecast) {
  $("probabilityWaterfall").replaceChildren(...forecast.waterfall.map((step, index) => {
    const row = document.createElement("div");
    row.className = "waterfall-row";
    const number = document.createElement("i");
    number.textContent = String(index + 1).padStart(2, "0");
    const body = document.createElement("div");
    const heading = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = step.label;
    value.textContent = `${step.value}%`;
    heading.append(label, value);
    const track = document.createElement("div");
    const fill = document.createElement("b");
    fill.style.width = `${Math.max(1, Math.min(99, step.value))}%`;
    track.append(fill);
    body.append(heading, track);
    const delta = document.createElement("em");
    delta.textContent = index
      ? `${step.delta >= 0 ? "+" : ""}${step.delta} pts`
      : "starting point";
    delta.className = tone(step.delta);
    row.append(number, body, delta);
    return row;
  }));
}

function renderAgreement(agreement) {
  if (!agreement) return;
  $("agreementLabel").textContent = agreement.label;
  $("agreementScore").textContent = `${agreement.score}/100`;
  $("agreementFill").style.width = `${agreement.score}%`;
  $("agreementComponents").replaceChildren(...agreement.components.map((component) => {
    const row = document.createElement("div");
    row.dataset.state = component.state;
    const stateIcon = document.createElement("i");
    stateIcon.textContent = component.state === "bullish"
      ? "↑" : component.state === "bearish" ? "↓" : "–";
    const copy = document.createElement("span");
    const label = document.createElement("strong");
    const detail = document.createElement("small");
    label.textContent = component.label;
    detail.textContent = component.detail;
    copy.append(label, detail);
    row.append(stateIcon, copy);
    return row;
  }));
}

function renderStrategyAudit(strategy) {
  if (!strategy?.available) {
    $("strategySummary").replaceChildren();
    $("strategyChart").replaceChildren();
    $("strategyNote").textContent = "Not enough non-overlapping checkpoints.";
    return;
  }
  const facts = [
    ["Long/cash", pct(strategy.strategy_return)],
    ["Buy and hold", pct(strategy.hold_return)],
    ["Excess", pct(strategy.excess_return)],
    ["Max drawdown", pct(strategy.max_drawdown)],
    ["Long signals", `${strategy.trades} / ${strategy.periods}`],
    ["Signal win rate", strategy.trade_win_rate === null ? "—" : `${strategy.trade_win_rate}%`],
  ];
  $("strategySummary").replaceChildren(...facts.map(([label, value]) => {
    const item = document.createElement("div");
    const key = document.createElement("span");
    const result = document.createElement("strong");
    key.textContent = label;
    result.textContent = value;
    item.append(key, result);
    return item;
  }));
  $("strategyNote").textContent = strategy.note;
  renderEquityChart(strategy);
}

function renderEquityChart(strategy) {
  const svg = $("strategyChart");
  const strategyValues = strategy.curve.map((point) => Number(point.value));
  const holdValues = strategy.hold_curve.map((point) => Number(point.value));
  const values = [...strategyValues, ...holdValues];
  if (values.length < 2) {
    svg.replaceChildren();
    return;
  }
  const width = 560;
  const height = 190;
  const pad = 12;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || 1;
  const x = (index, count) => pad + index / Math.max(1, count - 1) * (width - pad * 2);
  const y = (value) => pad + (maximum - value) / spread * (height - pad * 2);
  const path = (series) => series.map((value, index) =>
    `${index ? "L" : "M"}${x(index, series.length).toFixed(1)},${y(value).toFixed(1)}`)
    .join(" ");
  svg.innerHTML = [
    `<line class="strategy-baseline" x1="${pad}" x2="${width - pad}" y1="${y(100)}" y2="${y(100)}"></line>`,
    `<path class="strategy-hold-line" d="${path(holdValues)}"></path>`,
    `<path class="strategy-model-line" d="${path(strategyValues)}"></path>`,
  ].join("");
}

function renderStrataTable(id, strata, showReturn) {
  $(id).replaceChildren(...strata.map((item) => {
    const row = document.createElement("tr");
    const values = [
      item.label,
      item.count,
      `${item.accuracy}%`,
      showReturn ? pct(item.average_return) : Number(item.brier).toFixed(3),
    ];
    row.append(...values.map((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      cell.textContent = value;
      return cell;
    }));
    return row;
  }));
}

function renderEvaluationRecords(records) {
  const latest = records.slice(-14).reverse();
  $("evaluationBody").replaceChildren(...latest.map((record) => {
    const row = document.createElement("tr");
    const result = record.signal === "neutral"
      ? "No call"
      : record.signal_correct ? "Correct" : "Wrong";
    const values = [
      formatDate(record.date),
      `${record.probability_up}%`,
      `${record.edge_points >= 0 ? "+" : ""}${record.edge_points} pts`,
      record.signal,
      pct(record.actual_return),
      result,
    ];
    row.append(...values.map((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 5) {
        cell.className = result === "Correct" ? "result-correct"
          : result === "Wrong" ? "result-wrong" : "";
      }
      return cell;
    }));
    return row;
  }));
}

function formatDate(value) {
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.valueOf())
    ? String(value || "—")
    : date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
}

function renderCatalyst(catalyst) {
  const warning = $("catalystWarning");
  warning.hidden = !catalyst?.near;
  warning.textContent = catalyst?.near ? `⚠ ${catalyst.warning}` : "";
}

function renderUnavailable(play) {
  const card = $("verdictCard");
  card.dataset.direction = "neutral";
  $("verdictArrow").textContent = "…";
  $("verdictHeadline").textContent = "Not enough history to find trustworthy twins";
  $("verdictExplanation").textContent = play.reason;
  $("horizonKicker").textContent = "One-month analog forecast";
  $("probabilityValue").textContent = "—";
  $("probabilityInterval").textContent = "No probability generated";
  $("gaugeFill").style.width = "0%";
  $("confidenceLabel").textContent = "No evidence";
  ["analogProbability", "baselineProbability", "newsAdjustment", "analogEdge"]
    .forEach((id) => { $(id).textContent = "—"; });
  $("setupLine").hidden = true;
  $("fingerprintGrid").replaceChildren();
  $("matchingStrip").replaceChildren();
  $("profilePill").textContent = "Unavailable";
  $("chartSub").textContent =
    "No historical projection is shown until enough independent twins are available.";
  ["rangeLow", "rangeTypical", "rangeHigh"].forEach((id) => { $(id).textContent = "—"; });
  $("rangeMedianMarker").style.left = "50%";
  $("evidenceFacts").replaceChildren();
  $("horizonGrid").replaceChildren();
  $("intervalNote").textContent = play.reason;
  renderReliability({ available: false, reason: play.reason });
  renderAudit({ available: false, reason: play.reason });
  $("probabilityWaterfall").replaceChildren();
  $("agreementComponents").replaceChildren();
  $("agreementScore").textContent = "—";
  $("agreementFill").style.width = "0%";
  $("receiptsBody").replaceChildren();
  $("toggleReceipts").hidden = true;
  $("planRows").replaceChildren();
  $("planTitle").textContent = "No path-supported plan";
  $("planNote").textContent = "A defensible risk plan requires historical analog evidence first.";
  $("planSizer").hidden = true;
  $("catalystWarning").hidden = true;
  $("chartTooltip").hidden = true;
  $("twinChips").replaceChildren();
  $("twinContributions").replaceChildren();
}

function setTimeMachineBounds(data) {
  if (!data.history.length) return;
  const input = $("timeMachineDate");
  const latest = data.history[data.history.length - 1].date;
  input.max = latest;
  if (!input.value || input.value >= latest) {
    const defaultIndex = Math.max(0, data.history.length - 126);
    input.value = data.history[defaultIndex].date;
  }
}

async function loadTrackRecord(symbol) {
  if (state.trackRequest) state.trackRequest.abort();
  const controller = new AbortController();
  state.trackRequest = controller;
  try {
    const response = await fetch(
      `/api/track-record/${encodeURIComponent(symbol)}`,
      { signal: controller.signal, headers: { Accept: "application/json" } },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Track record unavailable.");
    if (state.trackRequest !== controller || state.analysis?.symbol !== symbol) return;
    renderTrackRecord(data);
  } catch (error) {
    if (error.name !== "AbortError" && state.trackRequest === controller) {
      $("trackRecordEmpty").hidden = false;
      $("trackRecordEmpty").textContent = error.message;
    }
  } finally {
    if (state.trackRequest === controller) state.trackRequest = null;
  }
}

function renderTrackRecord(data) {
  const records = data.records || [];
  $("trackRecordEmpty").hidden = records.length > 0;
  if (!records.length) {
    $("trackSummary").replaceChildren();
    $("trackRecordList").replaceChildren();
    return;
  }
  const summary = data.summary;
  const facts = [
    ["Stored", summary.total],
    ["Graded", summary.graded],
    ["Pending", summary.pending],
    ["Directional accuracy", summary.directional_accuracy === null
      ? "—" : `${summary.directional_accuracy}%`],
  ];
  $("trackSummary").replaceChildren(...facts.map(([label, value]) => {
    const item = document.createElement("div");
    const key = document.createElement("span");
    const result = document.createElement("strong");
    key.textContent = label;
    result.textContent = value;
    item.append(key, result);
    return item;
  }));
  $("trackRecordList").replaceChildren(...records.slice(0, 8).map((record) => {
    const row = document.createElement("article");
    row.className = "track-record-row";
    const call = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${formatDate(record.as_of_date)} · ${record.direction}`;
    const detail = document.createElement("span");
    detail.textContent =
      `${record.probability_up}% up · ${record.edge_points >= 0 ? "+" : ""}${record.edge_points} pt edge`;
    call.append(title, detail);
    const outcome = document.createElement("div");
    outcome.className = "track-outcome";
    if (record.status === "graded") {
      outcome.textContent = pct(record.realized_return);
      outcome.dataset.result = record.direction_correct === true
        ? "correct" : record.direction_correct === false ? "wrong" : "neutral";
    } else {
      outcome.textContent = `Due ${formatDate(record.horizon_date)}`;
      outcome.dataset.result = "pending";
    }
    row.append(call, outcome);
    return row;
  }));
}

async function runTimeMachine(date) {
  const symbol = state.analysis?.symbol;
  if (!symbol) return;
  if (state.timeRequest) state.timeRequest.abort();
  const controller = new AbortController();
  state.timeRequest = controller;
  $("timeMachineStatus").textContent = "Rebuilding the fingerprint with later rows sealed off…";
  $("timeMachineResult").hidden = true;
  try {
    const url =
      `/api/analyze/${encodeURIComponent(symbol)}/as-of?date=${encodeURIComponent(date)}`;
    const response = await fetch(url, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Time Machine could not run.");
    if (state.timeRequest !== controller) return;
    renderTimeMachine(data);
    $("timeMachineStatus").textContent =
      `Forecast rebuilt at ${formatDate(data.time_machine.session_date)} with no future rows.`;
  } catch (error) {
    if (error.name !== "AbortError" && state.timeRequest === controller) {
      $("timeMachineStatus").textContent = error.message;
    }
  } finally {
    if (state.timeRequest === controller) state.timeRequest = null;
  }
}

function renderTimeMachine(data) {
  const box = $("timeMachineResult");
  const play = data.playbook;
  if (!play.available) {
    box.textContent = play.reason;
    box.hidden = false;
    return;
  }
  const outcome = data.time_machine.outcome;
  const forecast = document.createElement("div");
  forecast.className = "time-machine-call";
  const probability = document.createElement("strong");
  probability.textContent = `${play.forecast.probability_up}%`;
  const copy = document.createElement("span");
  copy.textContent =
    `${play.verdict.headline} · ${play.forecast.edge_points >= 0 ? "+" : ""}` +
    `${play.forecast.edge_points} point analog edge`;
  forecast.append(probability, copy);
  const realized = document.createElement("div");
  realized.className = "time-machine-outcome";
  if (outcome.available) {
    realized.dataset.result = outcome.direction_correct === true
      ? "correct" : outcome.direction_correct === false ? "wrong" : "neutral";
    realized.textContent =
      `What happened by ${formatDate(outcome.date)}: ${pct(outcome.realized_return)} · ` +
      `${outcome.direction_correct === true ? "direction correct"
        : outcome.direction_correct === false ? "direction wrong" : "no directional call"}`;
  } else {
    realized.textContent = outcome.reason;
  }
  const seal = document.createElement("small");
  seal.textContent = "Forecast input cutoff verified · historical news excluded";
  box.replaceChildren(forecast, realized, seal);
  box.hidden = false;
}

function renderStory(story) {
  const box = $("storyCheck");
  if (!story) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.dataset.state = story.state;
  $("storyIcon").textContent =
    story.state === "confirms" ? "✓" : story.state === "conflicts" ? "!" : "N";
  $("storySummary").textContent = story.summary;
}

function renderReceipts(play) {
  const matches = state.showAllReceipts ? play.matches : play.matches.slice(0, 8);
  const rows = matches.map((match) => {
    const row = document.createElement("tr");
    const cell = (value, className) => {
      const item = document.createElement("td");
      if (className) item.className = className;
      item.textContent = value;
      return item;
    };
    const scoreCell = cell(`${match.quality}/100`, "match-score-cell");
    const score = document.createElement("span");
    score.className = "match-badge";
    score.textContent = `${match.quality}/100`;
    scoreCell.replaceChildren(score);
    row.append(
      cell(new Date(`${match.date}T12:00:00`).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      })),
      scoreCell,
      cell(match.regime, "regime-cell"),
      cell(pct(match.max_drawdown), "pct down"),
      cell(pct(match.max_upside), "pct up"),
      cell(pct(match.fwd_21d), `pct ${match.fwd_21d > 0 ? "up" : match.fwd_21d < 0 ? "down" : ""}`),
    );
    if (play.ghost_paths.some((path) => path.date === match.date)) {
      row.classList.add("interactive-receipt");
      if (match.date === state.selectedTwin) {
        row.classList.add("selected-receipt");
        row.setAttribute("aria-selected", "true");
      }
      row.tabIndex = 0;
      row.addEventListener("click", () => selectTwin(match.date));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTwin(match.date);
        }
      });
    }
    return row;
  });
  $("receiptsBody").replaceChildren(...rows);
  $("toggleReceipts").textContent = state.showAllReceipts
    ? "Show closest eight"
    : `Inspect all ${play.matches.length} twins`;
  $("toggleReceipts").hidden = play.matches.length <= 8;
}

function renderTwinExplorer(play) {
  const paths = play.ghost_paths || [];
  if (!paths.length) {
    $("twinChips").replaceChildren();
    $("twinContributions").replaceChildren();
    return;
  }
  if (!paths.some((path) => path.date === state.selectedTwin)) {
    state.selectedTwin = paths[0].date;
  }
  $("twinChips").replaceChildren(...paths.map((path) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.active = String(path.date === state.selectedTwin);
    button.textContent = `${formatDate(path.date)} · ${path.quality}`;
    button.addEventListener("click", () => selectTwin(path.date));
    return button;
  }));
  renderTwinContributions(play, state.selectedTwin);
}

function selectTwin(date) {
  if (!state.analysis?.playbook.available) return;
  state.selectedTwin = date;
  renderTwinExplorer(state.analysis.playbook);
  renderReceipts(state.analysis.playbook);
  renderChart();
}

function renderTwinContributions(play, date) {
  const match = play.matches.find((item) => item.date === date);
  if (!match) {
    $("twinContributions").replaceChildren();
    return;
  }
  const heading = document.createElement("div");
  heading.className = "twin-contribution-heading";
  const title = document.createElement("strong");
  title.textContent = `${formatDate(match.date)} · ${match.quality}/100 match`;
  const outcome = document.createElement("span");
  outcome.textContent =
    `Then: ${pct(match.fwd_5d)} after 5 · ${pct(match.fwd_10d)} after 10 · ` +
    `${pct(match.fwd_21d)} at the horizon`;
  heading.append(title, outcome);
  const signals = document.createElement("div");
  signals.className = "twin-signal-grid";
  signals.append(...match.contributions.map((item) => {
    const card = document.createElement("div");
    const label = document.createElement("span");
    const score = document.createElement("strong");
    const track = document.createElement("i");
    const fill = document.createElement("b");
    label.textContent = item.label;
    score.textContent = `${item.closeness}% close`;
    fill.style.width = `${item.closeness}%`;
    track.append(fill);
    card.append(label, score, track);
    return card;
  }));
  $("twinContributions").replaceChildren(heading, signals);
}

function planRow(icon, key, value, detail, kind) {
  const row = document.createElement("div");
  row.className = "plan-row";
  if (kind) row.dataset.kind = kind;
  const left = document.createElement("span");
  left.className = "plan-key";
  const glyph = document.createElement("span");
  glyph.className = "plan-icon";
  glyph.textContent = icon;
  const label = document.createTextNode(key);
  left.append(glyph, label);
  const right = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = value;
  right.append(strong);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    right.append(small);
  }
  row.append(left, right);
  return row;
}

function renderPlan(data) {
  const plan = data.playbook.trade_plan;
  const rows = [];
  $("planNote").textContent = plan.note;
  if (plan.action === "consider_buying") {
    $("planTitle").textContent = "A path-supported long plan";
    rows.push(planRow("↳", "Entry reference", money(plan.entry, data.currency)));
    rows.push(planRow("↑", "Path-derived target", money(plan.target, data.currency), pct(plan.target_pct), "target"));
    rows.push(planRow("↓", "Path-derived stop", money(plan.stop, data.currency), pct(plan.stop_pct), "stop"));
    rows.push(planRow("R", "Reward versus risk", `${plan.risk_reward} to 1`));
    rows.push(planRow("✓", "Target touched first", `${plan.matched_path_hit_rate}%`, "on these analog paths"));
    rows.push(planRow("T", "Research horizon", plan.horizon_label));
    $("planSizer").hidden = false;
  } else {
    $("planTitle").textContent =
      plan.action === "avoid_or_exit" ? "No supported long trade" : "A forecast is not a trade";
    rows.push(planRow("—", "Best action right now", plan.action === "avoid_or_exit" ? "Avoid new longs" : "Wait"));
    rows.push(planRow("↻", "Recheck after", "A material price or news change"));
    $("planSizer").hidden = true;
  }
  $("planRows").replaceChildren(...rows);
  updateSizer();
}

function updateSizer() {
  const play = state.analysis?.playbook;
  if (!play?.available || play.trade_plan.action !== "consider_buying") return;
  const plan = play.trade_plan;
  const investment = Math.max(0, Number($("investAmount").value) || 0);
  const units = plan.entry ? investment / plan.entry : 0;
  const gain = investment * plan.target_pct / 100;
  const loss = investment * plan.stop_pct / 100;
  const lines = [
    `About ${units.toLocaleString("en-US", { maximumFractionDigits: 4 })} units`,
    `${money(gain, state.analysis.currency, 0)} if target is reached`,
    `${money(loss, state.analysis.currency, 0)} if stop is reached`,
  ];
  $("sizerResults").replaceChildren(...lines.map((text, index) => {
    const row = document.createElement("div");
    row.textContent = text;
    if (index === 1) row.className = "gain";
    if (index === 2) row.className = "loss";
    return row;
  }));
}

function renderNews(data) {
  const summary = data.news_summary;
  $("newsSub").textContent = summary.count
    ? `${summary.positive} positive · ${summary.negative} negative · ${summary.neutral} neutral. ` +
      `News can nudge the forecast by no more than five points.`
    : "No current headline adjustment was available.";

  if (!data.news.length) {
    const empty = document.createElement("p");
    empty.className = "news-empty";
    empty.textContent = "No recent headlines were found for this symbol.";
    $("newsList").replaceChildren(empty);
    return;
  }

  $("newsList").replaceChildren(...data.news.slice(0, 6).map((article) => {
    const item = document.createElement("div");
    item.className = "news-item";
    const body = document.createElement("div");
    const headline = article.url ? document.createElement("a") : document.createElement("span");
    headline.className = "headline";
    headline.textContent = article.title;
    if (article.url) {
      headline.href = article.url;
      headline.target = "_blank";
      headline.rel = "noopener noreferrer";
    }
    const meta = document.createElement("span");
    meta.className = "news-meta";
    meta.textContent = `${article.publisher} · ${relativeTime(article.published_at)}`;
    body.append(headline, meta);
    const chip = document.createElement("span");
    chip.className = `sentiment-chip ${article.sentiment_label.toLowerCase()}`;
    chip.textContent = article.sentiment_label;
    item.append(body, chip);
    return item;
  }));
}

function relativeTime(value) {
  if (!value) return "recent";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "recent";
  const hours = Math.max(0, Math.round((Date.now() - date.valueOf()) / 3600000));
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function renderChart() {
  const data = state.analysis;
  if (!data) return;
  const svg = $("mainChart");
  const width = 900;
  const height = 360;
  const pad = { top: 24, right: 78, bottom: 22, left: 10 };
  const history = data.history.slice(-120);
  if (history.length < 2) {
    svg.replaceChildren();
    return;
  }

  const currentPrice = history[history.length - 1].close;
  const play = data.playbook;
  const projectionDays = play.available ? play.forecast.horizon_days : 0;
  const totalSteps = history.length - 1 + projectionDays;
  const ghosts = play.available
    ? play.ghost_paths.map((path) => ({
      date: path.date,
      values: path.offsets.map((offset) => currentPrice * (1 + offset / 100)),
    }))
    : [];
  const projection = play.available ? play.projection : null;
  const coneLower = projection
    ? projection.low.map((value) => currentPrice * (1 + value / 100))
    : [];
  const coneMedian = projection
    ? projection.median.map((value) => currentPrice * (1 + value / 100))
    : [];
  const coneUpper = projection
    ? projection.high.map((value) => currentPrice * (1 + value / 100))
    : [];
  const plan = play.available ? play.trade_plan : null;

  const values = [
    ...history.map((point) => point.close),
    ...ghosts.flatMap((path) => path.values),
    ...coneLower,
    ...coneUpper,
    ...(plan?.target ? [plan.target] : []),
    ...(plan?.stop ? [plan.stop] : []),
  ];
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || 1;
  const x = (step) =>
    pad.left + (step / totalSteps) * (width - pad.left - pad.right);
  const y = (value) =>
    pad.top + ((maximum - value) / spread) * (height - pad.top - pad.bottom);
  const nowX = x(history.length - 1);
  const linePath = (series, startStep) =>
    series.map((value, index) =>
      `${index ? "L" : "M"}${x(startStep + index).toFixed(1)},${y(value).toFixed(1)}`)
      .join(" ");

  const parts = [];
  [0.25, 0.5, 0.75].forEach((ratio) => {
    const gridY = pad.top + ratio * (height - pad.top - pad.bottom);
    parts.push(`<line class="chart-grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${gridY}" y2="${gridY}"></line>`);
  });

  if (coneUpper.length > 1) {
    const start = history.length - 1;
    const upper = coneUpper.map((value, index) =>
      `${index ? "L" : "M"}${x(start + index).toFixed(1)},${y(value).toFixed(1)}`)
      .join(" ");
    const lower = coneLower.map((_, index) => {
      const reverse = coneLower.length - 1 - index;
      return `L${x(start + reverse).toFixed(1)},${y(coneLower[reverse]).toFixed(1)}`;
    }).join(" ");
    parts.push(`<path class="chart-cone" d="${upper} ${lower} Z"></path>`);
  }

  ghosts.forEach((path) => {
    const selected = path.date === state.selectedTwin;
    const className = state.selectedTwin
      ? `chart-ghost ${selected ? "selected" : "dimmed"}`
      : "chart-ghost";
    parts.push(
      `<path class="${className}" d="${linePath(path.values, history.length - 1)}"></path>`,
    );
  });
  if (coneMedian.length > 1) {
    parts.push(`<path class="chart-median" d="${linePath(coneMedian, history.length - 1)}"></path>`);
  }

  parts.push(`<line class="chart-divider" x1="${nowX}" x2="${nowX}" y1="${pad.top}" y2="${height - pad.bottom}"></line>`);
  parts.push(`<text class="chart-label" x="${nowX}" y="${pad.top - 8}" text-anchor="middle">TODAY</text>`);
  if (plan?.action === "consider_buying") {
    const targetY = y(plan.target);
    const stopY = y(plan.stop);
    parts.push(`<line class="chart-target-line" x1="${nowX}" x2="${width - pad.right}" y1="${targetY}" y2="${targetY}"></line>`);
    parts.push(`<text class="chart-label up" x="${width - pad.right + 6}" y="${targetY + 4}">Target</text>`);
    parts.push(`<line class="chart-stop-line" x1="${nowX}" x2="${width - pad.right}" y1="${stopY}" y2="${stopY}"></line>`);
    parts.push(`<text class="chart-label down" x="${width - pad.right + 6}" y="${stopY + 4}">Stop</text>`);
  }
  parts.push(`<path class="chart-price" d="${linePath(history.map((point) => point.close), 0)}"></path>`);
  parts.push(`<rect id="chartHitArea" x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>`);
  svg.innerHTML = parts.join("");

  const hitArea = $("chartHitArea");
  hitArea.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const svgX = ((event.clientX - rect.left) / rect.width) * width;
    const ratio = Math.max(
      0,
      Math.min(1, (svgX - pad.left) / (width - pad.left - pad.right)),
    );
    const step = Math.round(ratio * totalSteps);
    const tooltip = $("chartTooltip");
    if (step <= history.length - 1) {
      const point = history[step];
      tooltip.hidden = false;
      tooltip.style.left = `${x(step) / width * 100}%`;
      tooltip.style.top = `${y(point.close) / height * 100}%`;
      tooltip.querySelector("span").textContent =
        new Date(`${point.date}T12:00:00`).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
        });
      tooltip.querySelector("strong").textContent = money(point.close, data.currency);
    } else if (coneMedian.length) {
      const offset = Math.min(step - (history.length - 1), coneMedian.length - 1);
      tooltip.hidden = false;
      tooltip.style.left = `${x(step) / width * 100}%`;
      tooltip.style.top = `${y(coneMedian[offset]) / height * 100}%`;
      tooltip.querySelector("span").textContent =
        `${offset} day${offset === 1 ? "" : "s"} from now · weighted median`;
      tooltip.querySelector("strong").textContent =
        `${money(coneMedian[offset], data.currency)} (${pct(projection.median[offset])})`;
    }
  });
  hitArea.addEventListener("pointerleave", () => {
    $("chartTooltip").hidden = true;
  });
}

function updateWatchButton() {
  const symbol = state.analysis?.symbol;
  const saved = Boolean(symbol && state.watchlist.includes(symbol));
  $("watchButton").setAttribute("aria-pressed", String(saved));
  $("watchButton").textContent = saved ? "★ Watching" : "☆ Watch";
  renderWatchlist();
}

function insightFromAnalysis(data) {
  if (!data?.playbook?.available) return null;
  return {
    symbol: data.symbol,
    price: data.quote.price,
    currency: data.currency,
    direction: data.playbook.verdict.direction,
    probability_up: data.playbook.forecast.probability_up,
    edge_points: data.playbook.forecast.edge_points,
    typical_return: data.playbook.forecast.range_21d.typical,
    evidence_score: data.playbook.forecast.evidence_score,
    stage: data.stage,
    updated_at: new Date().toISOString(),
  };
}

function rememberWatchInsight(data) {
  const insight = insightFromAnalysis(data);
  if (!insight) return;
  state.watchData[data.symbol] = insight;
  const retained = new Set([data.symbol, ...state.watchlist]);
  Object.keys(state.watchData).forEach((symbol) => {
    if (!retained.has(symbol)) delete state.watchData[symbol];
  });
  saveWatchInsights();
}

function renderWatchlist() {
  $("watchlistEmpty").hidden = state.watchlist.length > 0;
  $("watchlist").replaceChildren(...state.watchlist.map((symbol) => {
    const row = document.createElement("div");
    row.className = "watchlist-item";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "open-symbol";
    const name = document.createElement("strong");
    name.textContent = symbol;
    const insight = state.watchData[symbol];
    const detail = document.createElement("span");
    detail.textContent = insight
      ? `${insight.probability_up}% up · ` +
        `${insight.edge_points >= 0 ? "+" : ""}${insight.edge_points} pt edge · ` +
        `${insight.direction}`
      : "Not scanned yet";
    open.append(name, detail);
    open.addEventListener("click", () => loadSymbol(symbol));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-watch";
    remove.setAttribute("aria-label", `Remove ${symbol}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.watchlist = state.watchlist.filter((item) => item !== symbol);
      delete state.watchData[symbol];
      saveWatchlist();
      saveWatchInsights();
      updateWatchButton();
    });
    row.append(open, remove);
    return row;
  }));
}

async function refreshWatchlist() {
  if (!state.watchlist.length) {
    $("watchlistStatus").textContent = "Watch at least one symbol before running a scan.";
    return;
  }
  if (state.watchRefresh) state.watchRefresh.abort();
  const controller = new AbortController();
  state.watchRefresh = controller;
  $("refreshWatchlist").disabled = true;
  const symbols = [...state.watchlist];
  let completed = 0;
  try {
    for (let index = 0; index < symbols.length; index += 1) {
      const symbol = symbols[index];
      $("watchlistStatus").textContent =
        `Quick-scanning ${symbol} · ${index + 1} of ${symbols.length}`;
      const response = await fetch(
        `/api/analyze/${encodeURIComponent(symbol)}/quick`,
        {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        },
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || `${symbol} could not be scanned.`);
      }
      const insight = insightFromAnalysis(data);
      if (insight && state.watchlist.includes(symbol)) {
        state.watchData[symbol] = insight;
        saveWatchInsights();
        completed += 1;
      }
      renderWatchlist();
    }
    $("watchlistStatus").textContent =
      `Compared ${completed} watched setup` +
      `${completed === 1 ? "" : "s"} with preliminary balanced weights.`;
  } catch (error) {
    if (error.name !== "AbortError") {
      $("watchlistStatus").textContent = error.message;
    }
  } finally {
    if (state.watchRefresh === controller) {
      state.watchRefresh = null;
      $("refreshWatchlist").disabled = false;
    }
  }
}

$("symbolForm").addEventListener("submit", (event) => {
  event.preventDefault();
  loadSymbol($("symbolInput").value);
});

document.querySelectorAll("[data-symbol]").forEach((button) => {
  button.addEventListener("click", () => loadSymbol(button.dataset.symbol));
});

$("watchButton").addEventListener("click", () => {
  const symbol = state.analysis?.symbol;
  if (!symbol) return;
  state.watchlist = state.watchlist.includes(symbol)
    ? state.watchlist.filter((item) => item !== symbol)
    : [symbol, ...state.watchlist].slice(0, 10);
  saveWatchlist();
  updateWatchButton();
});

$("toggleReceipts").addEventListener("click", () => {
  state.showAllReceipts = !state.showAllReceipts;
  if (state.analysis?.playbook.available) {
    renderReceipts(state.analysis.playbook);
  }
});

$("investAmount").addEventListener("input", updateSizer);
$("refreshWatchlist").addEventListener("click", refreshWatchlist);
$("timeMachineForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runTimeMachine($("timeMachineDate").value);
});
$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storageSet("playbook-theme", next);
});

const savedTheme = storageGet("playbook-theme")
  || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
document.documentElement.dataset.theme = savedTheme;
renderWatchlist();

const initialSymbol = new URLSearchParams(window.location.search).get("symbol")
  || storageGet("playbook-last-symbol");
if (initialSymbol) loadSymbol(initialSymbol);
