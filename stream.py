# stream.py
import praw
import threading
from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

class RedditStreamer:
    def __init__(self, on_comment):
        self.reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT
        )
        self.on_comment = on_comment

    def start(self, subreddits=["all"]):
        def _loop():
            for comment in self.reddit.subreddit("+".join(subreddits)).stream.comments():
                self.on_comment(comment.body)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
