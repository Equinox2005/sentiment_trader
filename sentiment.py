import math
import re


TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*")

FINANCIAL_LEXICON = {
    "accelerate": 1.3,
    "approval": 1.6,
    "beat": 1.8,
    "beats": 1.8,
    "boost": 1.4,
    "breakthrough": 1.8,
    "bullish": 2.0,
    "buyback": 1.4,
    "confident": 1.3,
    "dividend": 0.9,
    "expand": 1.1,
    "gain": 1.2,
    "gains": 1.2,
    "growth": 1.4,
    "high": 0.7,
    "improve": 1.3,
    "innovation": 1.1,
    "outperform": 1.8,
    "profit": 1.4,
    "profitable": 1.5,
    "rally": 1.7,
    "record": 1.2,
    "recovery": 1.2,
    "resilient": 1.3,
    "rise": 1.2,
    "rises": 1.2,
    "strong": 1.3,
    "surge": 1.8,
    "upgrade": 1.8,
    "upside": 1.5,
    "win": 1.3,
    "bearish": -2.0,
    "concern": -1.1,
    "cut": -1.1,
    "decline": -1.3,
    "default": -2.2,
    "delay": -1.0,
    "downgrade": -1.8,
    "drop": -1.3,
    "fall": -1.2,
    "falls": -1.2,
    "fraud": -2.4,
    "investigation": -1.5,
    "layoff": -1.3,
    "layoffs": -1.3,
    "lawsuit": -1.5,
    "loss": -1.5,
    "miss": -1.8,
    "misses": -1.8,
    "plunge": -2.0,
    "probe": -1.4,
    "recall": -1.6,
    "risk": -0.9,
    "slump": -1.7,
    "slowdown": -1.5,
    "weak": -1.3,
    "warning": -1.4,
}

NEGATIONS = {
    "barely",
    "hardly",
    "isn't",
    "never",
    "no",
    "not",
    "wasn't",
    "without",
}

INTENSIFIERS = {
    "considerably": 1.35,
    "deeply": 1.35,
    "much": 1.2,
    "sharply": 1.4,
    "significantly": 1.4,
    "slightly": 0.7,
    "very": 1.3,
}


def analyze_financial_text(text):
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    raw_score = 0.0
    matched = []

    for index, token in enumerate(tokens):
        base_score = FINANCIAL_LEXICON.get(token)
        if base_score is None:
            continue

        context = tokens[max(0, index - 3) : index]
        multiplier = -1.0 if any(word in NEGATIONS for word in context) else 1.0
        if index and tokens[index - 1] in INTENSIFIERS:
            multiplier *= INTENSIFIERS[tokens[index - 1]]

        contribution = base_score * multiplier
        raw_score += contribution
        matched.append((token, contribution))

    score = math.tanh(raw_score / 3)
    if score >= 0.18:
        label = "Positive"
    elif score <= -0.18:
        label = "Negative"
    else:
        label = "Neutral"

    ordered_terms = [
        term
        for term, _ in sorted(
            matched,
            key=lambda match: abs(match[1]),
            reverse=True,
        )
    ]
    return {
        "score": round(score, 4),
        "label": label,
        "terms": list(dict.fromkeys(ordered_terms)),
    }


def analyze_vader(text):
    return analyze_financial_text(text)["score"]
