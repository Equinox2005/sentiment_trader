"""Download FINRA consolidated short interest for the replay universe.

FINRA publishes bi-monthly short interest with no authentication. One request
per symbol returns that symbol's complete history, so the whole replay universe
costs a few hundred requests rather than a bulk crawl.

Coverage begins 2017-12-29, so the first two years of the 2016-2026 panel have
no short interest at all. That is a property of the source, not a bug, and the
screen has to report results on the covered subset only.

    python backtest/fetch_short_interest.py

Writes `short_interest.csv` next to this file.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "replay_2016_2026.csv")
OUT = os.path.join(HERE, "short_interest.csv")

ENDPOINT = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)
FIELDS = [
    "symbol",
    "settlement_date",
    "short_shares",
    "previous_short_shares",
    "avg_daily_volume",
    "days_to_cover",
    "change_percent",
]
WORKERS = 6
PACE_SECONDS = 0.15
ATTEMPTS = 3


def fetch_symbol(symbol):
    body = {
        "limit": 5000,
        "compareFilters": [
            {
                "fieldName": "symbolCode",
                "fieldValue": symbol,
                "compareType": "equal",
            }
        ],
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    last_error = None
    for attempt in range(ATTEMPTS):
        try:
            raw = urllib.request.urlopen(request, timeout=60).read()
            if not raw.strip():
                return []
            return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def normalise(symbol, rows):
    out = []
    for row in rows:
        settlement = row.get("settlementDate")
        if not settlement:
            continue
        out.append(
            {
                "symbol": symbol,
                "settlement_date": settlement,
                "short_shares": row.get("currentShortPositionQuantity"),
                "previous_short_shares": row.get("previousShortPositionQuantity"),
                "avg_daily_volume": row.get("averageDailyVolumeQuantity"),
                "days_to_cover": row.get("daysToCoverQuantity"),
                "change_percent": row.get("changePercent"),
            }
        )
    return out


def main():
    panel = pd.read_csv(PANEL, low_memory=False)
    symbols = sorted(panel["symbol"].dropna().unique())
    print(f"Fetching short interest for {len(symbols)} symbols", flush=True)

    written = 0
    missing = []
    failures = []
    started = time.time()

    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {}
            for symbol in symbols:
                # FINRA uses the bare ticker; the cache normalises to BRK-B.
                futures[pool.submit(fetch_symbol, symbol.replace("-", "."))] = symbol
                time.sleep(PACE_SECONDS)
            for index, future in enumerate(as_completed(futures), start=1):
                symbol = futures[future]
                try:
                    rows = future.result()
                except Exception as exc:  # noqa: BLE001
                    failures.append((symbol, str(exc)[:120]))
                    continue
                if not rows:
                    missing.append(symbol)
                    continue
                records = normalise(symbol, rows)
                writer.writerows(records)
                written += len(records)
                if index % 40 == 0:
                    print(
                        f"  [{index}/{len(symbols)}] {written} rows, "
                        f"{time.time() - started:.0f}s",
                        flush=True,
                    )

    print(
        f"\nWrote {written} rows for {len(symbols) - len(missing) - len(failures)} "
        f"symbols in {(time.time() - started) / 60:.1f}m",
        flush=True,
    )
    print(f"  no FINRA coverage : {len(missing)}", flush=True)
    print(f"  request failures  : {len(failures)}", flush=True)
    for symbol, reason in failures[:5]:
        print(f"    {symbol}: {reason}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
