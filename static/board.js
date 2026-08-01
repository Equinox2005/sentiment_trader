const $ = (id) => document.getElementById(id);

const state = {
  side: "long",
  tier: "quality",
  data: null,
  pollTimer: null,
  checking: false,
};

const TIER_RANK = { strong: 3, moderate: 2, speculative: 1 };
const BADGE_CLASS = {
  "long:strong": "badge-strong-long",
  "long:moderate": "badge-long",
  "long:speculative": "badge-weak-long",
  "short:strong": "badge-strong-short",
  "short:moderate": "badge-short",
  "short:speculative": "badge-weak-short",
};

/* ---------------- formatting ---------------- */

function signed(value, digits = 1, suffix = "%") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}${suffix}`;
}

function money(value, currency = "USD") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: number < 1 ? 4 : 2,
    }).format(number);
  } catch {
    return `${number.toFixed(2)} ${currency}`;
  }
}

function runtime(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Number(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${Math.round(total)}s`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function numberCell(label, value, className, note) {
  const wrap = document.createElement("div");
  wrap.append(element("span", "", label));
  wrap.append(element("strong", className || "", value));
  if (note) wrap.append(element("small", "", note));
  return wrap;
}

function detailCell(label, value, note) {
  const wrap = document.createElement("div");
  wrap.append(element("span", "", label));
  wrap.append(element("strong", "", value));
  if (note) wrap.append(element("small", "", note));
  return wrap;
}

/* ---------------- signal card ---------------- */

function badge(item) {
  const wrap = element("div", "signal-badge");
  const label = element(
    "span",
    `badge-label ${BADGE_CLASS[`${item.side}:${item.tier}`] || "badge-neutral"}`,
    item.signal || "NO SIGNAL",
  );
  wrap.append(label);
  wrap.append(
    element(
      "span",
      "badge-score",
      `Conviction ${Number(item.opportunity_score).toFixed(0)}/100`,
    ),
  );
  return wrap;
}

function detailsGrid(item) {
  const grid = element("div", "signal-details");
  const factors = item.ranking_factors || {};
  const range = item.range || {};
  grid.append(
    detailCell(
      "Historical range",
      `${signed(range.low)} to ${signed(range.high)}`,
      "Adjusted 20th–80th percentile",
    ),
    detailCell(
      "Reward vs risk",
      `${Number(item.reward_risk || 0).toFixed(2)}×`,
      "Typical move ÷ adverse move",
    ),
    detailCell(
      "Probability edge",
      signed(item.edge_points, 1, " pts"),
      `Analogs ${item.analog_probability_up}% vs normal ${item.baseline_up_rate}%`,
    ),
    detailCell("Evidence quality", `${item.evidence_score}/100`, "Independent matches and years"),
    detailCell(
      "Signal agreement",
      `${Math.round(item.agreement?.score || 0)}%`,
      item.agreement?.label || "Across paths, median, and news",
    ),
    detailCell(
      "Untouched audit",
      item.validation_label || item.validation_grade || "—",
      `${item.validation_sample_size || 0} graded forecasts`,
    ),
    detailCell(
      "Skill vs baseline",
      signed(item.brier_skill),
      "Brier skill — higher is better",
    ),
    detailCell(
      "Matches used",
      `${item.match_count || 0}`,
      `${Number(item.effective_matches || 0).toFixed(1)} effective · ${item.distinct_years || 0} years`,
    ),
    detailCell("Sector", item.sector || "—", item.horizon_label || ""),
    detailCell(
      "Interval width",
      `${Number(factors.interval_width || 0).toFixed(1)} pts`,
      "Wider ranges lower the score",
    ),
  );
  return grid;
}

