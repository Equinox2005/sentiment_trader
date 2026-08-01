import copy
import gzip
import json
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import yfinance as yf

from playbook import build_playbook
from sentiment import analyze_financial_text


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")


class InvalidSymbolError(ValueError):
    pass


class InvalidDateError(ValueError):
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
    def __init__(
        self,
        store=None,
        context_cache_seconds=3600,
        full_refresh_seconds=7 * 86400,
    ):
        self.store = store
        self.context_cache_seconds = context_cache_seconds
        self.full_refresh_seconds = full_refresh_seconds
        self._context_cache = None
        self._context_cached_at = 0.0
        self._context_lock = threading.Lock()
        self._symbol_locks = tuple(threading.Lock() for _ in range(64))

    def _ticker(self, symbol):
        return yf.Ticker(symbol)

    def history(self, symbol, ticker, force_refresh=False):
        symbol_lock = self._symbol_locks[hash(symbol) % len(self._symbol_locks)]
        with symbol_lock:
            return self._history_locked(symbol, ticker, force_refresh)

    def _history_locked(self, symbol, ticker, force_refresh):
        if self.store is None:
            return self._download_full(ticker), []

        cached, metadata = self.store.load_price_snapshot(symbol)
        generation = metadata["generation"] if metadata else 0
        weekly_refresh = _full_refresh_due(
            metadata, self.full_refresh_seconds
        )
        if force_refresh or cached.empty or weekly_refresh:
            try:
                history = self._download_full(ticker)
            except MarketDataError:
                if cached.empty:
                    raise
                return cached, [
                    "A scheduled full price refresh failed; cached adjusted prices are in use."
                ]
            if not self.store.save_prices(
                symbol,
                history,
                full_refresh=True,
                expected_generation=generation,
            ):
                return self.store.load_prices(symbol), [
                    "A concurrent process completed a newer full price refresh."
                ]
            return history, []

        overlap_start = pd.Timestamp(cached.index[-1]) - timedelta(days=10)
        try:
            incremental = ticker.history(
                start=overlap_start.date().isoformat(),
                interval="1d",
                auto_adjust=True,
            )
        except Exception as exc:
            return cached, [
                "The latest price refresh failed; cached adjusted prices are in use."
            ]
        if incremental is None or incremental.empty:
            return cached, [
                "The provider returned no incremental prices; cached adjusted prices are in use."
            ]
        if "Close" not in incremental:
            raise MarketDataError("The market data provider returned malformed prices.")

        if _adjustment_drifted(cached, incremental):
            history = self._download_full(ticker)
            if not self.store.save_prices(
                symbol,
                history,
                full_refresh=True,
                expected_generation=generation,
            ):
                return self.store.load_prices(symbol), [
                    "A concurrent process completed a newer adjusted-price refresh."
                ]
            return history, [
                "A historical adjustment changed cached prices, so the full series was refreshed."
            ]

        merged = _merge_price_frames(cached, incremental)
        if not self.store.save_prices(
            symbol,
            incremental,
            full_refresh=False,
            expected_generation=generation,
        ):
            return self.store.load_prices(symbol), [
                "A concurrent process completed a newer incremental price refresh."
            ]
        return merged, []

    def _download_full(self, ticker):
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
        profile = {}
        failures = []
        warnings = []
        try:
            fast = ticker.fast_info
            for source, target in (
                ("currency", "currency"),
                ("exchange", "exchange"),
                ("quote_type", "quoteType"),
                ("timezone", "exchangeTimezoneName"),
                ("year_high", "fiftyTwoWeekHigh"),
                ("year_low", "fiftyTwoWeekLow"),
            ):
                value = _mapping_value(fast, source)
                if value is not None:
                    profile[target] = value
        except Exception as exc:
            failures.append(exc)
            warnings.append("Fast quote metadata is temporarily unavailable.")
        try:
            metadata = ticker.get_history_metadata() or {}
            profile.update(
                {
                    key: value
                    for key, value in {
                        "exchange": metadata.get("exchangeName"),
                        "currency": metadata.get("currency"),
                        "quoteType": metadata.get("instrumentType"),
                        "shortName": metadata.get("shortName"),
                    }.items()
                    if value
                }
            )
        except Exception as exc:
            failures.append(exc)
            warnings.append("Exchange metadata is temporarily unavailable.")
        try:
            details = ticker.get_info() or {}
            for key in (
                "longName",
                "shortName",
                "sector",
                "industry",
                "earningsTimestampStart",
                "earningsTimestamp",
                "earningsTimestampEnd",
            ):
                value = details.get(key)
                if value is not None:
                    profile[key] = value
        except Exception as exc:
            failures.append(exc)
            warnings.append(
                "Company sector and earnings metadata are temporarily unavailable."
            )
        if not profile and failures:
            raise MarketDataError(
                "Company details are temporarily unavailable."
            ) from failures[-1]
        return profile, warnings

    def news(self, ticker):
        try:
            if hasattr(ticker, "get_news"):
                return ticker.get_news(count=16) or []
            return ticker.news or []
        except Exception as exc:
            raise MarketDataError("Recent headlines are temporarily unavailable.") from exc

    def fetch(self, symbol, force_refresh=False):
        warnings = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            tasks = {
                "history": executor.submit(
                    self.history,
                    symbol,
                    self._ticker(symbol),
                    force_refresh,
                ),
                "profile": executor.submit(
                    self.profile, self._ticker(symbol)
                ),
                "news": executor.submit(self.news, self._ticker(symbol)),
                "context": executor.submit(self.market_context),
            }
            try:
                history, history_warnings = tasks["history"].result()
                warnings.extend(history_warnings)
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(
                    "The market data provider could not be reached."
                ) from exc

            optional = {}
            defaults = {"profile": {}, "news": [], "context": None}
            for name in ("profile", "news", "context"):
                try:
                    value = tasks[name].result()
                    if (
                        name == "profile"
                        and isinstance(value, tuple)
                        and len(value) == 2
                    ):
                        optional[name], profile_warnings = value
                        warnings.extend(profile_warnings)
                    else:
                        optional[name] = value
                except MarketDataError as exc:
                    optional[name] = defaults[name]
                    warnings.append(str(exc))
                except Exception as exc:
                    optional[name] = defaults[name]
                    warnings.append(
                        f"{name.title()} data is temporarily unavailable."
                    )
        return (
            history,
            optional["profile"],
            optional["news"],
            warnings,
            optional["context"],
        )


