import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path


DEFAULT_MODEL_VERSION = "playbook-analog-v1"


@dataclass(frozen=True)
class ForecastProvenance:
    model_version: str
    git_commit: str
    config_hash: str
    data_vintage: str
    universe_id: int | None = None
    scan_run_id: int | None = None

    def storage_values(self):
        return asdict(self)


def content_hash(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def runtime_config_hash(values=None):
    configured = os.getenv("PLAYBOOK_CONFIG_HASH")
    if configured:
        return configured
    return content_hash(values or {})


@lru_cache(maxsize=1)
def current_git_commit():
    configured = (
        os.getenv("PLAYBOOK_GIT_COMMIT")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GITHUB_SHA")
    )
    if configured:
        return configured.strip()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def default_forecast_provenance(data_vintage, overrides=None):
    supplied = dict(overrides or {})
    model_version = supplied.get("model_version") or os.getenv(
        "PLAYBOOK_MODEL_VERSION",
        DEFAULT_MODEL_VERSION,
    )
    config_hash = supplied.get("config_hash") or runtime_config_hash(
        {
            "model_version": model_version,
            "validation_path": "walk-forward-audit",
            "entry_reference": "next-session-open",
        }
    )
    return ForecastProvenance(
        model_version=model_version,
        git_commit=supplied.get("git_commit") or current_git_commit(),
        config_hash=config_hash,
        data_vintage=data_vintage,
        universe_id=supplied.get("universe_id"),
        scan_run_id=supplied.get("scan_run_id"),
    )