function signalCard(item, index) {
  const card = element("article", "signal-card");
  card.dataset.side = item.side;
  card.dataset.tier = item.tier;

  const head = element("div", "signal-head");
  head.append(element("span", "signal-rank", `#${item.rank || index + 1}`));

  const identity = element("div", "signal-identity");
  const line = element("div", "signal-ticker");
  line.append(element("h3", "", item.display_symbol || item.symbol));
  line.append(element("span", "signal-price", money(item.price, item.currency)));
  identity.append(line);
  identity.append(
    element("p", "signal-company", item.name || item.company_name || item.symbol),
  );
  head.append(identity, badge(item));

  const numbers = element("div", "signal-numbers");
  const moveClass = item.side === "long" ? "value-up" : "value-down";
  numbers.append(
    numberCell(
      item.side === "long" ? "Typical gain" : "Typical drop",
      signed(item.side === "long" ? item.expected_move : -item.expected_move),
      moveClass,
      item.horizon_label,
    ),
    numberCell(
      item.side === "long" ? "Odds it rises" : "Odds it falls",
      `${item.win_probability}%`,
      "",
      `Normally ${item.side === "long" ? item.baseline_up_rate : 100 - item.baseline_up_rate}%`,
    ),
    numberCell(
      "Risk if wrong",
      `-${Number(item.adverse_move).toFixed(1)}%`,
      // Always a loss in P&L terms, whichever side the trade is on.
      "value-down",
      item.side === "long"
        ? "Adverse tail of matched paths"
        : "If it rallies instead",
    ),
    numberCell(
      "Reward / risk",
      `${Number(item.reward_risk || 0).toFixed(1)}×`,
      "",
      "Above 1.0 is favourable",
    ),
  );

  const body = element("div", "signal-body");
  body.append(element("p", "signal-reason", item.reason || ""));
  if (item.news_conflict) {
    body.append(
      element(
        "span",
        "signal-flag",
        "⚠ Today’s headlines push against this historical lean",
      ),
    );
  }

  const details = detailsGrid(item);
  const actions = element("div", "signal-actions");
  const toggle = element("button", "detail-toggle", "Show details");
  toggle.type = "button";
  toggle.addEventListener("click", () => {
    const open = details.classList.toggle("is-open");
    toggle.textContent = open ? "Hide details" : "Show details";
  });
  const link = element("a", "signal-link", "Full historical analysis →");
  link.href = `/forecast/${encodeURIComponent(item.symbol)}`;
  actions.append(toggle, link);

  body.append(actions, details);
  card.append(head, numbers, body);
  return card;
}

/* ---------------- board rendering ---------------- */

function filtered(items) {
  if (state.tier === "all") return items;
  const floor = state.tier === "strong" ? 3 : 2;
  return items.filter((item) => (TIER_RANK[item.tier] || 0) >= floor);
}

function renderBoard() {
  const data = state.data;
  const list = $("boardList");
  if (!data || !data.available) {
    list.replaceChildren();
    $("boardEmpty").hidden = false;
    $("boardEmpty").textContent =
      (data && data.message) ||
      "No completed scan exists yet. The first nightly run will populate this board.";
    return;
  }

  const all = state.side === "long" ? data.longs || [] : data.shorts || [];
  const shown = filtered(all);
  list.replaceChildren(...shown.map(signalCard));

  $("boardEmpty").hidden = shown.length > 0;
  if (!shown.length) {
    $("boardEmpty").textContent = all.length
      ? `No ${state.side === "long" ? "buy" : "short"} signal met that strength filter today. Loosen it to see the ${all.length} weaker ${all.length === 1 ? "candidate" : "candidates"}.`
      : `Nothing on the ${state.side === "long" ? "long" : "short"} side cleared the evidence bar for this session. That is a valid result, not a reason to lower the standard.`;
  }

  $("boardNote").textContent = `Showing ${shown.length} of ${all.length} ${
    state.side === "long" ? "buy" : "short"
  } candidates · ${data.run.total_count} stocks scanned`;
}

function renderMeta(data) {
  $("longCount").textContent = data.available ? String(data.long_count) : "—";
  $("shortCount").textContent = data.available ? String(data.short_count) : "—";
  $("methodologyNote").textContent = data.methodology || "";

  if (!data.available) {
    $("scanChip").textContent = "No scan yet";
    return;
  }
  const run = data.run;
  const failed = run.failed_count ? ` · ${run.failed_count} unavailable` : "";
  $("scanChip").textContent =
    `${run.session_date} close · ${run.processed_count}/${run.total_count} scanned in ${runtime(run.runtime_seconds)}${failed}`;
}

function renderActive(run) {
  $("activeScan").hidden = !run;
  if (!run) return;
  $("activeScanText").textContent =
    `${run.processed_count} of ${run.total_count} stocks processed · ${run.progress_percent}%`;
  $("activeScanBar").style.width = `${run.progress_percent}%`;
}

async function loadBoard() {
  try {
    const response = await fetch("/api/opportunities/latest?limit=250", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The signal board is unavailable.");
    state.data = data;
    renderMeta(data);
    renderActive(data.active_run);
    renderBoard();
    if (data.active_run && !state.pollTimer) {
      state.pollTimer = window.setTimeout(() => {
        state.pollTimer = null;
        loadBoard();
      }, 20000);
    }
  } catch (error) {
    $("scanChip").textContent = error.message;
  }
}

/* ---------------- inline ticker checker ---------------- */

function sparkline(points) {
  if (!points || points.length < 2) return null;
  const width = 600;
  const height = 64;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const path = points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 8) - 4;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "check-spark");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("d", path);
  line.setAttribute(
    "stroke",
    points[points.length - 1] >= points[0] ? "var(--up)" : "var(--down)",
  );
  svg.append(line);
  return svg;
}