class MarketIntelligenceService:
    def __init__(
        self,
        provider,
        cache_seconds=300,
        max_cache_entries=128,
        source_cache_seconds=90,
    ):
        self.provider = provider
        self.cache_seconds = cache_seconds
        self.max_cache_entries = max_cache_entries
        self.source_cache_seconds = source_cache_seconds
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._request_versions = {}
        self._request_counter = 0
        self._source_cache = {}
        self._source_cache_lock = threading.Lock()

    def analyze(
        self,
        raw_symbol,
        force_refresh=False,
        include_validation=True,
        snapshot_id=None,
    ):
        symbol = normalize_symbol(raw_symbol)
        if include_validation:
            cache_mode = "audit" if snapshot_id else "legacy"
        else:
            cache_mode = "quick"
        cache_key = (
            symbol,
            cache_mode,
            snapshot_id or "latest",
        )
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached is not None:
                if snapshot_id:
                    source_is_valid = (
                        cached.get("snapshot_id") == snapshot_id
                        and self._get_source(snapshot_id, symbol) is not None
                    )
                elif include_validation:
                    source_is_valid = True
                else:
                    source_is_valid = (
                        self._get_source(cached["snapshot_id"], symbol)
                        is not None
                    )
                if source_is_valid:
                    return cached

        request_version = self._begin_request(cache_key)
        source = (
            self._get_source(snapshot_id, symbol)
            if include_validation and snapshot_id and not force_refresh
            else None
        )
        if include_validation and snapshot_id and source is None:
            raise MarketDataError(
                "That staged market snapshot expired. Run the symbol again."
            )
        if source is None:
            try:
                source = self.provider.fetch(
                    symbol, force_refresh=force_refresh
                )
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(
                    "Market research is temporarily unavailable. Please try again."
                ) from exc
            snapshot_id = self._store_source(symbol, source)

        history, profile, raw_news, warnings, context = copy.deepcopy(source)

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

        result = _build_analysis(
            symbol=symbol,
            history=history,
            profile=profile,
            news=normalized_news,
            warnings=warnings,
            context=context,
            include_validation=include_validation,
            snapshot_id=snapshot_id,
        )
        if include_validation:
            self._record_live_forecast(symbol, history, result)
        self._store_cached(cache_key, result, request_version)
        return copy.deepcopy(result)

    def _get_source(self, snapshot_id, symbol):
        if not snapshot_id:
            return None
        now = time.monotonic()
        with self._source_cache_lock:
            expired = [
                key
                for key, (created_at, _symbol, _source) in self._source_cache.items()
                if now - created_at >= self.source_cache_seconds
            ]
            for key in expired:
                del self._source_cache[key]
            entry = self._source_cache.get(snapshot_id)
            if entry and entry[1] == symbol:
                return copy.deepcopy(entry[2])
        store = getattr(self.provider, "store", None)
        if store is None:
            return None
        record = store.load_source_snapshot(
            snapshot_id,
            symbol,
            ttl_seconds=self.source_cache_seconds,
        )
        if record is None:
            return None
        payload, created_at = record
        source = _deserialize_source(payload)
        created = _parse_datetime(created_at)
        age_seconds = (
            max(
                0.0,
                (datetime.now(timezone.utc) - created).total_seconds(),
            )
            if created is not None
            else 0.0
        )
        self._cache_source(
            snapshot_id,
            symbol,
            source,
            now - age_seconds,
        )
        return source

    def _store_source(self, symbol, source):
        snapshot_id = uuid.uuid4().hex
        now = time.monotonic()
        self._cache_source(snapshot_id, symbol, source, now)
        store = getattr(self.provider, "store", None)
        if store is not None:
            store.save_source_snapshot(
                snapshot_id,
                symbol,
                _serialize_source(source),
                ttl_seconds=self.source_cache_seconds,
                max_entries=self.max_cache_entries,
            )
        return snapshot_id

    def _cache_source(self, snapshot_id, symbol, source, now):
        with self._source_cache_lock:
            expired = [
                key
                for key, (created_at, _symbol, _source) in self._source_cache.items()
                if now - created_at >= self.source_cache_seconds
            ]
            for key in expired:
                del self._source_cache[key]
            if (
                snapshot_id not in self._source_cache
                and len(self._source_cache) >= self.max_cache_entries
            ):
                oldest = min(
                    self._source_cache,
                    key=lambda key: self._source_cache[key][0],
                )
                del self._source_cache[oldest]
            self._source_cache[snapshot_id] = (
                now,
                symbol,
                copy.deepcopy(source),
            )

    def analyze_quick(self, raw_symbol, force_refresh=False):
        return self.analyze(
            raw_symbol,
            force_refresh=force_refresh,
            include_validation=False,
        )

    def analyze_audit(
        self,
        raw_symbol,
        force_refresh=False,
        snapshot_id=None,
    ):
        if not snapshot_id:
            raise MarketDataError(
                "The audit requires the snapshot token returned by the quick forecast."
            )
        if force_refresh:
            raise MarketDataError(
                "Refresh the quick forecast first, then audit its new snapshot."
            )
        return self.analyze(
            raw_symbol,
            force_refresh=False,
            include_validation=True,
            snapshot_id=snapshot_id,
        )

    def analyze_as_of(self, raw_symbol, as_of_date, force_refresh=False):
        symbol = normalize_symbol(raw_symbol)
        requested_date = _parse_as_of_date(as_of_date)
        try:
            source = self.provider.fetch(
                symbol,
                force_refresh=force_refresh,
            )
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(
                "Market research is temporarily unavailable. Please try again."
            ) from exc

        history, profile, _news, warnings, context = copy.deepcopy(source)
        history = history.sort_index()
        valid_close = pd.to_numeric(
            history["Close"],
            errors="coerce",
        ).notna()
        history = history.loc[valid_close]
        session_dates = [
            pd.Timestamp(value).date()
            for value in history.index
        ]
        eligible = [
            position
            for position, session_date in enumerate(session_dates)
            if session_date <= requested_date
        ]
        if not eligible:
            raise InvalidDateError(
                "Choose a date within this symbol's available price history."
            )
        position = eligible[-1]
        if position == len(history) - 1 and requested_date > session_dates[-1]:
            raise InvalidDateError(
                "Time Machine dates cannot be later than the latest market session."
            )

        historical_history = history.iloc[: position + 1].copy()
        historical_context = _frame_through_date(context, session_dates[position])
        historical_profile = {
            key: value
            for key, value in profile.items()
            if not key.startswith("earningsTimestamp")
        }
        historical_warnings = list(warnings)
        historical_warnings.append(
            "Historical headlines are unavailable, so this replay uses price and "
            "market context only."
        )
        result = _build_analysis(
            symbol=symbol,
            history=historical_history,
            profile=historical_profile,
            news=[],
            warnings=historical_warnings,
            context=historical_context,
            include_validation=True,
            snapshot_id=f"as-of:{session_dates[position].isoformat()}",
        )
        result["stage"] = "time_machine"
        result["as_of"] = session_dates[position].isoformat()
        result["methodology"] = (
            "Time Machine ran the same analog engine on a history cut off after "
            f"{session_dates[position].isoformat()}. No later price, context, "
            "outcome, or current headline was available to the forecast."
        )

        outcome = {
            "available": False,
            "reason": "The selected forecast horizon has not completed yet.",
        }
        play = result.get("playbook", {})
        if play.get("available"):
            horizon = int(play["forecast"]["horizon_days"])
            outcome_position = position + horizon
            if outcome_position < len(history):
                entry = float(history["Close"].iloc[position])
                exit_price = float(history["Close"].iloc[outcome_position])
                realized_return = (exit_price / entry - 1) * 100
                direction = play["verdict"]["direction"]
                direction_correct = (
                    realized_return > 0
                    if direction == "bullish"
                    else realized_return < 0
                    if direction == "bearish"
                    else None
                )
                outcome = {
                    "available": True,
                    "date": session_dates[outcome_position].isoformat(),
                    "realized_return": round(realized_return, 2),
                    "actual_up": realized_return > 0,
                    "probability_correct": (
                        play["forecast"]["probability_up"] >= 50
                    ) == (realized_return > 0),
                    "direction_correct": direction_correct,
                }
        result["time_machine"] = {
            "requested_date": requested_date.isoformat(),
            "session_date": session_dates[position].isoformat(),
            "future_data_used": False,
            "outcome": outcome,
        }
        return result

    def track_record(self, raw_symbol, force_refresh=False):
        symbol = normalize_symbol(raw_symbol)
        store = getattr(self.provider, "store", None)
        if store is None:
            return {
                "symbol": symbol,
                "available": False,
                "reason": "Persistent forecast storage is not configured.",
                "records": [],
            }
        warnings = []
        if force_refresh:
            try:
                history, _profile, _news, warnings, _context = self.provider.fetch(
                    symbol,
                    force_refresh=True,
                )
            except MarketDataError:
                raise
            except Exception as exc:
                raise MarketDataError(
                    "Market research is temporarily unavailable. Please try again."
                ) from exc
            store.grade_pending_forecasts(symbol, history)
        records = store.list_forecasts(symbol)
        return _build_track_record(symbol, records, warnings)

    def _record_live_forecast(self, symbol, history, result):
        store = getattr(self.provider, "store", None)
        play = result.get("playbook", {})
        if store is None or not play.get("available"):
            return
        store.grade_pending_forecasts(symbol, history)
        forecast = play["forecast"]
        as_of_date = _date_string(history.index[-1])
        horizon_days = int(forecast["horizon_days"])
        exchange_timezone = (
            result.get("exchange_timezone")
            or _index_timezone(history.index)
        )
        if not exchange_timezone:
            result["warnings"].append(
                "The live forecast ledger was deferred because the market "
                "timezone could not be verified."
            )
            return
        if not _session_is_complete(
            history.index[-1],
            timezone_name=exchange_timezone,
        ):
            store.delete_pending_forecast(
                symbol,
                as_of_date,
                horizon_days,
            )
            return
        start = pd.Timestamp(as_of_date)
        if forecast["sampling"] == "calendar_daily":
            horizon_date = start + pd.Timedelta(days=horizon_days)
        else:
            horizon_date = start + pd.offsets.BDay(horizon_days)
        store.save_forecast(
            symbol=symbol,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            horizon_date=horizon_date.date().isoformat(),
            payload={
                "entry_price": float(history["Close"].iloc[-1]),
                "probability_up": forecast["probability_up"],
                "analog_probability_up": forecast["analog_probability_up"],
                "baseline_up_rate": forecast["baseline_up_rate"],
                "edge_points": forecast["edge_points"],
                "direction": play["verdict"]["direction"],
                "range": forecast["range_21d"],
                "evidence_score": forecast["evidence_score"],
                "validation_grade": play["validation"].get("grade"),
                "horizon_label": forecast["horizon_label"],
                "snapshot_id": result["snapshot_id"],
                "exchange_timezone": exchange_timezone,
            },
        )

    def _begin_request(self, cache_key):
        with self._cache_lock:
            self._request_counter += 1
            version = self._request_counter
            if (
                cache_key not in self._request_versions
                and len(self._request_versions) >= self.max_cache_entries
            ):
                oldest = min(
                    self._request_versions,
                    key=self._request_versions.get,
                )
                del self._request_versions[oldest]
                self._cache.pop(oldest, None)
            self._request_versions[cache_key] = version
            return version

    def _get_cached(self, cache_key):
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if not entry:
                return None
            created_at, value = entry
            if time.monotonic() - created_at >= self.cache_seconds:
                del self._cache[cache_key]
                return None
            return copy.deepcopy(value)

    def _remove_cached(self, cache_key):
        with self._cache_lock:
            self._cache.pop(cache_key, None)

    def _store_cached(self, cache_key, value, request_version):
        with self._cache_lock:
            if self._request_versions.get(cache_key) != request_version:
                return False
            now = time.monotonic()
            expired = [
                key
                for key, (created_at, _value) in self._cache.items()
                if now - created_at >= self.cache_seconds
            ]
            for key in expired:
                del self._cache[key]
            if (
                cache_key not in self._cache
                and len(self._cache) >= self.max_cache_entries
            ):
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                del self._cache[oldest]
            self._cache[cache_key] = (now, copy.deepcopy(value))
            return True


