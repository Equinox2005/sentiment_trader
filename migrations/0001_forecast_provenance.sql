ALTER TABLE forecasts ADD COLUMN model_version TEXT;
ALTER TABLE forecasts ADD COLUMN git_commit TEXT;
ALTER TABLE forecasts ADD COLUMN config_hash TEXT;
ALTER TABLE forecasts ADD COLUMN data_vintage TEXT;
ALTER TABLE forecasts ADD COLUMN universe_id INTEGER;
ALTER TABLE forecasts ADD COLUMN scan_run_id INTEGER;
ALTER TABLE forecasts ADD COLUMN entry_date TEXT;
ALTER TABLE forecasts ADD COLUMN entry_price REAL;

CREATE INDEX forecasts_status_horizon
ON forecasts(status, horizon_date);

CREATE INDEX forecasts_universe
ON forecasts(universe_id, as_of_date);
