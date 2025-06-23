import time
import re

import yfinance as yf
from stream import RedditStreamer
from sentiment import analyze_vader
from aggregator import Aggregator
from signals import generate_signals

def main():
    # 1) Prompt the user for keywords
    raw = input("Enter keywords to track (separated by commas): ")
    keywords = [kw.strip() for kw in raw.split(",") if kw.strip()]
    if not keywords:
        print("No keywords provided, exiting.")
        return

    # 2) For each keyword, ask for its ticker symbol
    kw_to_ticker = {}
    for kw in keywords:
        ticker = input(f"Enter the ticker symbol for '{kw}' (include $): ").strip().upper()
        if not ticker.startswith("$"):
            ticker = "$" + ticker
        kw_to_ticker[kw] = ticker

    print("\nTracking:")
    for kw, ticker in kw_to_ticker.items():
        print(f"  • {kw} → {ticker}")
    print()

    # 3) Compile whole-word regex patterns for each keyword
    patterns = {
        kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        for kw in keywords
    }

    # 4) Initialize the sentiment aggregator
    agg = Aggregator()
    # keep track of last average sentiment
    previous_avg = None

    # 5) Define the callback for each incoming comment
    def on_message(text: str):
        nonlocal previous_avg

        # find which keyword(s) match
        matched = [kw for kw, pat in patterns.items() if pat.search(text)]
        if not matched:
            return

        # take the first match
        kw = matched[0]
        ticker = kw_to_ticker[kw]
        symbol = ticker.lstrip("$")

        # print the comment and its sentiment
        print("\nMessage:", text)
        score = analyze_vader(text)
        print(f"Sentiment score: {score:.2f}")

        # fetch and print the latest price
        try:
            hist = yf.Ticker(symbol).history(period="1d", interval="1m")
            price = hist["Close"].iloc[-1]
            print(f"Current price for {ticker}: ${price:.2f}")
        except Exception as e:
            print(f"Could not fetch price for {ticker}: {e}")

        # add score, bucket it, and generate any signals
        agg.add(score)
        buckets = agg.get_buckets()
        for ts, sig in generate_signals(buckets):
            print(f"{ts.isoformat()} → {sig}")

        # --- New summary output ---
        total_msgs = agg._df.shape[0]
        avg_sent = agg._df["sentiment"].mean()
        if previous_avg is None:
            change = 0.0
        else:
            change = avg_sent - previous_avg

        print(
            f"\nSummary:\n"
            f"  Total messages recorded: {total_msgs}\n"
            f"  Average sentiment: {avg_sent:.2f}\n"
            f"  Change since last: {change:+.2f}"
        )

        previous_avg = avg_sent

    # 6) Start the Reddit stream on investing-related subreddits
    rd = RedditStreamer(on_message)
    rd.start(subreddits=[
        "investing", "wallstreetbets", "stocks", "StockMarket",
        "personalfinance", "ValueInvesting", "DividendInvestor", "ETF",
        "Options", "optionsTrading", "RobinHood", "Daytrading",
        "FinancialPlanning", "financialindependence", "SecurityAnalysis",
        "incomeinvesting", "CryptoCurrency"
    ])
    print("Streaming comments… press Ctrl+C to stop")

    # 7) Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down")

if __name__ == "__main__":
    main()
