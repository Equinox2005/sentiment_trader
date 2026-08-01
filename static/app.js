const state = {
  analysis: null,
  request: null,
  showAllReceipts: false,
  watchlist: readStoredList(),
};

const $ = (id) => document.getElementById(id);

const money = (value, code = "USD", digits) => {
  const d = digits !== undefined ? digits : (value < 1 ? 4 : 2);
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: code,
      minimumFractionDigits: d, maximumFractionDigits: d,
    }).format(value);
  } catch {
    return `${Number(value).toFixed(d)} ${code}`;
  }
};

const pct = (value, digits = 1) => `${value >= 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
const tone = (value) => (value > 0.001 ? "positive" : value < -0.001 ? "negative" : "neutral");

function storageGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch { /* optional */ } }

function readStoredList() {
  try {
    const value = JSON.parse(storageGet("playbook-watchlist") || "[]");
    return Array.isArray(value) ? value.filter((s) => typeof s === "string").slice(0, 10) : [];
  } catch { return []; }
}

function saveWatchlist() { storageSet("playbook-watchlist", JSON.stringify(state.watchlist)); }

/* ================= LOADING ================= */

const LOADING_LINES = [
  "Scanning ten years of history…",
  "Fingerprinting today's setup…",
  "Finding the closest look-alike days…",
  "Checking what happened next…",
  "Reading today's headlines…",
];
let loadingTimer = null;

function startLoading() {
  let i = 0;
  clearInterval(loadingTimer);
  $("loadingStrip").hidden = false;
  $("loadingText").textContent = LOADING_LINES[0];
  loadingTimer = setInterval(() => {
    i = (i + 1) % LOADING_LINES.length;
    $("loadingText").textContent = LOADING_LINES[i];
  }, 1400);
}

function stopLoading() {
  clearInterval(loadingTimer);
  $("loadingStrip").hidden = true;
}

/* ================= FETCH ================= */

async function loadSymbol(rawSymbol) {
  const symbol = String(rawSymbol || "").trim().toUpperCase().replace(/^\$/, "");
  if (!symbol) { $("symbolInput").focus(); return; }

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
    if (!response.ok) throw new Error(payload.error || "Something went wrong building the playbook.");
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

/* ================= RENDER ================= */

function render(data) {
  $("workspace").hidden = false;
  const play = data.playbook;

  $("assetSymbol").textContent = data.symbol;
  $("assetName").textContent = [data.name, data.sector].filter(Boolean).join(" · ");
  $("assetPrice").textContent = money(data.quote.price, data.currency);
  $("dailyChange").textContent = `${pct(data.quote.daily_change, 2)} today`;
  $("dailyChange").dataset.tone = tone(data.quote.daily_change);

  if (play.available) {
    renderVerdict(data);
    renderPlan(data);
    renderOdds(play);
    renderReceipts(play);
  } else {
    renderUnavailable(play);
  }

  renderStory(data.story);
  renderNews(data);
  renderChart();
  updateWatchButton();

  $("methodologyText").textContent =
    `${data.methodology} It found ${play.available ? play.stats.count : 0} comparable setups in ` +
    `${data.history_years} years of data. What it can't do: see tomorrow's news, know about ` +
    `earnings dates or events, or guarantee any outcome. Patterns break. Use the stop-loss.`;
}

function renderVerdict(data) {
  const verdict = data.playbook.verdict;
  const card = $("verdictCard");
  card.dataset.direction = verdict.direction;
  $("verdictArrow").textContent =
    verdict.direction === "bullish" ? "↗" : verdict.direction === "bearish" ? "↘" : "⇄";
  $("verdictHeadline").textContent = verdict.headline;
  $("verdictExplanation").textContent = verdict.explanation;
  $("gaugeFill").style.width = `${verdict.confidence}%`;
  $("confidenceLabel").textContent = `Signal strength: ${verdict.confidence}/100`;
  $("setupLine").textContent = data.playbook.setup;
  $("setupLine").hidden = false;
}

function renderUnavailable(play) {
  const card = $("verdictCard");
  card.dataset.direction = "neutral";
  $("verdictArrow").textContent = "…";
  $("verdictHeadline").textContent = "Not enough history for a playbook";
  $("verdictExplanation").textContent = play.reason;
  $("gaugeFill").style.width = "0%";
  $("confidenceLabel").textContent = "No signal";
  $("setupLine").hidden = true;
  $("planRows").replaceChildren();
  $("planNote").textContent = "A trade plan needs a playbook first.";
  $("sizerResults").replaceChildren();
  $("oddsVisual").replaceChildren();
  $("oddsStats").replaceChildren();
  $("receiptsBody").replaceChildren();
}

function renderStory(story) {
  const box = $("storyCheck");
  if (!story) { box.hidden = true; return; }
  box.hidden = false;
  box.dataset.state = story.state;
  $("storyIcon").textContent =
    story.state === "confirms" ? "✅" : story.state === "conflicts" ? "⚠️" : "📰";
  $("storySummary").textContent = story.summary;
}

/* ================= TRADE PLAN ================= */

function renderPlan(data) {
  const plan = data.playbook.trade_plan;
  const code = data.currency;
  const rows = [];

  const row = (icon, key, valueHtml, kind) => {
    const el = document.createElement("div");
    el.className = "plan-row";
    if (kind) el.dataset.kind = kind;
    const left = document.createElement("span");
    left.className = "plan-key";
    left.innerHTML = `<span class="plan-icon">${icon}</span>${key}`;
    const right = document.createElement("span");
    right.innerHTML = valueHtml;
    el.append(left, right);
    return el;
  };

  if (plan.action === "consider_buying") {
    $("planTitle").textContent = "If you take this trade";
    $("planNote").textContent = plan.note;
    rows.push(row("🛒", "Buy near", `<strong>${money(plan.entry, code)}</strong>`));
    rows.push(row("🎯", "Take profit at", `<strong>${money(plan.target, code)}</strong><small>${pct(plan.target_pct)}</small>`, "target"));
    rows.push(row("🛑", "Cut losses at", `<strong>${money(plan.stop, code)}</strong><small>${pct(plan.stop_pct)}</small>`, "stop"));
    rows.push(row("⚖️", "Reward vs. risk", `<strong>${plan.risk_reward} to 1</strong>`));
    rows.push(row("⏳", "Give it", `<strong>about ${plan.horizon_days} trading days</strong>`));
    $("planSizer").hidden = false;
  } else {
    $("planTitle").textContent = plan.action === "avoid_or_exit" ? "The smart move: stay out" : "The smart move: wait";
    $("planNote").textContent = plan.note;
    rows.push(row(plan.action === "avoid_or_exit" ? "🚫" : "⏸️", "Best action right now",
      `<strong>${plan.action === "avoid_or_exit" ? "Don't buy / protect gains" : "Sit on your hands"}</strong>`));
    rows.push(row("🔁", "Check back", "<strong>after the next big move or news</strong>"));
    $("planSizer").hidden = true;
  }

  $("planRows").replaceChildren(...rows);
  updateSizer();
}

function updateSizer() {
  const play = state.analysis?.playbook;
  if (!play?.available || play.trade_plan.action !== "consider_buying") return;
  const plan = play.trade_plan;
  const code = state.analysis.currency;
  const invest = Math.max(0, Number($("investAmount").value) || 0);
  const units = plan.entry ? invest / plan.entry : 0;
  const gain = invest * (plan.target_pct / 100);
  const loss = invest * (plan.stop_pct / 100);

  const results = $("sizerResults");
  results.innerHTML = "";
  const lines = [
    `You'd get about <strong>${units.toLocaleString("en-US", { maximumFractionDigits: 4 })} ${units === 1 ? "share" : "shares/units"}</strong>.`,
    `If it hits the target: <span class="gain">${money(gain, code, 0)} profit</span>.`,
    `If it hits the stop: <span class="loss">${money(loss, code, 0)} loss</span> — and you're out, no second-guessing.`,
  ];
  lines.forEach((html) => {
    const p = document.createElement("div");
    p.innerHTML = html;
    results.append(p);
  });
}

