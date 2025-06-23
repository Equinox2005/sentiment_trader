import os
from dotenv import load_dotenv

load_dotenv()

REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT")

# list of tickers to track (include the "$")
KEYWORDS = [
    "$AAPL", "AAPL", "Apple",
    "$MSFT", "MSFT", "Microsoft",
    "$AMZN", "AMZN", "Amazon",
    "$GOOGL", "GOOGL", "Alphabet Class A",
    "$META", "META", "Meta Platforms",
    "$TSLA", "TSLA", "Tesla",
    "$BRK.B", "BRK.B", "Berkshire Hathaway B",
    "$NVDA", "NVDA", "NVIDIA",
    "$JPM", "JPM", "JPMorgan Chase",
    "$JNJ", "JNJ", "Johnson & Johnson",
    "$V",    "V",    "Visa",
    "$UNH",  "UNH",  "UnitedHealth",
    "$HD",   "HD",   "Home Depot",
    "$PG",   "PG",   "Procter & Gamble",
    "$MA",   "MA",   "Mastercard",
    "$DIS",  "DIS",  "Disney",
    "$BAC",  "BAC",  "Bank of America",
    "$XOM",  "XOM",  "Exxon Mobil",
    "$VZ",   "VZ",   "Verizon",
    "$T",    "T",    "AT&T",
    "$PFE",  "PFE",  "Pfizer",
    "$NFLX", "NFLX", "Netflix",
    "$KO",   "KO",   "Coca-Cola",
    "$PEP",  "PEP",  "PepsiCo",
    "$ABBV", "ABBV", "AbbVie",
    "$CVX",  "CVX",  "Chevron",
    "$MRK",  "MRK",  "Merck",
    "$WMT",  "WMT",  "Walmart",
    "$MCD",  "MCD",  "McDonald’s",
    "$ORCL", "ORCL", "Oracle",
    "$CSCO", "CSCO", "Cisco",
    "$CMCSA","CMCSA","Comcast",
    "$INTC", "INTC", "Intel",
    "$NKE",  "NKE",  "Nike",
    "$ADBE", "ADBE", "Adobe",
    "$TXN",  "TXN",  "Texas Instruments",
    "$CRM",  "CRM",  "Salesforce",
    "$ABT",  "ABT",  "Abbott Labs",
    "$COST", "COST", "Costco",
    "$AVGO", "AVGO", "Broadcom",
    "$AMD",  "AMD",  "AMD",
    "$QCOM", "QCOM", "Qualcomm",
    "$UPS",  "UPS",  "UPS",
    "$TMO",  "TMO",  "Thermo Fisher",
    "$MDT",  "MDT",  "Medtronic",
    "$LIN",  "LIN",  "Linde",
    "$LLY",  "LLY",  "Eli Lilly",
    "$HON",  "HON",  "Honeywell",
    "$AMGN", "AMGN", "Amgen",
    "$DHR",  "DHR",  "Danaher"
]

TIME_BUCKET = "1min"   # pandas resample rule
POS_THRESH  = 0.5
NEG_THRESH  = -0.5
