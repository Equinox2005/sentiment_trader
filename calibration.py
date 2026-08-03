"""Empirical recalibration of the analog probability.

The walk-forward audit grades each matcher against its own history, but it
never measured whether the published probability means what it says across
the whole market. A 26,347-cell blind replay (220 symbols x 126 non-overlapping
21-session windows, 2016-01-04 to 2026-06-12) did, and the answer was no: the
raw probability is monotonic but far too dispersed. Names carrying a stated 75%
chance resolved up about 58% of the time, and the raw forecast scored *worse*
than the asset's own base rate.

Shrinking the stated edge toward that base rate fixes the dispersion without
discarding the ordering the matcher does provide.

    calibrated_logit = logit(baseline) + s * (logit(raw) - logit(baseline))

``s`` was fitted by minimising the Brier score on the first half of the replay
(2016-01-04 to 2021-03-08, n=12,505) and then measured on the untouched second
half (2021-04-07 to 2026-06-12, n=13,809):

    raw probability          Brier 0.2552   skill -1.2%
    shrink to baseline       Brier 0.2489   skill +1.3%
    baseline alone (s=0)     Brier 0.2494   skill +1.1%

Two honest consequences are visible in those numbers. The matcher's own
contribution over a plain base rate is about 0.2 points of Brier skill, and a
fitted ``s`` of 0.132 means roughly seven eighths of the stated edge was noise.

This transform is deliberately presentation-only. ``edge_points`` and
``analog_probability_up`` keep their raw values so board ranking, tier gating,
and every forecast already written to the ledger stay on one consistent
definition.
"""

from __future__ import annotations

import math
import os

# Fitted on the training half of the blind replay; see module docstring.
DEFAULT_SHRINK = 0.132

CALIBRATION_PROVENANCE = {
    "method": "logit shrinkage toward the as-of base rate",
    "fitted_on": "2016-01-04..2021-03-08",
    "fitted_observations": 12505,
    "validated_on": "2021-04-07..2026-06-12",
    "validated_observations": 13809,
    "validated_brier_skill_points": 1.3,
    "uncalibrated_brier_skill_points": -1.2,
}

_ENV_SHRINK = "PLAYBOOK_PROBABILITY_SHRINK"


def shrink_factor():
    """Active shrink factor. 1.0 disables calibration, 0.0 quotes the base rate."""
    raw = os.environ.get(_ENV_SHRINK)
    if raw is None:
        return DEFAULT_SHRINK
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SHRINK
    return min(1.0, max(0.0, value))


def _logit(percent):
    ratio = min(0.99, max(0.01, percent / 100.0))
    return math.log(ratio / (1.0 - ratio))


def calibrate_probability(raw_percent, baseline_percent, shrink=None):
    """Pull a stated probability toward the asset's own as-of up-rate.

    Both arguments are percentages. The result is a percentage in [1, 99].
    """
    try:
        raw = float(raw_percent)
        baseline = float(baseline_percent)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(raw) and math.isfinite(baseline)):
        return None

    factor = shrink_factor() if shrink is None else min(1.0, max(0.0, float(shrink)))
    adjusted = _logit(baseline) + factor * (_logit(raw) - _logit(baseline))
    probability = 100.0 / (1.0 + math.exp(-adjusted))
    return min(99.0, max(1.0, probability))