def _build_analysis(
    symbol,
    history,
    profile,
    news,
    warnings,
    context=None,
    include_validation=True,
    snapshot_id=None,
):
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
        include_validation=include_validation,
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
        "snapshot_id": snapshot_id or _snapshot_id(close),
        "stage": "audit" if include_validation else "quick",
        "name": display_name,
        "exchange": profile.get("exchange") or profile.get("fullExchangeName") or "",
        "exchange_timezone": profile.get("exchangeTimezoneName") or "",
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
            (
                "Playbook compares today's OHLCV fingerprint and one-month chart shape "
                "with independent episodes in up to twenty years of history. This "
                "preliminary stage uses balanced weights while the adaptive walk-forward "
                "audit loads. Recent news remains bounded to five probability points."
            )
            if not include_validation
            else (
                "Playbook compares today's OHLCV fingerprint and one-month chart shape "
                "with independent episodes in up to twenty years of history. It selects "
                "an explainable weight profile on older walk-forward checkpoints, "
                "evaluates that profile on newer untouched checkpoints, and weights the "
                "real paths that followed. Recent news can adjust the displayed "
                "probability by at most five points; it cannot manufacture or reverse "
                "an analog edge."
            )
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


def _parse_as_of_date(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise InvalidDateError("Enter a Time Machine date as YYYY-MM-DD.")
    try:
        parsed = pd.Timestamp(text)
    except (TypeError, ValueError) as exc:
        raise InvalidDateError("Enter a valid Time Machine date.") from exc
    if pd.isna(parsed):
        raise InvalidDateError("Enter a valid Time Machine date.")
    return parsed.date()


def _frame_through_date(frame, target_date):
    if frame is None:
        return None
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_index()
    positions = [
        position
        for position, value in enumerate(ordered.index)
        if pd.Timestamp(value).date() <= target_date
    ]
    if not positions:
        return ordered.iloc[:0].copy()
    return ordered.iloc[: positions[-1] + 1].copy()


def _index_timezone(index):
    timezone_value = getattr(index, "tz", None)
    return str(timezone_value) if timezone_value is not None else ""


def _session_is_complete(value, timezone_name=None, now=None):
    session = pd.Timestamp(value)
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if timezone_name:
        try:
            market_timezone = ZoneInfo(timezone_name)
        except (TypeError, ZoneInfoNotFoundError):
            return False
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        current = current.tz_convert(market_timezone)
    elif session.tzinfo is not None:
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        current = current.tz_convert(session.tz)
    else:
        return False
    return session.date() < current.date()


def _build_track_record(symbol, records, warnings):
    public_records = []
    for record in records:
        graded = record["status"] == "graded"
        realized_return = (
            float(record["realized_return"])
            if graded and record["realized_return"] is not None
            else None
        )
        actual_up = realized_return > 0 if realized_return is not None else None
        probability_correct = (
            (float(record["probability_up"]) >= 50) == actual_up
            if actual_up is not None
            else None
        )
        direction = record["direction"]
        direction_correct = (
            realized_return > 0
            if direction == "bullish" and realized_return is not None
            else realized_return < 0
            if direction == "bearish" and realized_return is not None
            else None
        )
        public_records.append(
            {
                "as_of_date": record["as_of_date"],
                "horizon_date": record["horizon_date"],
                "outcome_date": record["outcome_date"],
                "status": record["status"],
                "entry_price": round(float(record["entry_price"]), 4),
                "probability_up": float(record["probability_up"]),
                "baseline_up_rate": float(record["baseline_up_rate"]),
                "edge_points": float(record["edge_points"]),
                "direction": direction,
                "range": record["range"],
                "evidence_score": int(record["evidence_score"]),
                "validation_grade": record["validation_grade"],
                "horizon_label": record["horizon_label"],
                "realized_return": (
                    round(realized_return, 2)
                    if realized_return is not None
                    else None
                ),
                "probability_correct": probability_correct,
                "direction_correct": direction_correct,
            }
        )

    graded = [
        item for item in public_records
        if item["status"] == "graded"
    ]
    directional = [
        item for item in graded
        if item["direction"] in {"bullish", "bearish"}
    ]
    brier = (
        sum(
            (
                item["probability_up"] / 100
                - float(item["realized_return"] > 0)
            ) ** 2
            for item in graded
        ) / len(graded)
        if graded
        else None
    )
    return {
        "symbol": symbol,
        "available": bool(records),
        "summary": {
            "total": len(public_records),
            "graded": len(graded),
            "pending": len(public_records) - len(graded),
            "probability_accuracy": (
                round(
                    sum(item["probability_correct"] for item in graded)
                    / len(graded)
                    * 100
                )
                if graded
                else None
            ),
            "directional_calls": len(directional),
            "directional_accuracy": (
                round(
                    sum(item["direction_correct"] for item in directional)
                    / len(directional)
                    * 100
                )
                if directional
                else None
            ),
            "brier": round(brier, 3) if brier is not None else None,
        },
        "records": public_records[:100],
        "records_shown": min(100, len(public_records)),
        "records_truncated": len(public_records) > 100,
        "warnings": warnings,
        "note": (
            "Live forecasts are stored once per completed market session and "
            "graded after the exact configured number of future sessions. "
            "Pending forecasts are never treated as wins or losses."
        ),
    }


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


def _mapping_value(mapping, key):
    try:
        if hasattr(mapping, "get"):
            return mapping.get(key)
        return getattr(mapping, key, None)
    except Exception:
        return None


def _full_refresh_due(metadata, full_refresh_seconds):
    if not metadata:
        return True
    refreshed = _parse_datetime(metadata.get("last_full_refresh_at"))
    if refreshed is None:
        return True
    return (
        datetime.now(timezone.utc) - refreshed
    ).total_seconds() >= full_refresh_seconds


def _price_rows_by_date(frame):
    rows = {}
    if frame is None or frame.empty:
        return rows
    for index, row in frame.iterrows():
        close = pd.to_numeric(row.get("Close"), errors="coerce")
        if pd.isna(close):
            continue
        rows[pd.Timestamp(index).date().isoformat()] = float(close)
    return rows


def _adjustment_drifted(cached, incremental, threshold=0.005):
    cached_rows = _price_rows_by_date(cached)
    incremental_rows = _price_rows_by_date(incremental)
    overlap = sorted(set(cached_rows).intersection(incremental_rows))
    if not overlap:
        return False
    for date in overlap:
        cached_close = cached_rows[date]
        incoming_close = incremental_rows[date]
        if not cached_close:
            if incoming_close != 0:
                return True
            continue
        if abs(incoming_close / cached_close - 1) > threshold:
            return True
    return False


def _merge_price_frames(cached, incremental):
    frames = []
    for frame in (cached, incremental):
        normalized = frame.copy()
        normalized.index = pd.DatetimeIndex(
            [pd.Timestamp(value).date() for value in normalized.index]
        )
        frames.append(normalized)
    merged = pd.concat(frames).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


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


def _serialize_source(source):
    history, profile, news, warnings, context = source
    payload = {
        "history": _frame_payload(history),
        "profile": _sanitize_json(profile),
        "news": _sanitize_json(news),
        "warnings": _sanitize_json(warnings),
        "context": _frame_payload(context) if context is not None else None,
    }
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        default=_json_value,
        allow_nan=False,
    ).encode("utf-8")
    return gzip.compress(encoded, compresslevel=5)


