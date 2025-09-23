````markdown
# Sentiment Trader

Live and historical sentiment-driven trading signals from Reddit comments.

## Features

- Backfill: Fetches the most recent comments from your chosen subreddits.
- Real-time stream: Continues listening for new comments as they appear.
- Keyword tracking: Monitors comments matching your custom keywords (e.g. “Tesla”, “NVIDIA”).
- Sentiment analysis: Base sentiment from VADER (or your choice) plus tweaks for “bearish”, “bullish”, “red”, “green”.
- Live price lookup: Fetches the latest ticker price via Yahoo Finance.
- Per-ticker averages: Displays running average sentiment per symbol.
- Subreddit info: Shows which subreddit each comment came from.
- GUI: Simple PySimpleGUI desktop interface.

## 📦 Installation

1. **Clone the repo**

   ```bash
   git clone https://github.com/Equinox2005/sentiment_trader.git
   cd sentiment_trader
````

2. **Create & activate a virtual environment**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

## 🔧 Configuration

Create a `.env` file in the project root:

```ini
REDDIT_CLIENT_ID=your_reddit_app_client_id
REDDIT_CLIENT_SECRET=your_reddit_app_client_secret
REDDIT_USER_AGENT=YourAppName/0.1 by u/YourRedditUsername
```

## 🚀 Usage

```bash
python app_gui.py
```

1. **Enter** a comma-separated list of keywords (e.g. `Tesla, NVIDIA, AMD`).
2. **For each keyword**, enter its ticker symbol (include `$`, e.g. `$TSLA`).
3. The app backfills the last 24 hours of comments, then opens the GUI:

   * **Message pane** shows each comment + sentiment, subreddit, time, price.
   * **Summary** shows total comments and overall average sentiment.
   * **Per-ticker box** shows average sentiment per ticker.

## 🗂 File Structure

```
sentiment-trader/
├── app_gui.py         # main PySimpleGUI application
├── stream.py          # Reddit streaming logic
├── sentiment.py       # VADER (or keyword) sentiment functions
├── aggregator.py      # time-bucket aggregation
├── signals.py         # buy/sell signal logic
├── config.py          # loads your .env variables
├── requirements.txt
├── .env               # your Reddit credentials (not committed)
├── .gitignore
└── README.md
```

## Dependencies

* [PySimpleGUI](https://pypi.org/project/PySimpleGUI/)
* [PRAW](https://praw.readthedocs.io/)
* [yfinance](https://pypi.org/project/yfinance/)
* [python-dotenv](https://pypi.org/project/python-dotenv/)
* [pandas](https://pypi.org/project/pandas/)
* [nltk](https://pypi.org/project/nltk/)
* [requests](https://pypi.org/project/requests/)
* [matplotlib](https://pypi.org/project/matplotlib/) *(if using chart features)*
* [numpy](https://pypi.org/project/numpy/)
* [transformers](https://pypi.org/project/transformers/) & [torch](https://pypi.org/project/torch/) *(optional)*



```
```