/* ================= ODDS ================= */

function renderOdds(play) {
  const stats = play.stats;
  $("oddsSub").textContent =
    `Based on the ${stats.count} most similar setups found in this asset's own history.`;

  const dots = play.matches.map((match) => {
    const dot = document.createElement("span");
    const win = match.fwd_21d > 0;
    dot.className = `odds-dot ${win ? "win" : "loss"}`;
    dot.textContent = win ? "↑" : "↓";
    dot.title = `${match.date}: ${pct(match.fwd_21d)} one month later`;
    return dot;
  });
  $("oddsVisual").replaceChildren(...dots);

  const stat = (label, valueHtml) => {
    const el = document.createElement("div");
    el.className = "odds-stat";
    el.innerHTML = `<span>${label}</span>${valueHtml}`;
    return el;
  };

  $("oddsStats").replaceChildren(
    stat("Chance it was higher a month later",
      `<strong class="${stats.win_rate_21d >= 55 ? "up" : stats.win_rate_21d <= 45 ? "down" : ""}">${stats.win_rate_21d}%</strong>`),
    stat("Typical move over that month",
      `<strong class="${stats.median_21d > 0 ? "up" : stats.median_21d < 0 ? "down" : ""}">${pct(stats.median_21d)}</strong>`),
    stat("Best case seen", `<strong class="up">${pct(stats.best_21d)}</strong>`),
    stat("Worst case seen", `<strong class="down">${pct(stats.worst_21d)}</strong>`),
  );
}

