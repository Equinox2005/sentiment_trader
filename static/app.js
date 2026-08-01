const state = {
  analysis: null,
  request: null,
  showAllReceipts: false,
  watchlist: readStoredList(),
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

function saveWatchlist() {
  storageSet("playbook-watchlist", JSON.stringify(state.watchlist));
}

const LOADING_LINES = [
  "Building today’s market fingerprint…",
  "Comparing the 21-day chart shape…",
  "Searching twenty years for independent twins…",
  "Replaying every matched future…",
  "Running untouched walk-forward checks…",
  "Applying today’s bounded news adjustment…",
];
let loadingTimer = null;

function startLoading() {
  let index = 0;
  clearInterval(loadingTimer);
  $("loadingStrip").hidden = false;
  $("loadingText").textContent = LOADING_LINES[0];
  loadingTimer = setInterval(() => {
    index = (index + 1) % LOADING_LINES.length;
    $("loadingText").textContent = LOADING_LINES[index];
  }, 1500);
}

function stopLoading() {
  clearInterval(loadingTimer);
  loadingTimer = null;
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
  const controller = new AbortController();
  state.request = controller;
  $("statusMessage").textContent = "";
  $("symbolInput").value = symbol;
  startLoading();

  try {
    const response = await fetch(`/api/analyze/${encodeURIComponent(symbol)}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Playbook could not build this forecast.");
    }
    if (state.request !== controller) return;

    state.analysis = payload;
    state.showAllReceipts = false;
    render(payload);
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", payload.symbol);
    window.history.replaceState({}, "", url);
    storageSet("playbook-last-symbol", payload.symbol);
  } catch (error) {
    if (error.name !== "AbortError" && state.request === controller) {
      $("statusMessage").textContent = error.message;
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
    renderReceipts(play);
    renderPlan(data);
    renderCatalyst(play.catalyst);
  } else {
    renderUnavailable(play);
  }

  renderStory(data.story);
  renderNews(data);
  renderChart();
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
  $("horizonKicker").textContent = `${forecast.horizon_label} analog forecast`;
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
}

function renderReliability(validation) {
  if (!validation.available) {
    $("reliabilityGrade").textContent = "Not enough checks";
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
  renderReliability({ available: false, reason: play.reason });
  $("receiptsBody").replaceChildren();
  $("toggleReceipts").hidden = true;
  $("planRows").replaceChildren();
  $("planTitle").textContent = "No path-supported plan";
  $("planNote").textContent = "A defensible risk plan requires historical analog evidence first.";
  $("planSizer").hidden = true;
  $("catalystWarning").hidden = true;
  $("chartTooltip").hidden = true;
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
    return row;
  });
  $("receiptsBody").replaceChildren(...rows);
  $("toggleReceipts").textContent = state.showAllReceipts
    ? "Show closest eight"
    : `Inspect all ${play.matches.length} twins`;
  $("toggleReceipts").hidden = play.matches.length <= 8;
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
    ? play.ghost_paths.map((path) =>
      path.offsets.map((offset) => currentPrice * (1 + offset / 100)))
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
    ...ghosts.flat(),
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
    parts.push(`<path class="chart-ghost" d="${linePath(path, history.length - 1)}"></path>`);
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

function renderWatchlist() {
  $("watchlistEmpty").hidden = state.watchlist.length > 0;
  $("watchlist").replaceChildren(...state.watchlist.map((symbol) => {
    const row = document.createElement("div");
    row.className = "watchlist-item";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "open-symbol";
    open.textContent = symbol;
    open.addEventListener("click", () => loadSymbol(symbol));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-watch";
    remove.setAttribute("aria-label", `Remove ${symbol}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.watchlist = state.watchlist.filter((item) => item !== symbol);
      saveWatchlist();
      updateWatchButton();
    });
    row.append(open, remove);
    return row;
  }));
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
