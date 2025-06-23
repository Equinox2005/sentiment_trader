# aggregator.py
import pandas as pd
from config import TIME_BUCKET

class Aggregator:
    def __init__(self):
        # initialize an empty DataFrame with the proper columns
        self._df = pd.DataFrame(columns=["timestamp", "sentiment"])

    def add(self, sentiment_score):
        now = pd.Timestamp.utcnow()
        # append a new row by index assignment
        self._df.loc[len(self._df)] = [now, sentiment_score]

    def get_buckets(self):
        # set timestamp as index and compute per‐bucket aggregations
        df = self._df.set_index("timestamp")
        res = (
            df["sentiment"]
            .resample(TIME_BUCKET)
            .mean()
            .to_frame()
            .dropna()
        )
        # volume = count of items in each time bucket
        res["volume"] = df["sentiment"].resample(TIME_BUCKET).size().loc[res.index]
        return res.reset_index()
