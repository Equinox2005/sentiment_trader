import yfinance as yf
import pandas as pd

def backtest(ticker, signals):
    data = yf.download(ticker, period="1mo", interval="1m")
    trades = []
    position = 0
    for ts, sig in signals:
        price = data.loc[ts:ts].Close.iloc[0]
        if sig == "BUY" and position == 0:
            position = 1; entry = price
        elif sig == "SELL" and position == 1:
            trades.append(price - entry)
            position = 0
    total = sum(trades)
    return {"total_pnl": total, "num_trades": len(trades)}
