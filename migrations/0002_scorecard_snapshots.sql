CREATE TABLE scorecard_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    report_json TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL,
    matured_count INTEGER NOT NULL,
    expired_ungraded_count INTEGER NOT NULL,
    cohort_start TEXT,
    cohort_end TEXT,
    model_version TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    data_vintage TEXT NOT NULL,
    universe_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX scorecard_snapshots_session
ON scorecard_snapshots(session_date, id);
