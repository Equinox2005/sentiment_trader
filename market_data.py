import copy
import math
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import yfinance as yf

from playbook import build_playbook
from sentiment import analyze_financial_text


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")


class InvalidSymbolError(ValueError):
    pass


class MarketDataError(RuntimeError):
    pass


def normalize_symbol(value):
    symbol = value.strip().upper().lstrip("$")
    if not symbol or not SYMBOL_PATTERN.fullmatch(symbol):
        raise InvalidSymbolError(
            "Enter a valid market symbol such as AAPL, BRK.B, BTC-USD, or ^GSPC."
        )
    return symbol


class YahooFinanceProvider:
    def __init__(self, context_cache_seconds=3600):
        self.context_cache_seconds = context_cache_seconds
        self._context_cache = None
        self._context_cached_at = 0.0
        self._context_lock = threading.Lock()

    def _ticker(self, symbol):
        return yf.Ticker(symbol)

    def history(self, ticker):
        try:
            history = ticker.history(period="max", interval="1d", auto_adjust=True)
        except Exception as exc:
            raise MarketDataError("The market data provider could not be reached.") from exc
        if history is None or history.empty or "Close" not in history:
            raise MarketDataError(
                "No recent price history was found for that symbol."
            )
        return history

    def market_context(self):
        with self._context_lock:
            if (
                self._context_cache is not None
                and time.monotonic() - self._context_cached_at
                < self.context_cache_seconds
            ):
                return self._context_cache.copy()
            try:
                raw = yf.download(
                    ["SPY", "^VIX"],
                    period="max",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                close = raw["Close"]
                context = pd.DataFrame(
                    {
                        "Market": pd.to_numeric(close["SPY"], errors="coerce"),
                        "VIX": pd.to_numeric(close["^VIX"], errors="coerce"),
                    }
                ).dropna(how="all")
            except Exception as exc:
                raise MarketDataError(
                    "Broad-market context is temporarily unavailable."
                ) from exc
            if context.empty:
                raise MarketDataError(
                    "Broad-market context is temporarily unavailable."
                )
            self._context_cache = context
            self._context_cached_at = time.monotonic()
            return context.copy()

    def profile(self, ticker):
        try:
            return ticker.get_info() or {}
        except Exception as exc:
            raise MarketDataError("Company details are temporarily unavailable.") from exc

    def news(self, ticker):
        try:
            if hasattr(ticker, "get_news"):
                return ticker.get_news(count=16) or []
            return ticker.news or []
        except Exception as exc:
            raise MarketDataError("Recent headlines are temporarily unavailable.") from exc

    def fetch(self, symbol):
        ticker = self._ticker(symbol)
        history = self.history(ticker)
        warnings = []
        try:
            profile = self.profile(ticker)
        except MarketDataError as exc:
            profile = {}
            warnings.append(str(exc))
        try:
            news = self.news(ticker)
        except MarketDataError as exc:
            news = []
            warnings.append(str(exc))
        return history, profile, news, warnings


class MarketIntelligenceService:
    def __init__(self, provider, cache_seconds=300, max_cache_entries=128):
        self.provider = provider
        self.cache_seconds = cache_seconds
        self.max_cache_entries = max_cache_entries
        self._cache = {}
        self._cache_lock = threading.Lock()

    def analyze(self, raw_symbol, force_refresh=False):
        symbol = normalize_symbol(raw_symbol)
        if not force_refresh:
            cached = self._get_cached(symbol)
            if cached is not None:
                return cached

        try:
            history, profile, raw_news, warnings = self.provider.fetch(symbol)
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(
                "Market research is temporarily unavailable. Please try again."
            ) from exc

        normalized_news = []
        stale_headlines = 0
        for item in raw_news:
            article = _normalize_news_item(item)
            if article:
                if not _is_recent(article["published_at"], max_age_days=14):
                    stale_headlines += 1
                    continue
                sentiment = analyze_financial_text(article["title"])
                article.update(
                    {
                        "sentiment": sentiment["score"],
                        "sentiment_label": sentiment["label"],
                        "signals": sentiment["terms"][:4],
                    }
                )
                normalized_news.append(article)
            if len(normalized_news) == 12:
                break

        if stale_headlines:
            warnings.append(
                f"{stale_headlines} older headline{'s were' if stale_headlines != 1 else ' was'} "
                "excluded from the narrative score."
            )
        if not normalized_news:
            warnings.append(
                "No recent headlines were available, so the brief relies on price action."
            )

        context = None
        if hasattr(self.provider, "market_context"):
            try:
                context = self.provider.market_context()
            except MarketDataError as exc:
                warnings.append(str(exc))

        result = _build_analysis(
            symbol=symbol,
            history=history,
            profile=profile,
            news=normalized_news,
            warnings=warnings,
            context=context,
        )
        self._store_cached(symbol, result)
        return copy.deepcopy(result)

    def _get_cached(self, symbol):
        with self._cache_lock:
            entry = self._cache.get(symbol)
            if not entry:
                return None
            created_at, value = entry
            if time.monotonic() - created_at >= self.cache_seconds:
                del self._cache[symbol]
                return None
            return copy.deepcopy(value)

    def _store_cached(self, symbol, value):
        with self._cache_lock:
            now = time.monotonic()
            expired = [
                key
                for key, (created_at, _value) in self._cache.items()
                if now - created_at >= self.cache_seconds
            ]
            for key in expired:
                del self._cache[key]
            if (
                symbol not in self._cache
                and len(self._cache) >= self.max_cache_entries
            ):
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                del self._cache[oldest]
            self._cache[symbol] = (now, copy.deepcopy(value))


def _build_analysis(symbol, history, profile, news, warnings, context=None):
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2:
        raise MarketDataError("Not enough price history exists to analyze this symbol.")

    current = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    daily_change = _percent_change(current, previous)
    month_change = _period_return(close, _month_period(close))
    year_high = float(close.tail(252).max())
    year_low = float(close.tail(252).min())

    narrative_score = _weighted_news_score(news)
    play = build_playbook(
        history,
        context=context,
        news_score=narrative_score,
        news_count=len(news),
        earnings_at=_profile_earnings_at(profile),
    )
    story = _build_story_check(play, narrative_score, len(news))

    chart_close = close.tail(190)
    chart = [
        {
            "date": _date_string(index),
            "close": round(float(value), 4),
        }
        for index, value in chart_close.items()
    ]

    display_name = (
        profile.get("longName")
        or profile.get("shortName")
        or profile.get("name")
        or symbol
    )

    return {
        "symbol": symbol,
        "name": display_name,
        "exchange": profile.get("exchange") or profile.get("fullExchangeName") or "",
        "sector": profile.get("sector") or profile.get("quoteType") or "Market asset",
        "currency": profile.get("currency") or "USD",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "quote": {
            "price": round(current, 4),
            "daily_change": round(daily_change, 2),
            "month_change": round(month_change, 2),
            "year_high": round(year_high, 4),
            "year_low": round(year_low, 4),
        },
        "playbook": play,
        "story": story,
        "news": news,
        "news_summary": {
            "count": len(news),
            "positive": sum(item["sentiment"] >= 0.18 for item in news),
            "negative": sum(item["sentiment"] <= -0.18 for item in news),
            "neutral": sum(abs(item["sentiment"]) < 0.18 for item in news),
        },
        "history": chart,
        "history_years": _history_years(close),
        "warnings": warnings,
        "methodology": (
            "Playbook compares today's OHLCV fingerprint and one-month chart shape with "
            "independent episodes in up to twenty years of history. It selects an "
            "explainable weight profile on older walk-forward checkpoints, evaluates "
            "that profile on newer untouched checkpoints, and weights the real paths "
            "that followed. Recent news can adjust the displayed probability by at "
            "most five points; it cannot manufacture or reverse an analog edge."
        ),
    }


def _build_story_check(play, narrative_score, news_count):
    news_lean = (
        "positive" if narrative_score >= 0.15
        else "negative" if narrative_score <= -0.15
        else "neutral"
    )
    if news_count == 0:
        return {
            "state": "no_news",
            "news_lean": news_lean,
            "summary": (
                "No recent headlines were found, so the playbook stands on "
                "price history alone."
            ),
        }
    if not play.get("available"):
        return {
            "state": "news_only",
            "news_lean": news_lean,
            "summary": (
                f"Today's headlines lean {news_lean}, but there is not enough "
                "price history to test them against past outcomes."
            ),
        }

    direction = play["forecast"]["analog_direction"]
    adjustment = play["forecast"]["news_adjustment_points"]
    agreement = {
        ("bullish", "positive"): (
            "confirms",
            f"Positive headlines add {adjustment:+.1f} points to the displayed "
            "up-probability. The historical edge remains the primary evidence.",
        ),
        ("bearish", "negative"): (
            "confirms",
            f"Negative headlines add {adjustment:+.1f} points to the displayed "
            "up-probability. The historical edge remains the primary evidence.",
        ),
        ("bullish", "negative"): (
            "conflicts",
            f"Negative headlines subtract {abs(adjustment):.1f} points from the "
            "historical up-probability. They can cancel, but never reverse, the analog edge.",
        ),
        ("bearish", "positive"): (
            "conflicts",
            f"Positive headlines add {adjustment:+.1f} points against the bearish "
            "historical edge. They can cancel, but never reverse, that edge.",
        ),
    }
    state, summary = agreement.get(
        (direction, news_lean),
        (
            "neutral",
            f"Today's headlines lean {news_lean} and add no strong push in either "
            "direction. The playbook verdict stands on its own.",
        ),
    )
    return {"state": state, "news_lean": news_lean, "summary": summary}


def _profile_earnings_at(profile):
    for key in (
        "earningsTimestampStart",
        "earningsTimestamp",
        "earningsTimestampEnd",
    ):
        parsed = _parse_datetime(profile.get(key))
        if parsed:
            return parsed
    return None


def _normalize_news_item(item):
    if not isinstance(item, dict):
        return None
    content = item.get("content") if isinstance(item.get("content"), dict) else item
    title = content.get("title") or item.get("title")
    if not title or not isinstance(title, str):
        return None

    provider = content.get("provider") or item.get("publisher") or {}
    if isinstance(provider, dict):
        publisher = provider.get("displayName") or provider.get("name") or "Market news"
    else:
        publisher = str(provider)

    url = _nested_url(content.get("canonicalUrl"))
    if not url:
        url = _nested_url(content.get("clickThroughUrl"))
    if not url:
        url = item.get("link") or item.get("url") or ""
    if url and urlparse(url).scheme not in {"http", "https"}:
        url = ""

    published_value = (
        content.get("pubDate")
        or content.get("displayTime")
        or item.get("providerPublishTime")
        or item.get("published")
    )
    published_at = _parse_datetime(published_value)

    return {
        "title": " ".join(title.split()),
        "publisher": publisher,
        "url": url,
        "published_at": published_at.isoformat() if published_at else None,
    }


def _nested_url(value):
    if isinstance(value, dict):
        return value.get("url") or ""
    return value if isinstance(value, str) else ""


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _weighted_news_score(news):
    if not news:
        return 0.0
    now = datetime.now(timezone.utc)
    total = 0.0
    weights = 0.0
    for article in news:
        published = _parse_datetime(article.get("published_at"))
        hours_old = (
            max((now - published).total_seconds() / 3600, 0) if published else 72
        )
        weight = math.exp(-hours_old / 120)
        total += article["sentiment"] * weight
        weights += weight
    return _clamp(total / max(weights, 1.0)) if weights else 0.0


def _is_recent(value, max_age_days):
    published = _parse_datetime(value)
    if not published:
        return True
    age_seconds = (datetime.now(timezone.utc) - published).total_seconds()
    return age_seconds <= max_age_days * 86400


def _period_return(close, periods):
    start = float(close.iloc[-min(periods + 1, len(close))])
    return _percent_change(float(close.iloc[-1]), start)


def _history_years(close):
    if len(close) < 2:
        return 0.0
    try:
        elapsed = (pd.Timestamp(close.index[-1]) - pd.Timestamp(close.index[0])).days
        return round(min(20.0, max(0.0, elapsed / 365.25)), 1)
    except (TypeError, ValueError):
        return round(min(20.0, len(close) / 252), 1)


def _month_period(close):
    if isinstance(close.index, pd.DatetimeIndex) and len(close) >= 60:
        if float((close.index.dayofweek >= 5).mean()) >= 0.15:
            return 30
    return 21


def _percent_change(current, previous):
    return ((current / previous) - 1) * 100 if previous else 0.0


def _date_string(value):
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


def _clamp(value, minimum=-1.0, maximum=1.0):
    return max(minimum, min(maximum, value))