def _deserialize_source(payload):
    value = json.loads(gzip.decompress(payload).decode("utf-8"))
    return (
        _frame_from_payload(value["history"]),
        value["profile"],
        value["news"],
        value["warnings"],
        (
            _frame_from_payload(value["context"])
            if value["context"] is not None
            else None
        ),
    )


def _frame_payload(frame):
    columns = [str(column) for column in frame.columns]
    data = []
    for row in frame[columns].itertuples(index=False, name=None):
        data.append(
            [
                None if pd.isna(item) else _json_value(item)
                for item in row
            ]
        )
    return {
        "index": [_date_string(value) for value in frame.index],
        "timezone": _index_timezone(frame.index),
        "columns": columns,
        "data": data,
    }


def _frame_from_payload(payload):
    frame = pd.DataFrame(payload["data"], columns=payload["columns"])
    index = pd.to_datetime(payload["index"])
    timezone_name = payload.get("timezone")
    if timezone_name:
        try:
            index = index.tz_localize(ZoneInfo(timezone_name))
        except (TypeError, ZoneInfoNotFoundError):
            index = index.tz_localize("UTC")
    else:
        index = index.tz_localize("UTC")
    frame.index = index
    return frame


def _json_value(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sanitize_json(value):
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    return _json_value(value)


def _snapshot_id(close):
    return (
        f"{_date_string(close.index[-1])}:"
        f"{len(close)}:{float(close.iloc[-1]):.8f}"
    )


def _clamp(value, minimum=-1.0, maximum=1.0):
    return max(minimum, min(maximum, value))
