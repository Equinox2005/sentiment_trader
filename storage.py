import json
import os
import sqlite3
import threading
import time
from bisect import bisect_left
from contextlib import closing
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class PlaybookStore:
    """Concurrency-safe persistent storage for prices and forecast records."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._lock, closing(self._connect()) as connection:
            for attempt in range(20):
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 19:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_bars (
                    symbol TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL NOT NULL,
                    volume REAL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, session_date)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_cache_meta (
                    symbol TEXT PRIMARY KEY,
                    last_updated_at TEXT NOT NULL,
                    last_full_refresh_at TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    horizon_date TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    realized_return REAL,
                    realized_price REAL,
                    outcome_date TEXT,
                    created_at TEXT NOT NULL,
                    graded_at TEXT,
                    UNIQUE(symbol, as_of_date, horizon_days)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS source_snapshots_symbol_created
                ON source_snapshots(symbol, created_at)
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(price_cache_meta)"
                ).fetchall()
            }
            if "generation" not in columns:
                connection.execute(
                    """
                    ALTER TABLE price_cache_meta
                    ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
                    """
                )
            forecast_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(forecasts)"
                ).fetchall()
            }
            if "realized_price" not in forecast_columns:
                connection.execute(
                    "ALTER TABLE forecasts ADD COLUMN realized_price REAL"
                )
            if "outcome_date" not in forecast_columns:
                connection.execute(
                    "ALTER TABLE forecasts ADD COLUMN outcome_date TEXT"
                )
            connection.commit()

    def load_prices(self, symbol):
        frame, _metadata = self.load_price_snapshot(symbol)
        return frame

    def load_price_snapshot(self, symbol):
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                """
                SELECT session_date, open, high, low, close, volume
                FROM price_bars
                WHERE symbol = ?
                ORDER BY session_date
                """,
                (symbol,),
            ).fetchall()
            metadata = connection.execute(
                """
                SELECT last_updated_at, last_full_refresh_at, generation
                FROM price_cache_meta
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            connection.commit()
        frame = _price_rows_to_frame(rows)
        return frame, (dict(metadata) if metadata else None)

    def save_source_snapshot(
        self,
        snapshot_id,
        symbol,
        payload,
        now=None,
        ttl_seconds=180,
        max_entries=128,
    ):
        timestamp = _iso_utc(now)
        cutoff = _iso_utc(
            (now or datetime.now(timezone.utc))
            - pd.Timedelta(seconds=ttl_seconds)
        )
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM source_snapshots WHERE created_at < ?",
                (cutoff,),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO source_snapshots (
                    snapshot_id, symbol, created_at, payload
                ) VALUES (?, ?, ?, ?)
                """,
                (snapshot_id, symbol, timestamp, payload),
            )
            overflow = connection.execute(
                """
                SELECT snapshot_id
                FROM source_snapshots
                ORDER BY created_at DESC
                LIMIT -1 OFFSET ?
                """,
                (max_entries,),
            ).fetchall()
            if overflow:
                connection.executemany(
                    "DELETE FROM source_snapshots WHERE snapshot_id = ?",
                    [(row["snapshot_id"],) for row in overflow],
                )
            connection.commit()

    def load_source_snapshot(self, snapshot_id, symbol, ttl_seconds=180):
        cutoff = _iso_utc(
            datetime.now(timezone.utc)
            - pd.Timedelta(seconds=ttl_seconds)
        )
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT payload, created_at
                FROM source_snapshots
                WHERE snapshot_id = ?
                  AND symbol = ?
                  AND created_at >= ?
                """,
                (snapshot_id, symbol, cutoff),
            ).fetchone()
        return (
            (bytes(row["payload"]), row["created_at"])
            if row
            else None
        )

    def price_meta(self, symbol):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT last_updated_at, last_full_refresh_at, generation
                FROM price_cache_meta
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
        return dict(row) if row else None

    def save_prices(
        self,
        symbol,
        frame,
        full_refresh=False,
        now=None,
        expected_generation=None,
    ):
        if frame is None or frame.empty:
            return False
        timestamp = _iso_utc(now)
        records = []
        for index, row in frame.iterrows():
            close = _optional_float(row.get("Close"))
            if close is None:
                continue
            records.append(
                (
                    symbol,
                    _session_date(index),
                    _optional_float(row.get("Open")),
                    _optional_float(row.get("High")),
                    _optional_float(row.get("Low")),
                    close,
                    _optional_float(row.get("Volume")),
                    timestamp,
                )
            )
        if not records:
            return False

        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """
                SELECT last_full_refresh_at, generation
                FROM price_cache_meta
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            current_generation = previous["generation"] if previous else 0
            if (
                expected_generation is not None
                and current_generation != expected_generation
            ):
                connection.rollback()
                return False
            if full_refresh:
                connection.execute(
                    "DELETE FROM price_bars WHERE symbol = ?", (symbol,)
                )
            connection.executemany(
                """
                INSERT INTO price_bars (
                    symbol, session_date, open, high, low, close, volume, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, session_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    updated_at = excluded.updated_at
                """,
                records,
            )
            last_full = (
                timestamp
                if full_refresh or previous is None
                else previous["last_full_refresh_at"]
            )
            connection.execute(
                """
                INSERT INTO price_cache_meta (
                    symbol, last_updated_at, last_full_refresh_at, generation
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    last_updated_at = excluded.last_updated_at,
                    last_full_refresh_at = excluded.last_full_refresh_at,
                    generation = excluded.generation
                """,
                (symbol, timestamp, last_full, current_generation + 1),
            )
            connection.commit()
            return True

    def clear_prices(self, symbol):
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM price_bars WHERE symbol = ?", (symbol,)
            )
            connection.execute(
                "DELETE FROM price_cache_meta WHERE symbol = ?", (symbol,)
            )
            connection.commit()

    def save_forecast(
        self,
        symbol,
        as_of_date,
        horizon_days,
        horizon_date,
        payload,
        now=None,
    ):
        timestamp = _iso_utc(now)
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO forecasts (
                    symbol,
                    as_of_date,
                    horizon_days,
                    horizon_date,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    as_of_date,
                    int(horizon_days),
                    horizon_date,
                    encoded,
                    timestamp,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def grade_pending_forecasts(self, symbol, history, now=None):
        if history is None or history.empty or "Close" not in history:
            return 0
        close = pd.to_numeric(history["Close"], errors="coerce").dropna()
        if close.empty:
            return 0
        dates = [_session_date(value) for value in close.index]
        values = close.to_numpy(dtype=float)
        timestamp = _iso_utc(now)

        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT
                    id,
                    as_of_date,
                    horizon_days,
                    horizon_date,
                    payload_json
                FROM forecasts
                WHERE symbol = ? AND status = 'pending'
                ORDER BY as_of_date
                """,
                (symbol,),
            ).fetchall()
            updates = []
            for row in rows:
                entry_position = bisect_left(dates, row["as_of_date"])
                if (
                    entry_position >= len(dates)
                    or dates[entry_position] != row["as_of_date"]
                ):
                    continue
                position = entry_position + int(row["horizon_days"])
                if position >= len(dates):
                    continue
                payload = json.loads(row["payload_json"])
                if not _completed_session_date(
                    dates[position],
                    payload.get("exchange_timezone"),
                    now=now,
                ):
                    continue
                entry_price = float(values[entry_position])
                realized_price = float(values[position])
                realized_return = (
                    (realized_price / entry_price) - 1
                ) * 100
                updates.append(
                    (
                        realized_return,
                        realized_price,
                        dates[position],
                        timestamp,
                        row["id"],
                    )
                )
            if updates:
                connection.executemany(
                    """
                    UPDATE forecasts
                    SET status = 'graded',
                        realized_return = ?,
                        realized_price = ?,
                        outcome_date = ?,
                        graded_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    updates,
                )
            connection.commit()
        return len(updates)

    def delete_pending_forecast(self, symbol, as_of_date, horizon_days):
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                DELETE FROM forecasts
                WHERE symbol = ?
                  AND as_of_date = ?
                  AND horizon_days = ?
                  AND status = 'pending'
                """,
                (symbol, as_of_date, int(horizon_days)),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_forecasts(self, symbol, limit=None):
        query = """
            SELECT
                id,
                symbol,
                as_of_date,
                horizon_days,
                horizon_date,
                payload_json,
                status,
                realized_return,
                realized_price,
                outcome_date,
                created_at,
                graded_at
            FROM forecasts
            WHERE symbol = ?
            ORDER BY as_of_date DESC, id DESC
        """
        parameters = [symbol]
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        records = []
        for row in rows:
            item = dict(row)
            payload = json.loads(item.pop("payload_json"))
            item.update(payload)
            records.append(item)
        return records


def _price_rows_to_frame(rows):
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = pd.DataFrame(
        [
            {
                "Date": row["session_date"],
                "Open": row["open"],
                "High": row["high"],
                "Low": row["low"],
                "Close": row["close"],
                "Volume": row["volume"],
            }
            for row in rows
        ]
    )
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.set_index("Date")


def _optional_float(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def _session_date(value):
    return pd.Timestamp(value).date().isoformat()


def _iso_utc(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _completed_session_date(session_date, timezone_name, now=None):
    if not timezone_name:
        return False
    try:
        market_timezone = ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = current.astimezone(market_timezone).date()
    return date.fromisoformat(session_date) < local_date
