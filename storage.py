import json
import os
import sqlite3
import threading
import time
from bisect import bisect_left
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class PlaybookStore:
    """Concurrency-safe persistent storage for prices and forecast records."""

    def __init__(self, path, scan_retention_runs=None):
        self.path = os.path.abspath(path)
        configured_retention = (
            scan_retention_runs
            if scan_retention_runs is not None
            else os.getenv("PLAYBOOK_SCAN_RETENTION_RUNS", "30")
        )
        self.scan_retention_runs = max(1, int(configured_retention))
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_universes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_timestamp TEXT,
                    fetched_at TEXT NOT NULL,
                    constituents_json TEXT NOT NULL,
                    constituent_count INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    universe_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    total_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    runtime_seconds REAL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    FOREIGN KEY (universe_id) REFERENCES scan_universes(id),
                    UNIQUE(session_date, algorithm_version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    display_symbol TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    sector TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claim_owner TEXT,
                    eligible INTEGER NOT NULL DEFAULT 0,
                    side TEXT,
                    opportunity_score REAL,
                    rank INTEGER,
                    payload_json TEXT,
                    error TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES scan_runs(id) ON DELETE CASCADE,
                    UNIQUE(run_id, symbol)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS scan_results_run_rank
                ON scan_results(run_id, eligible, side, rank)
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
            scan_result_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(scan_results)"
                ).fetchall()
            }
            if "claim_owner" not in scan_result_columns:
                connection.execute(
                    "ALTER TABLE scan_results ADD COLUMN claim_owner TEXT"
                )
            if "side" not in scan_result_columns:
                connection.execute(
                    "ALTER TABLE scan_results ADD COLUMN side TEXT"
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
            - timedelta(seconds=float(ttl_seconds))
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
            - timedelta(seconds=float(ttl_seconds))
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

    def save_scan_universe(
        self,
        constituents,
        source,
        source_timestamp=None,
        now=None,
    ):
        encoded = json.dumps(
            constituents,
            separators=(",", ":"),
            allow_nan=False,
        )
        timestamp = _iso_utc(now)
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_universes (
                    source,
                    source_timestamp,
                    fetched_at,
                    constituents_json,
                    constituent_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source,
                    source_timestamp,
                    timestamp,
                    encoded,
                    len(constituents),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def latest_scan_universe(self):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    source,
                    source_timestamp,
                    fetched_at,
                    constituents_json,
                    constituent_count
                FROM scan_universes
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["constituents"] = json.loads(
            result.pop("constituents_json")
        )
        return result

    def acquire_scan_run(
        self,
        session_date,
        algorithm_version,
        universe_id,
        constituents,
        owner,
        lease_seconds=900,
        now=None,
    ):
        current = _utc_datetime(now)
        timestamp = _iso_utc(current)
        lease_expires = _iso_utc(
            current + timedelta(seconds=float(lease_seconds))
        )
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM scan_runs
                WHERE session_date = ? AND algorithm_version = ?
                """,
                (session_date, algorithm_version),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO scan_runs (
                        session_date,
                        algorithm_version,
                        universe_id,
                        total_count,
                        started_at,
                        updated_at,
                        lease_owner,
                        lease_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_date,
                        algorithm_version,
                        int(universe_id),
                        len(constituents),
                        timestamp,
                        timestamp,
                        owner,
                        lease_expires,
                    ),
                )
                run_id = int(cursor.lastrowid)
                connection.executemany(
                    """
                    INSERT INTO scan_results (
                        run_id,
                        symbol,
                        display_symbol,
                        company_name,
                        sector
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            item["symbol"],
                            item.get("display_symbol", item["symbol"]),
                            item.get("name", item["symbol"]),
                            item.get("sector", ""),
                        )
                        for item in constituents
                    ],
                )
                acquired = True
            else:
                run_id = int(row["id"])
                if row["status"] in {"completed", "partial"}:
                    connection.commit()
                    return self._scan_run_from_row(row), False
                existing_expiry = _parse_utc(row["lease_expires_at"])
                lease_active = (
                    existing_expiry is not None
                    and existing_expiry > current
                    and row["lease_owner"] != owner
                )
                if lease_active:
                    connection.commit()
                    return self._scan_run_from_row(row), False
                reset_statuses = (
                    ("running", "failed")
                    if row["status"] == "failed"
                    else ("running",)
                )
                placeholders = ",".join("?" for _ in reset_statuses)
                connection.execute(
                    f"""
                    UPDATE scan_results
                    SET status = 'pending',
                        claim_owner = NULL,
                        error = NULL,
                        started_at = NULL,
                        completed_at = NULL
                    WHERE run_id = ? AND status IN ({placeholders})
                    """,
                    (run_id, *reset_statuses),
                )
                connection.execute(
                    """
                    UPDATE scan_runs
                    SET status = 'running',
                        updated_at = ?,
                        completed_at = NULL,
                        runtime_seconds = NULL,
                        lease_owner = ?,
                        lease_expires_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, owner, lease_expires, run_id),
                )
                acquired = True
            connection.commit()
        return self.get_scan_run(run_id), acquired

    def heartbeat_scan_run(
        self,
        run_id,
        owner,
        lease_seconds=900,
        now=None,
    ):
        current = _utc_datetime(now)
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE scan_runs
                SET updated_at = ?, lease_expires_at = ?
                WHERE id = ?
                  AND status = 'running'
                  AND lease_owner = ?
                """,
                (
                    _iso_utc(current),
                    _iso_utc(
                        current + timedelta(seconds=float(lease_seconds))
                    ),
                    int(run_id),
                    owner,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def pending_scan_symbols(self, run_id):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT symbol, display_symbol, company_name, sector
                FROM scan_results
                WHERE run_id = ? AND status = 'pending'
                ORDER BY symbol
                """,
                (int(run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_scan_symbol(self, run_id, symbol, owner, now=None):
        timestamp = _iso_utc(now)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status, lease_owner, lease_expires_at
                FROM scan_runs
                WHERE id = ?
                """,
                (int(run_id),),
            ).fetchone()
            expiry = _parse_utc(run["lease_expires_at"]) if run else None
            if (
                run is None
                or run["status"] != "running"
                or run["lease_owner"] != owner
                or expiry is None
                or expiry <= _utc_datetime(now)
            ):
                connection.rollback()
                return False
            cursor = connection.execute(
                """
                UPDATE scan_results
                SET status = 'running',
                    claim_owner = ?,
                    started_at = ?,
                    error = NULL
                WHERE run_id = ? AND symbol = ? AND status = 'pending'
                """,
                (owner, timestamp, int(run_id), symbol),
            )
            connection.commit()
            return cursor.rowcount > 0

    def save_scan_result(
        self,
        run_id,
        symbol,
        status,
        owner,
        payload=None,
        error=None,
        now=None,
    ):
        if status not in {"completed", "failed", "skipped"}:
            raise ValueError("Invalid scan result status.")
        encoded = (
            json.dumps(payload, separators=(",", ":"), allow_nan=False)
            if payload is not None
            else None
        )
        eligible = bool(payload and payload.get("eligible"))
        score = (
            float(payload["opportunity_score"])
            if payload and payload.get("opportunity_score") is not None
            else None
        )
        side = payload.get("side") if payload else None
        timestamp = _iso_utc(now)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE scan_results
                SET status = ?,
                    claim_owner = NULL,
                    eligible = ?,
                    side = ?,
                    opportunity_score = ?,
                    payload_json = ?,
                    error = ?,
                    completed_at = ?
                WHERE run_id = ?
                  AND symbol = ?
                  AND status = 'running'
                  AND claim_owner = ?
                  AND EXISTS (
                      SELECT 1
                      FROM scan_runs r
                      WHERE r.id = scan_results.run_id
                        AND r.status = 'running'
                        AND r.lease_owner = ?
                        AND r.lease_expires_at > ?
                  )
                """,
                (
                    status,
                    int(eligible),
                    side,
                    score,
                    encoded,
                    error,
                    timestamp,
                    int(run_id),
                    symbol,
                    owner,
                    owner,
                    timestamp,
                ),
            )
            if cursor.rowcount:
                self._refresh_scan_counts(connection, run_id, timestamp)
            connection.commit()
            return cursor.rowcount > 0

    def finish_scan_run(self, run_id, owner, warnings=None, now=None):
        current = _utc_datetime(now)
        timestamp = _iso_utc(current)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT *
                FROM scan_runs
                WHERE id = ? AND lease_owner = ?
                """,
                (int(run_id), owner),
            ).fetchone()
            if not run:
                connection.rollback()
                return None
            counts = self._scan_counts(connection, run_id)
            unfinished = counts["pending"] + counts["running"]
            if unfinished:
                connection.rollback()
                raise RuntimeError(
                    f"Cannot finish a scan with {unfinished} unfinished symbols."
                )
            final_status = (
                "partial"
                if counts["failed"] or counts["skipped"]
                else "completed"
            )
            final_warnings = list(warnings or [])
            if counts["failed"]:
                final_warnings.append(
                    f"{counts['failed']} symbols remained unavailable after retries; "
                    "the published board is partial."
                )
            if counts["skipped"]:
                final_warnings.append(
                    f"{counts['skipped']} symbols lacked enough clean history and were skipped."
                )
            started = _parse_utc(run["started_at"])
            runtime = (
                max(0.0, (current - started).total_seconds())
                if started is not None
                else None
            )
            connection.execute(
                """
                UPDATE scan_runs
                SET status = ?,
                    completed_count = ?,
                    failed_count = ?,
                    skipped_count = ?,
                    warnings_json = ?,
                    updated_at = ?,
                    completed_at = ?,
                    runtime_seconds = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL
                WHERE id = ?
                """,
                (
                    final_status,
                    counts["completed"],
                    counts["failed"],
                    counts["skipped"],
                    json.dumps(final_warnings, separators=(",", ":")),
                    timestamp,
                    timestamp,
                    runtime,
                    int(run_id),
                ),
            )
            connection.execute(
                "UPDATE scan_results SET rank = NULL WHERE run_id = ?",
                (int(run_id),),
            )
            updates = []
            for side in ("long", "short"):
                ranked = connection.execute(
                    """
                    SELECT symbol
                    FROM scan_results
                    WHERE run_id = ?
                      AND eligible = 1
                      AND status = 'completed'
                      AND side = ?
                    ORDER BY opportunity_score DESC, symbol
                    """,
                    (int(run_id), side),
                ).fetchall()
                updates.extend(
                    (rank, int(run_id), row["symbol"])
                    for rank, row in enumerate(ranked, start=1)
                )
            connection.executemany(
                """
                UPDATE scan_results SET rank = ?
                WHERE run_id = ? AND symbol = ?
                """,
                updates,
            )
            self._prune_scan_history(connection)
            connection.commit()
        return self.get_scan_run(run_id, include_results=True)

    def _prune_scan_history(self, connection):
        """Bound detailed board history while allowing SQLite page reuse."""

        connection.execute(
            """
            DELETE FROM scan_runs
            WHERE id IN (
                SELECT id
                FROM scan_runs
                ORDER BY session_date DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.scan_retention_runs,),
        )
        connection.execute(
            """
            DELETE FROM scan_universes
            WHERE NOT EXISTS (
                SELECT 1
                FROM scan_runs
                WHERE scan_runs.universe_id = scan_universes.id
            )
            """
        )

    def fail_scan_run(self, run_id, owner, warning, now=None):
        timestamp = _iso_utc(now)
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE scan_runs
                SET status = 'failed',
                    warnings_json = ?,
                    updated_at = ?,
                    completed_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL
                WHERE id = ? AND lease_owner = ?
                """,
                (
                    json.dumps([str(warning)], separators=(",", ":")),
                    timestamp,
                    timestamp,
                    int(run_id),
                    owner,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get_scan_run(self, run_id, include_results=False):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT r.*, u.source AS universe_source,
                       u.source_timestamp AS universe_source_timestamp,
                       u.fetched_at AS universe_fetched_at
                FROM scan_runs r
                JOIN scan_universes u ON u.id = r.universe_id
                WHERE r.id = ?
                """,
                (int(run_id),),
            ).fetchone()
            result_rows = (
                connection.execute(
                    """
                    SELECT *
                    FROM scan_results
                    WHERE run_id = ?
                    ORDER BY
                        CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                        CASE side WHEN 'long' THEN 0 WHEN 'short' THEN 1 ELSE 2 END,
                        rank,
                        symbol
                    """,
                    (int(run_id),),
                ).fetchall()
                if row and include_results
                else []
            )
        if not row:
            return None
        result = self._scan_run_from_row(row)
        if include_results:
            result["results"] = [
                self._scan_result_from_row(item) for item in result_rows
            ]
        return result

    def latest_completed_scan(self, include_results=True):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id
                FROM scan_runs
                WHERE status IN ('completed', 'partial')
                ORDER BY session_date DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return (
            self.get_scan_run(row["id"], include_results=include_results)
            if row
            else None
        )

    def active_scan_run(self, now=None):
        cutoff = _iso_utc(now)
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id
                FROM scan_runs
                WHERE status = 'running'
                  AND lease_expires_at > ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (cutoff,),
            ).fetchone()
        return self.get_scan_run(row["id"]) if row else None

    def list_scan_runs(self, limit=20):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM scan_runs
                ORDER BY session_date DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [
            self.get_scan_run(row["id"], include_results=False)
            for row in rows
        ]

    @staticmethod
    def _scan_run_from_row(row):
        result = dict(row)
        result["warnings"] = json.loads(
            result.pop("warnings_json", "[]") or "[]"
        )
        return result

    @staticmethod
    def _scan_result_from_row(row):
        result = dict(row)
        payload = json.loads(result.pop("payload_json")) if result.get(
            "payload_json"
        ) else {}
        result.update(payload)
        result["eligible"] = bool(result["eligible"])
        return result

    @staticmethod
    def _scan_counts(connection, run_id):
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM scan_results
            WHERE run_id = ?
            GROUP BY status
            """,
            (int(run_id),),
        ).fetchall()
        counts = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
        }
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return counts

    def _refresh_scan_counts(self, connection, run_id, timestamp):
        counts = self._scan_counts(connection, run_id)
        connection.execute(
            """
            UPDATE scan_runs
            SET completed_count = ?,
                failed_count = ?,
                skipped_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                counts["completed"],
                counts["failed"],
                counts["skipped"],
                timestamp,
                int(run_id),
            ),
        )


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


def _utc_datetime(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return _utc_datetime(parsed)


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