function neutralCard(result) {
  const card = element("article", "check-card");
  card.dataset.side = "none";
  const head = element("div", "signal-head");
  head.append(element("span", "signal-rank", "—"));
  const identity = element("div", "signal-identity");
  const line = element("div", "signal-ticker");
  line.append(element("h3", "", result.symbol));
  line.append(element("span", "signal-price", money(result.quote?.price, result.currency)));
  identity.append(line);
  identity.append(element("p", "signal-company", result.name || result.symbol));
  head.append(identity);

  const wrap = element("div", "signal-badge");
  wrap.append(element("span", "badge-label badge-neutral", "NO CLEAR SIGNAL"));
  wrap.append(
    element(
      "span",
      "badge-score",
      result.side
        ? `Leans ${result.side === "long" ? "up" : "down"}, but below the bar`
        : "Stand aside",
    ),
  );
  head.append(wrap);

  const body = element("div", "signal-body");
  body.append(
    element(
      "p",
      "signal-reason",
      result.reason ||
        "The closest historical setups did not lean far enough in either direction to justify a trade.",
    ),
  );
  const actions = element("div", "signal-actions");
  const link = element("a", "signal-link", "Full historical analysis →");
  link.href = `/forecast/${encodeURIComponent(result.symbol)}`;
  actions.append(link);
  body.append(actions);

  card.append(head);
  const spark = sparkline(result.spark);
  if (spark) card.append(spark);
  card.append(body);
  return card;
}

function checkCard(result) {
  // A directional lean that failed the board's guardrails is still a "stand
  // aside", so it must not be dressed up with a buy or short badge.
  if (!result.side || !result.eligible) return neutralCard(result);
  const card = signalCard(
    { ...result, rank: null, name: result.name || result.symbol },
    0,
  );
  card.classList.add("check-card");
  card.querySelector(".signal-rank").textContent = "✓";
  const spark = sparkline(result.spark);
  if (spark) card.insertBefore(spark, card.querySelector(".signal-numbers"));
  return card;
}

let checkProgress = null;

function startCheckProgress() {
  let value = 5;
  $("checkBar").style.width = "5%";
  checkProgress = window.setInterval(() => {
    value = Math.min(92, value + Math.max(1, (92 - value) * 0.12));
    $("checkBar").style.width = `${value}%`;
  }, 400);
}

function stopCheckProgress() {
  if (checkProgress) window.clearInterval(checkProgress);
  checkProgress = null;
  $("checkBar").style.width = "100%";
}

async function checkSymbol(raw) {
  const symbol = String(raw || "").trim().toUpperCase();
  if (!symbol || state.checking) return;
  state.checking = true;
  $("checkButton").disabled = true;
  $("checkPanel").hidden = false;
  $("checkError").hidden = true;
  $("checkCard").replaceChildren();
  $("checkLoading").hidden = false;
  $("checkLoadingText").textContent = `Matching ${symbol} against twenty years of history…`;
  startCheckProgress();

  try {
    const response = await fetch(`/api/signal/${encodeURIComponent(symbol)}`, {
      headers: { Accept: "application/json" },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "That symbol could not be analyzed.");
    if (!data.playbook_available) {
      throw new Error(
        data.reason ||
          "There is not enough clean history for this symbol to build a forecast.",
      );
    }
    $("checkCard").replaceChildren(checkCard(data));
    window.history.replaceState(null, "", `/?symbol=${encodeURIComponent(symbol)}`);
  } catch (error) {
    $("checkError").hidden = false;
    $("checkError").textContent = error.message;
  } finally {
    stopCheckProgress();
    $("checkLoading").hidden = true;
    $("checkButton").disabled = false;
    state.checking = false;
  }
}

/* ---------------- wiring ---------------- */

function setTheme() {
  let stored = null;
  try { stored = localStorage.getItem("playbook-theme"); } catch { /* optional */ }
  document.documentElement.dataset.theme =
    stored || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
}

setTheme();

$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("playbook-theme", next); } catch { /* optional */ }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.side = tab.dataset.side;
    document.querySelectorAll(".tab").forEach((other) => {
      const active = other === tab;
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-selected", String(active));
    });
    renderBoard();
  });
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    state.tier = chip.dataset.tier;
    document.querySelectorAll(".chip").forEach((other) => {
      other.classList.toggle("is-active", other === chip);
    });
    renderBoard();
  });
});

$("checkForm").addEventListener("submit", (event) => {
  event.preventDefault();
  checkSymbol($("checkInput").value);
});

document.querySelectorAll(".quick-picks button").forEach((button) => {
  button.addEventListener("click", () => {
    $("checkInput").value = button.dataset.symbol;
    checkSymbol(button.dataset.symbol);
  });
});

loadBoard();

const requested = new URLSearchParams(window.location.search).get("symbol");
if (requested) {
  $("checkInput").value = requested.toUpperCase();
  checkSymbol(requested);
}
