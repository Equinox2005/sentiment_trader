const $ = (id) => document.getElementById(id);
let pollTimer = null;

function money(value, currency = "USD") {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: Number(value) < 1 ? 4 : 2,
    }).format(value);
  } catch {
    return `${Number(value).toFixed(2)} ${currency}`;
  }
}

function signed(value, suffix = "%") {
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(1)}${suffix}`;
}

function runtime(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const minutes = Math.floor(Number(seconds) / 60);
  const remainder = Math.round(Number(seconds) % 60);
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function setTheme() {
  let stored = null;
  try { stored = localStorage.getItem("playbook-theme"); } catch { /* optional */ }
  document.documentElement.dataset.theme = stored
    || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
}

function renderActive(run) {
  $("activeScan").hidden = !run;
  if (!run) return;
  $("activeScanText").textContent =
    `${run.processed_count} of ${run.total_count} constituents processed · ` +
    `${run.progress_percent}%`;
  $("activeScanBar").style.width = `${run.progress_percent}%`;
}

function metric(label, value, className = "") {
  const element = document.createElement("div");
  const name = document.createElement("span");
  name.textContent = label;
  const amount = document.createElement("strong");
  amount.textContent = value;
  if (className) amount.className = className;
  element.append(name, amount);
  return element;
}

function opportunityCard(item) {
  const article = document.createElement("article");
  article.className = "opportunity-card";

  const heading = document.createElement("div");
  heading.className = "opportunity-heading";
  const rank = document.createElement("span");
  rank.className = "opportunity-rank";
  rank.textContent = `#${item.rank}`;
  const identity = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = item.display_symbol || item.symbol;
  const name = document.createElement("p");
  name.textContent = `${item.name || item.company_name} · ${item.sector || "S&P 500"}`;
  identity.append(title, name);
  const score = document.createElement("div");
  score.className = "opportunity-score";
  score.innerHTML = `<strong>${Number(item.opportunity_score).toFixed(1)}</strong><span>risk-adjusted score</span>`;
  heading.append(rank, identity, score);

  const thesis = document.createElement("div");
  thesis.className = "opportunity-thesis";
  const increase = document.createElement("div");
  increase.innerHTML =
    `<span>Predicted median increase</span><strong>${signed(item.range.typical)}</strong>` +
    `<small>${item.horizon_label}</small>`;
  const range = document.createElement("div");
  range.innerHTML =
    `<span>Historical outcome range</span><strong>${signed(item.range.low)} to ${signed(item.range.high)}</strong>` +
    `<small>Adjusted 20th–80th percentile</small>`;
  thesis.append(increase, range);

  const metrics = document.createElement("div");
  metrics.className = "opportunity-metrics";
  metrics.append(
    metric("Chance higher", `${item.probability_up}%`),
    metric("Analog edge", signed(item.edge_points, " pts"), "positive"),
    metric("Evidence", `${item.evidence_score}/100`),
    metric("Agreement", `${item.agreement?.score || 0}%`),
    metric("Brier skill", signed(item.brier_skill), Number(item.brier_skill) > 0 ? "positive" : "negative"),
    metric("Audit sample", `${item.validation_sample_size} forecasts`),
  );

  const footer = document.createElement("div");
  footer.className = "opportunity-footer";
  const reason = document.createElement("p");
  reason.textContent = item.reason;
  const link = document.createElement("a");
  link.href = `/forecast/${encodeURIComponent(item.symbol)}`;
  link.textContent = "Open complete forecast →";
  footer.append(reason, link);
  article.append(heading, thesis, metrics, footer);
  return article;
}

function render(data) {
  renderActive(data.active_run);
  if (data.active_run && !pollTimer) {
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      loadBoard();
    }, 15000);
  }
  if (!data.available) {
    $("boardState").textContent = data.message;
    $("scanOverview").hidden = true;
    $("opportunityControls").hidden = true;
    $("opportunityList").replaceChildren();
    return;
  }

  const run = data.run;
  const opportunities = data.opportunities || [];
  $("boardState").textContent =
    `${run.status === "partial" ? "Partial" : "Completed"} after-close scan · ` +
    `${run.session_date} · algorithm ${run.algorithm_version}`;
  $("scanOverview").hidden = false;
  $("opportunityControls").hidden = false;
  $("scanSession").textContent = run.session_date;
  $("scanCoverage").textContent =
    `${run.processed_count}/${run.total_count}` +
    (run.failed_count ? ` · ${run.failed_count} failed` : "");
  $("credibleCount").textContent = String(data.eligible_count);
  $("scanRuntime").textContent = runtime(run.runtime_seconds);
  $("boardMethodology").textContent = data.methodology;
  $("opportunityList").replaceChildren(...opportunities.map(opportunityCard));
  $("emptyBoard").hidden = opportunities.length > 0;
}

async function loadBoard() {
  try {
    const response = await fetch(
      `/api/opportunities/latest?limit=${encodeURIComponent($("resultLimit").value)}`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Opportunity board unavailable.");
    render(data);
  } catch (error) {
    $("boardState").textContent = error.message;
  }
}

setTheme();
$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("playbook-theme", next); } catch { /* optional */ }
});
$("resultLimit").addEventListener("change", loadBoard);
loadBoard();
