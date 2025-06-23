from config import POS_THRESH, NEG_THRESH

def generate_signals(buckets):
    signals = []
    for _, row in buckets.iterrows():
        if row.sentiment >= POS_THRESH and row.volume > buckets.volume.mean():
            signals.append((row.timestamp, "BUY"))
        elif row.sentiment <= NEG_THRESH:
            signals.append((row.timestamp, "SELL"))
    return signals