/* ================= RECEIPTS ================= */

function renderReceipts(play) {
  const matches = state.showAllReceipts ? play.matches : play.matches.slice(0, 6);
  const rows = matches.map((match) => {
    const tr = document.createElement("tr");
    const cell = (html, className) => {
      const td = document.createElement("td");
      if (className) td.className = className;
      td.innerHTML = html;
      return td;
    };
    const pctCell = (value) =>
      cell(pct(value), `pct ${value > 0 ? "up" : value < 0 ? "down" : ""}`);
    tr.append(
      cell(new Date(`${match.date}T12:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })),
      cell(`<span class="match-badge">${match.similarity}% similar</span>`),
      pctCell(match.fwd_5d),
      pctCell(match.fwd_10d),
      pctCell(match.fwd_21d),
    );
    return tr;
  });
  $("receiptsBody").replaceChildren(...rows);
  $("toggleReceipts").textContent = state.showAllReceipts
    ? "Show fewer"
    : `Show all ${play.matches.length} matches`;
  $("toggleReceipts").hidden = play.matches.length <= 6;
}

/* ================= NEWS ================= */

function renderNews(data) {
  const list = $("newsList");
  const summary = data.news_summary;
  $("newsSub").textContent = summary.count
    ? `${summary.positive} positive · ${summary.negative} negative · ${summary.neutral} neutral`
    : "";

  if (!data.news.length) {
    const empty = document.createElement("p");
    empty.className = "news-empty";
    empty.textContent = "No recent headlines were found for this symbol.";
    list.replaceChildren(empty);
    return;
  }

  list.replaceChildren(...data.news.slice(0, 8).map((article) => {
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

/* ================= CHART ================= */

function renderChart() {
  const data = state.analysis;
  if (!data) return;
  const svg = $("mainChart");
  const width = 900;
  const height = 360;
  const pad = { top: 24, right: 74, bottom: 22, left: 10 };

  const history = data.history.slice(-120);
  if (history.length < 2) { svg.innerHTML = ""; return; }
  const currentPrice = history[history.length - 1].close;

  const play = data.playbook;
  const projectionDays = play.available ? 21 : 0;
  const totalSteps = history.length - 1 + projectionDays;

  // Build ghost path absolute prices
  const ghosts = play.available
    ? play.ghost_paths.map((path) => path.offsets.map((offset) => currentPrice * (1 + offset / 100)))
    : [];

  // Cone from per-step percentile envelope of ghost offsets
  let coneUpper = [], coneLower = [], coneMedian = [];
  if (ghosts.length >= 3) {
    const steps = Math.min(...play.ghost_paths.map((p) => p.offsets.length));
    for (let step = 0; step < Math.min(steps, projectionDays + 1); step++) {
      const values = play.ghost_paths.map((p) => p.offsets[step]).sort((a, b) => a - b);
      const q = (fraction) => {
        const index = (values.length - 1) * fraction;
        const low = Math.floor(index), high = Math.ceil(index);
        return values[low] + (values[high] - values[low]) * (index - low);
      };
      coneUpper.push(currentPrice * (1 + q(0.8) / 100));
      coneLower.push(currentPrice * (1 + q(0.2) / 100));
      coneMedian.push(currentPrice * (1 + q(0.5) / 100));
    }
  }

  const plan = play.available ? play.trade_plan : null;
  const allValues = [
    ...history.map((point) => point.close),
    ...ghosts.flat(),
    ...coneUpper, ...coneLower,
    ...(plan && plan.target ? [plan.target] : []),
    ...(plan && plan.stop ? [plan.stop] : []),
  ];
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const spread = max - min || 1;

  const x = (step) => pad.left + (step / totalSteps) * (width - pad.left - pad.right);
  const y = (value) => pad.top + ((max - value) / spread) * (height - pad.top - pad.bottom);
  const nowX = x(history.length - 1);

  const linePath = (values, startStep) =>
    values.map((value, index) => `${index ? "L" : "M"}${x(startStep + index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");

  let parts = [];

  // grid
  [0.25, 0.5, 0.75].forEach((ratio) => {
    const gy = pad.top + ratio * (height - pad.top - pad.bottom);
    parts.push(`<line class="chart-grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${gy}" y2="${gy}"></line>`);
  });

  // cone
  if (coneUpper.length > 1) {
    const start = history.length - 1;
    const upper = coneUpper.map((value, index) => `${index ? "L" : "M"}${x(start + index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
    const lower = coneLower.map((value, index) => `L${x(start + coneLower.length - 1 - index).toFixed(1)},${y(coneLower[coneLower.length - 1 - index]).toFixed(1)}`).join(" ");
    parts.push(`<path class="chart-cone" d="${upper} ${lower} Z"></path>`);
  }

  // ghosts
  ghosts.forEach((values) => {
    parts.push(`<path class="chart-ghost" d="${linePath(values, history.length - 1)}"></path>`);
  });

  // median projection
  if (coneMedian.length > 1) {
    parts.push(`<path class="chart-median" d="${linePath(coneMedian, history.length - 1)}"></path>`);
  }

  // today divider
  parts.push(`<line class="chart-divider" x1="${nowX}" x2="${nowX}" y1="${pad.top}" y2="${height - pad.bottom}"></line>`);
  parts.push(`<text class="chart-label" x="${nowX}" y="${pad.top - 8}" text-anchor="middle">TODAY</text>`);

  // plan lines
  if (plan && plan.action === "consider_buying") {
    const ty = y(plan.target);
    const sy = y(plan.stop);
    parts.push(`<line class="chart-target-line" x1="${nowX}" x2="${width - pad.right}" y1="${ty}" y2="${ty}"></line>`);
    parts.push(`<text class="chart-label up" x="${width - pad.right + 6}" y="${ty + 4}">Target</text>`);
    parts.push(`<line class="chart-stop-line" x1="${nowX}" x2="${width - pad.right}" y1="${sy}" y2="${sy}"></line>`);
    parts.push(`<text class="chart-label down" x="${width - pad.right + 6}" y="${sy + 4}">Stop</text>`);
  }

  // actual price on top
  parts.push(`<path class="chart-price" d="${linePath(history.map((point) => point.close), 0)}"></path>`);
  parts.push(`<rect id="chartHitArea" x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>`);

  svg.innerHTML = parts.join("");

  // tooltip on the historical section
  const hit = $("chartHitArea");
  hit.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const step = Math.round(ratio * totalSteps);
    const tooltip = $("chartTooltip");
    if (step <= history.length - 1) {
      const point = history[step];
      tooltip.hidden = false;
      tooltip.style.left = `${(x(step) / width) * 100}%`;
      tooltip.style.top = `${(y(point.close) / height) * 100}%`;
      tooltip.querySelector("span").textContent =
        new Date(`${point.date}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      tooltip.querySelector("strong").textContent = money(point.close, data.currency);
    } else if (coneMedian.length) {
      const offset = Math.min(step - (history.length - 1), coneMedian.length - 1);
      tooltip.hidden = false;
      tooltip.style.left = `${(x(step) / width) * 100}%`;
      tooltip.style.top = `${(y(coneMedian[offset]) / height) * 100}%`;
      tooltip.querySelector("span").textContent = `${offset} trading day${offset === 1 ? "" : "s"} from now (typical path)`;
      tooltip.querySelector("strong").textContent = money(coneMedian[offset], data.currency);
    }
  });
  hit.addEventListener("pointerleave", () => { $("chartTooltip").hidden = true; });
}

/* ================= WATCHLIST ================= */

function updateWatchButton() {
  const symbol = state.analysis?.symbol;
  const saved = Boolean(symbol && state.watchlist.includes(symbol));
  $("watchButton").setAttribute("aria-pressed", String(saved));
  $("watchButton").textContent = saved ? "★ Watching" : "☆ Watch";
  renderWatchlist();
}

function renderWatchlist() {
  const container = $("watchlist");
  $("watchlistEmpty").hidden = state.watchlist.length > 0;
  container.replaceChildren(...state.watchlist.map((symbol) => {
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

/* ================= EVENTS ================= */

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
  if (state.analysis?.playbook.available) renderReceipts(state.analysis.playbook);
});

$("investAmount").addEventListener("input", updateSizer);

$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storageSet("playbook-theme", next);
});

/* ================= INIT ================= */

const savedTheme = storageGet("playbook-theme")
  || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
document.documentElement.dataset.theme = savedTheme;
renderWatchlist();

const initialSymbol = new URLSearchParams(window.location.search).get("symbol")
  || storageGet("playbook-last-symbol");
if (initialSymbol) loadSymbol(initialSymbol);
