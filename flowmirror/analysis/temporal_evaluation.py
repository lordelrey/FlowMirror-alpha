"""Offline, train-only ridge evaluation of time-stamped NAV log changes.

No provider or filesystem access. Dates and daily clusters use UTC. Callers are
responsible for ensuring supplied features were known at origin_at.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from numbers import Integral, Real

import numpy as np


def _time(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an ISO timestamp with timezone") from exc


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _names(value, name):
    if (not isinstance(value, list)
            or any(not isinstance(x, str) or not x for x in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{name} must be a list of unique nonempty strings")
    return list(value)


def _validate(rows):
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise ValueError("rows must be an iterable of dictionaries") from exc
    result, seen, schema = [], set(), None
    required = {"row_id", "unit_id", "origin_at", "target_at", "target",
                "features", "state_features", "publication_features", "support_post_ids"}
    for row in iterator:
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("each row must be a dictionary with all required fields")
        for name in ("row_id", "unit_id"):
            if not isinstance(row[name], str) or not row[name]:
                raise ValueError(f"{name} must be a nonempty string")
        if row["row_id"] in seen:
            raise ValueError("duplicate row_id")
        seen.add(row["row_id"])
        state = _names(row["state_features"], "state_features")
        publication = _names(row["publication_features"], "publication_features")
        if set(state) & set(publication):
            raise ValueError("state and publication feature names must be disjoint")
        features = row["features"]
        if not isinstance(features, dict) or set(features) != set(state + publication):
            raise ValueError("features must exactly match declared feature names")
        if schema is not None and schema != (state, publication):
            raise ValueError("all rows must have identical ordered feature lists")
        schema = (state, publication)
        origin, target_at = _time(row["origin_at"], "origin_at"), _time(row["target_at"], "target_at")
        if target_at <= origin:
            raise ValueError("target_at must be after origin_at")
        posts = row["support_post_ids"]
        if not isinstance(posts, list) or any(not isinstance(x, str) or not x for x in posts):
            raise ValueError("support_post_ids must be a list of nonempty strings")
        values = {k: _number(v, f"features.{k}") for k, v in features.items()}
        result.append({"row_id": row["row_id"], "unit_id": row["unit_id"],
                       "origin_at": origin, "target_at": target_at,
                       "target": _number(row["target"], "target"), "features": values,
                       "support_post_ids": list(posts),
                       "publication_exposed": any(values[k] != 0 for k in publication)})
    result.sort(key=lambda r: (r["target_at"], r["origin_at"], r["row_id"]))
    return result, schema or ([], [])


def _summary(rows):
    def span(key):
        return {"min": min(r[key] for r in rows).isoformat(),
                "max": max(r[key] for r in rows).isoformat()} if rows else {"min": None, "max": None}
    return {"n_rows": len(rows), "n_units": len({r["unit_id"] for r in rows}),
            "n_target_days": len({r["target_at"].date() for r in rows}),
            "n_publication_exposed": sum(r["publication_exposed"] for r in rows),
            "n_support_post_rows": sum(bool(r["support_post_ids"]) for r in rows),
            "origin_at": span("origin_at"), "target_at": span("target_at"),
            "row_ids": [r["row_id"] for r in rows]}


def _fit(rows, names, alpha):
    x = np.array([[r["features"][k] for k in names] for r in rows], dtype=float).reshape(len(rows), len(names))
    y = np.array([r["target"] for r in rows])
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    z, y_mean = (x - mean) / scale, float(y.mean())
    # Augmented least squares also handles alpha=0, constants and collinearity.
    design = np.vstack((z, np.sqrt(alpha) * np.eye(len(names))))
    response = np.concatenate((y - y_mean, np.zeros(len(names))))
    coefficients = np.linalg.lstsq(design, response, rcond=None)[0] if names else np.array([])
    return {"status": "fitted", "feature_names": list(names), "alpha": alpha,
            "scaler": {"mean": mean.tolist(), "scale": scale.tolist()},
            "target_mean": y_mean, "intercept": y_mean,
            "coefficients": coefficients.tolist(), "coefficient_space": "standardized",
            "n_train": len(rows)}


def _predict(model, row):
    return float(model["intercept"] + sum(
        (row["features"][k] - m) / s * b for k, m, s, b in zip(
            model["feature_names"], model["scaler"]["mean"],
            model["scaler"]["scale"], model["coefficients"])))


def _metrics(records):
    n = len(records)
    intervals = [r for r in records if r["interval"] is not None]
    return {"n": n, "mae": float(np.mean([r["absolute_error"] for r in records])) if n else None,
            "rmse": float(np.sqrt(np.mean([r["squared_error"] for r in records]))) if n else None,
            "n_intervals": len(intervals),
            "observed_coverage": float(np.mean([r["covered"] for r in intervals])) if intervals else None,
            "mean_width": float(np.mean([r["interval"][1] - r["interval"][0] for r in intervals])) if intervals else None}


def _strata(records):
    return {"all": _metrics(records),
            "publication_exposed": _metrics([r for r in records if r["publication_exposed"]])}


def _bootstrap(daily, seed):
    result = {"method": "moving_calendar_day_block_bootstrap", "block_days": 7,
              "n_resamples": 200, "seed": seed, "estimand": "equal_target_day_mean_mae_delta",
              "confidence_level": 0.95, "ci": None,
              "note": "Resamples calendar blocks containing all funds together; not an independent-fund bootstrap.",
              "status": "insufficient_support"}
    if len(daily) < 2:
        return result
    first, last = datetime.fromisoformat(daily[0]["target_day"]), datetime.fromisoformat(daily[-1]["target_day"])
    length = (last - first).days + 1
    if length < 14:
        return result
    # Sparse block sums avoid allocating potentially huge empty calendar spans.
    offsets = np.array([(datetime.fromisoformat(d["target_day"]) - first).days for d in daily])
    values = np.array([d["mae_delta"] for d in daily])
    rng, draws = np.random.default_rng(seed), []
    for _ in range(200):
        total, count = 0.0, 0
        remaining = length
        while remaining:
            width = min(7, remaining)
            start = int(rng.integers(0, length - 7 + 1))
            mask = (offsets >= start) & (offsets < start + width)
            total += float(values[mask].sum())
            count += int(mask.sum())
            remaining -= width
        if count:
            draws.append(total / count)
    result.update(status="ok" if draws else "insufficient_support", n_valid_resamples=len(draws),
                  ci=np.quantile(draws, [0.025, 0.975]).tolist() if draws else None)
    return result


def evaluate(rows, *, train_until, calibration_until, end=None, alpha=1.0, coverage=.9, seed=2027):
    """Return a JSON-serializable report; invalid inputs raise ValueError.

    Train: target <= train_until. Calibration: origin > train_until and target
    <= calibration_until. Test: origin > calibration_until and target <= end
    (if supplied). Rows spanning either training/calibration cutoff are purged;
    remaining out-of-window rows are future_excluded, including targets > end.
    Ridge minimizes sum squared errors + alpha * squared standardized weights,
    with an unpenalized intercept. Calibration uses the empirical absolute-error
    quantile (higher order statistic), without a nominal coverage guarantee.
    """
    train_cut = _time(train_until, "train_until")
    cal_cut = _time(calibration_until, "calibration_until")
    end_cut = _time(end, "end") if end is not None else None
    if cal_cut <= train_cut or (end_cut is not None and end_cut <= cal_cut):
        raise ValueError("cutoffs must be strictly increasing")
    alpha, coverage = _number(alpha, "alpha"), _number(coverage, "coverage")
    if alpha < 0 or not 0 < coverage < 1:
        raise ValueError("alpha must be >= 0 and coverage must be between 0 and 1")
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    seed = int(seed)
    data, (state, publication) = _validate(rows)
    splits = {k: [] for k in ("train", "calibration", "test", "purged", "future_excluded")}
    for r in data:
        origin, target = r["origin_at"], r["target_at"]
        if target <= train_cut:
            split = "train"
        elif origin > train_cut and target <= cal_cut:
            split = "calibration"
        elif origin > cal_cut and (end_cut is None or target <= end_cut):
            split = "test"
        elif any(origin <= cut < target for cut in (train_cut, cal_cut)):
            split = "purged"
        else:
            split = "future_excluded"
        splits[split].append(r)
    missing = [k for k in ("train", "calibration", "test") if not splits[k]]
    report = {"status": "insufficient_support" if missing else "ok", "missing_splits": missing,
              "cutoffs": {"train_until": train_cut.isoformat(), "calibration_until": cal_cut.isoformat(),
                          "end": end_cut.isoformat() if end_cut else None},
              "alpha": alpha, "requested_coverage": coverage, "seed": seed,
              "target_definition": "next_observed_raw_nav_log_change",
              "day_timezone": "UTC", "selected_model": None,
              "notes": ["All fitting and standardization use training rows only; alpha is fixed.",
                        "Target is the next observed single-step raw NAV log change, not dividend-adjusted total return.",
                        "Temporal dependence means empirical calibration intervals do not guarantee nominal coverage.",
                        "Publication exposure means at least one nonzero publication feature.",
                        "Test metrics are descriptive and do not select a model."],
              "splits": {k: _summary(v) for k, v in splits.items()},
              "models": {}, "paired_comparisons": {}}
    model_specs = {"zero_change": [], "state_ridge": state, "publication_ridge": state + publication}
    for name, names in model_specs.items():
        if name == "zero_change":
            model = {"status": "fixed", "feature_names": [], "scaler": {"mean": [], "scale": []},
                     "coefficients": [], "intercept": 0.0, "target_mean": None, "n_train": 0}
        elif splits["train"]:
            model = _fit(splits["train"], names, alpha)
        else:
            report["models"][name] = {"status": "insufficient_training", "fit": None,
                                     "calibration": {"status": "unavailable", "n": len(splits["calibration"]), "radius": None},
                                     "predictions": [], "metrics": _strata([]), "daily_metrics": []}
            continue
        residuals = [abs(_predict(model, r) - r["target"]) for r in splits["calibration"]]
        radius = float(np.quantile(residuals, coverage, method="higher")) if residuals else None
        predictions = []
        for r in splits["test"]:
            prediction = _predict(model, r)
            error = prediction - r["target"]
            interval = [prediction - radius, prediction + radius] if radius is not None else None
            predictions.append({"row_id": r["row_id"], "unit_id": r["unit_id"],
                                "origin_at": r["origin_at"].isoformat(), "target_at": r["target_at"].isoformat(),
                                "target_day": r["target_at"].date().isoformat(), "target": r["target"],
                                "publication_exposed": r["publication_exposed"], "support_post_ids": r["support_post_ids"],
                                "prediction": prediction, "error": error, "absolute_error": abs(error),
                                "squared_error": error ** 2, "interval": interval,
                                "covered": interval[0] <= r["target"] <= interval[1] if interval else None})
        days = sorted({r["target_day"] for r in predictions})
        daily = [{"target_day": day, "metrics": _strata([r for r in predictions if r["target_day"] == day])} for day in days]
        report["models"][name] = {"status": "ok" if residuals else "insufficient_calibration", "fit": model,
                                 "calibration": {"status": "ok" if residuals else "insufficient_support",
                                                 "n": len(residuals), "radius": radius, "quantile_method": "higher"},
                                 "predictions": predictions, "metrics": _strata(predictions), "daily_metrics": daily}
    for reference, candidate in combinations(model_specs, 2):
        a, b = report["models"][reference], report["models"][candidate]
        strata = {}
        for stratum in ("all", "publication_exposed"):
            daily = []
            for left, right in zip(a["daily_metrics"], b["daily_metrics"]):
                am, bm = left["metrics"][stratum], right["metrics"][stratum]
                if am["n"]:
                    daily.append({"target_day": left["target_day"], "n": am["n"],
                                  "mae_delta": bm["mae"] - am["mae"], "rmse_delta": bm["rmse"] - am["rmse"]})
            am, bm = a["metrics"][stratum], b["metrics"][stratum]
            strata[stratum] = {"n": bm["n"] if am["n"] and bm["n"] else 0,
                               "mae_delta": bm["mae"] - am["mae"] if am["n"] and bm["n"] else None,
                               "daily_mean_mae_delta": float(np.mean([d["mae_delta"] for d in daily])) if daily else None,
                               "daily": daily, "bootstrap": _bootstrap(daily, seed)}
        report["paired_comparisons"][f"{candidate}_minus_{reference}"] = {
            "reference": reference, "candidate": candidate, "direction": "negative delta favors candidate", "strata": strata}
    return report
