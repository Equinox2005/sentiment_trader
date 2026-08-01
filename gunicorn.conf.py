"""Gunicorn settings tuned for a single disk-backed Playbook instance.

One worker with many threads keeps the SQLite cache, the in-process scheduler,
and the background scan thread inside a single process. Scaling out horizontally
would require moving storage off SQLite first.
"""

import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
preload_app = False
