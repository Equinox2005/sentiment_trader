# app_gui.py

import threading
import queue
import re
import time
from datetime import datetime

import PySimpleGUI as sg
import yfinance as yf
import praw

from sentiment import analyze_vader
from aggregator import Aggregator
from signals import generate_signals
from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

sg.theme("DarkGrey12")

def _fetch_price(ticker):
    symbol = ticker.lstrip("$")
    try:
        return yf.Ticker(symbol).history(period="1d", interval="1m")["Close"].iloc[-1]
    except Exception:
        return None

def fetch_historical_comments_praw(patterns, kw_to_ticker, out_queue, subs, per_sub=500, hours=24):
    """
    Grab up to `per_sub` recent comments per subreddit via PRAW,
    but only those from the past `hours` hours.
    """
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT
    )
    cutoff = time.time() - hours * 3600

    for sub in subs:
        count = 0
        try:
            for c in reddit.subreddit(sub).comments(limit=per_sub):
                if c.created_utc < cutoff:
                    break
                text = c.body
                matched = [kw for kw, pat in patterns.items() if pat.search(text)]
                if not matched:
                    continue
                ticker = kw_to_ticker[matched[0]]
                score  = analyze_vader(text)
                price  = _fetch_price(ticker)
                ts     = datetime.fromtimestamp(c.created_utc)
                out_queue.put({
                    "text":      text,
                    "ticker":    ticker,
                    "score":     score,
                    "price":     price,
                    "subreddit": sub,
                    "time":      ts
                })
                count += 1
        except Exception:
            pass
        print(f"[BACKFILL] r/{sub}: enqueued {count} comments from last {hours}h")

def start_reddit_stream(patterns, kw_to_ticker, subs, out_queue):
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT
    )
    for comment in reddit.subreddit("+".join(subs)).stream.comments(skip_existing=True):
        text = comment.body
        matched = [kw for kw, pat in patterns.items() if pat.search(text)]
        if not matched:
            continue
        ticker = kw_to_ticker[matched[0]]
        score  = analyze_vader(text)
        price  = _fetch_price(ticker)
        out_queue.put({
            "text":      text,
            "ticker":    ticker,
            "score":     score,
            "price":     price,
            "subreddit": comment.subreddit.display_name,
            "time":      datetime.now()
        })

def main():
    # 1) Prompt for keywords & tickers
    raw = sg.popup_get_text("Enter keywords to track (comma-separated)", "Setup")
    if not raw:
        return
    keywords = [kw.strip() for kw in raw.split(",") if kw.strip()]

    kw_to_ticker = {}
    for kw in keywords:
        t = sg.popup_get_text(f"Ticker for '{kw}' (include $)", "Setup")
        if not t:
            return
        t = t.strip().upper()
        if not t.startswith("$"):
            t = "$" + t
        kw_to_ticker[kw] = t

    patterns = {kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in keywords}

    # 2) Define all subreddits to track
    subreddits = [
        "investing",
        "wallstreetbets",
        "stocks",
        "StockMarket",
        "personalfinance",
        "ValueInvesting",
        "DividendInvestor",
        "ETF",
        "Options",
        "optionsTrading",
        "RobinHood",
        "Daytrading",
        "FinancialPlanning",
        "financialindependence",
        "SecurityAnalysis",
        "incomeinvesting",
        "CryptoCurrency"
    ]

    # 3) Backfill via PRAW for past 24 hours
    msg_queue = queue.Queue()
    fetch_historical_comments_praw(patterns, kw_to_ticker, msg_queue, subreddits, per_sub=5000, hours=24)

    # 4) Start live streaming thread
    threading.Thread(
        target=start_reddit_stream,
        args=(patterns, kw_to_ticker, subreddits, msg_queue),
        daemon=True
    ).start()

    # 5) Prepare data stores
    agg           = Aggregator()
    ticker_scores = {t: [] for t in kw_to_ticker.values()}

    # 6) Build GUI
    header = sg.Text(f"Tracking: {', '.join(keywords)}", font=("Segoe UI",14,"bold"))
    msg_box = sg.Multiline(
        key="-MSG-", disabled=True, autoscroll=True,
        expand_x=True, expand_y=True,
        font=("Courier New",12), border_width=1
    )
    summary = sg.Frame(
        "Summary",
        [[
            sg.Text("Msgs", font=("Segoe UI",12)),
            sg.Text("0", key="-TOTAL-", font=("Segoe UI",12), pad=((5,20),0)),
            sg.Text("Avg Sent", font=("Segoe UI",12)),
            sg.Text("0.00", key="-AVG-", font=("Segoe UI",12))
        ]],
        title_color="#00CCFF",
        relief=sg.RELIEF_SUNKEN,
        expand_x=True,
        font=("Segoe UI",12,"bold")
    )
    ticker_summary = sg.Multiline(
        key="-TICKER_SUM-",
        size=(30,5),
        disabled=True,
        font=("Segoe UI",12),
        border_width=1
    )

    layout = [
        [header],
        [msg_box],
        [summary, ticker_summary],
        [sg.Push(), sg.Button("Exit", size=(8,1), font=("Segoe UI",12), pad=(0,10))]
    ]

    window = sg.Window(
        "Sentiment Trader", layout,
        resizable=True, size=(900,650),
        finalize=True, font=("Segoe UI",12)
    )
    window["-MSG-"].expand(expand_x=True, expand_y=True)

    # helper to process one message dict
    def process_one(msg):
        text      = msg["text"]
        ticker    = msg["ticker"]
        score     = msg["score"]
        price     = msg["price"]
        subreddit = msg["subreddit"]
        ts_val    = msg["time"]
        timestamp = ts_val.strftime("%m/%d/%Y %H:%M:%S")

        # apply keyword tweaks
        adj = score
        tl  = text.lower()
        if "bearish" in tl: adj -= 0.1
        if "bullish" in tl: adj += 0.1
        if "red" in tl: adj   -= 0.1
        if "green" in tl: adj += 0.1

        # update aggregator & per-ticker
        agg.add(adj)
        ticker_scores[ticker].append(adj)
        sigs = generate_signals(agg.get_buckets())

        total = agg._df.shape[0]
        avg   = agg._df["sentiment"].mean()
        lines = [
            f"{t}: {sum(v)/len(v):.2f}" if v else f"{t}: N/A"
            for t, v in ticker_scores.items()
        ]

        price_str = f"{ticker} ${price:.2f}" if price else "N/A"
        detail    = f"Sent: {adj:.2f} Sub: r/{subreddit} Time: {timestamp} Price: {price_str}"
        window["-MSG-"].update(f"{text}\n{detail}\n\n", append=True)
        window["-TOTAL-"].update(str(total))
        window["-AVG-"].update(f"{avg:.2f}")
        window["-TICKER_SUM-"].update("\n".join(lines))

        for s_ts, sig in sigs:
            sg.popup_auto_close(f"{s_ts} → {sig}", auto_close_duration=2)

    # 7) Drain historical comments first
    while True:
        try:
            m = msg_queue.get_nowait()
        except queue.Empty:
            break
        process_one(m)

    # 8) Live update loop
    while True:
        event, _ = window.read(timeout=200)
        if event in (sg.WIN_CLOSED, "Exit"):
            break
        for _ in range(5):
            try:
                m = msg_queue.get_nowait()
            except queue.Empty:
                break
            process_one(m)

    window.close()

if __name__ == "__main__":
    main()
