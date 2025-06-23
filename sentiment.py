import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
from transformers import pipeline

nltk.download("vader_lexicon")
_vader = SentimentIntensityAnalyzer()
_bert = pipeline("sentiment-analysis", model="nlptown/bert-base-multilingual-uncased-sentiment")

def analyze_vader(text: str) -> float:
    return _vader.polarity_scores(text)["compound"]

def analyze_bert(text: str) -> float:
    # returns star rating from 1–5, map to -1→+1
    stars = int(_bert(text)[0]["label"][0])
    return (stars - 3) / 2
