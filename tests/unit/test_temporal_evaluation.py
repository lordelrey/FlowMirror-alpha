"""Pure in-memory temporal evaluation checks, without providers or run output."""
import copy
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from flowmirror.analysis.temporal_evaluation import evaluate


CUTS = dict(train_until="2026-01-10T00:00:00Z", calibration_until="2026-01-20T00:00:00Z")


def row(day, *, unit="f", x=None, p=0.0, target=None):
    date = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)
    x = float(day if x is None else x)
    return {"row_id": f"{unit}-{day}", "unit_id": unit,
            "origin_at": (date - timedelta(hours=12)).isoformat(), "target_at": date.isoformat(),
            "target": 2 * x + 3 * p + 1 if target is None else target,
            "features": {"state": x, "pub": p}, "state_features": ["state"],
            "publication_features": ["pub"], "support_post_ids": ["post"] if p else []}


def sample():
    return [row(d, p=float(d % 3)) for d in range(2, 41)]


def test_real_fit_and_no_input_mutation():
    rows = sample()
    before = copy.deepcopy(rows)
    report = evaluate(rows, alpha=0, **CUTS)
    assert rows == before
    assert report["status"] == "ok"
    model = report["models"]["publication_ridge"]
    assert model["metrics"]["all"]["mae"] < 1e-10
    fit = model["fit"]
    assert np.array(fit["coefficients"]) / fit["scaler"]["scale"] == pytest.approx([2, 3])
    assert report["models"]["zero_change"]["predictions"][0]["prediction"] == 0
    assert report["selected_model"] is None
    json.dumps(report, allow_nan=False)


def test_fixed_alpha_matches_normal_equations():
    rows = sample()
    report = evaluate(rows, alpha=2.5, **CUTS)
    train = rows[:9]
    x = np.array([[r["features"]["state"], r["features"]["pub"]] for r in train])
    y = np.array([r["target"] for r in train])
    z = (x - x.mean(0)) / x.std(0)
    expected = np.linalg.solve(z.T @ z + 2.5 * np.eye(2), z.T @ (y - y.mean()))
    assert report["models"]["publication_ridge"]["fit"]["coefficients"] == pytest.approx(expected)


def test_held_out_labels_and_features_do_not_change_fit():
    rows = sample()
    original = evaluate(rows, **CUTS)
    for r in rows:
        if r["origin_at"] > "2026-01-20":
            r["target"] += 1000
    changed = evaluate(rows, **CUTS)
    for name in original["models"]:
        assert original["models"][name]["fit"] == changed["models"][name]["fit"]
        assert original["models"][name]["calibration"] == changed["models"][name]["calibration"]
        assert [r["prediction"] for r in original["models"][name]["predictions"]] == [r["prediction"] for r in changed["models"][name]["predictions"]]
    for r in rows[9:]:
        r["features"]["state"] += 500
        r["target"] -= 99
    changed_features = evaluate(rows, **CUTS)
    assert original["models"]["publication_ridge"]["fit"] == changed_features["models"]["publication_ridge"]["fit"]


def test_cutoff_equalities_purge_and_future_exclusion():
    rows = [row(d) for d in (10, 11, 20, 21, 22, 23, 24)]
    rows[1]["origin_at"] = CUTS["train_until"]
    rows[3]["origin_at"] = CUTS["calibration_until"]
    rows[4]["origin_at"] = "2026-01-09T00:00:00Z"
    report = evaluate(rows, end="2026-01-23T00:00:00Z", **CUTS)
    splits = report["splits"]
    assert splits["train"]["row_ids"] == ["f-10"]
    assert splits["calibration"]["row_ids"] == ["f-20"]
    assert splits["test"]["row_ids"] == ["f-23"]
    assert splits["purged"]["row_ids"] == ["f-11", "f-21", "f-22"]
    assert splits["future_excluded"]["row_ids"] == ["f-24"]
    assert sum(s["n_rows"] for s in splits.values()) == len(rows)


def test_constants_empty_features_and_no_exposure():
    rows = [row(d, x=1, target=4) for d in (2, 3, 12, 22)]
    report = evaluate(rows, **CUTS)
    fit = report["models"]["publication_ridge"]["fit"]
    assert fit["scaler"]["scale"] == [1, 1]
    assert fit["coefficients"] == [0, 0]
    assert report["models"]["state_ridge"]["metrics"]["all"]["mae"] == 0
    exposed = report["models"]["publication_ridge"]["metrics"]["publication_exposed"]
    assert exposed["n"] == 0 and exposed["mae"] is None and exposed["observed_coverage"] is None
    for r in rows:
        r.update(features={}, state_features=[], publication_features=[])
    report = evaluate(rows, **CUTS)
    assert report["models"]["state_ridge"]["predictions"][0]["prediction"] == 4


def test_empty_train_calibration_test_are_explicit():
    empty = evaluate([], **CUTS)
    assert empty["status"] == "insufficient_support"
    assert empty["missing_splits"] == ["train", "calibration", "test"]
    assert empty["models"]["zero_change"]["metrics"]["all"]["mae"] is None
    no_train = evaluate([row(12), row(22)], **CUTS)
    assert no_train["models"]["state_ridge"]["status"] == "insufficient_training"
    assert no_train["models"]["state_ridge"]["predictions"] == []
    no_cal = evaluate([row(2), row(22)], **CUTS)
    assert no_cal["models"]["state_ridge"]["status"] == "insufficient_calibration"
    prediction = no_cal["models"]["state_ridge"]["predictions"][0]
    assert prediction["interval"] is None and prediction["covered"] is None
    no_test = evaluate([row(2), row(12)], **CUTS)
    assert no_test["missing_splits"] == ["test"]
    json.dumps([empty, no_train, no_cal, no_test], allow_nan=False)


def test_calibration_quantile_coverage_and_width():
    rows = [row(2, target=0), row(12, target=1), row(13, target=3), row(14, target=5),
            row(22, target=4), row(23, target=8)]
    report = evaluate(rows, coverage=.5, **CUTS)
    model = report["models"]["zero_change"]
    assert model["calibration"]["radius"] == 3
    assert model["metrics"]["all"]["observed_coverage"] == 0
    assert model["metrics"]["all"]["mean_width"] == 6
    assert model["metrics"]["all"]["rmse"] == pytest.approx(np.sqrt(40))


def test_daily_clusters_pairing_and_calendar_bootstrap():
    rows = sample() + [row(22, unit="second", p=2, target=1000), row(22, unit="third", p=1)]
    report = evaluate(rows, **CUTS)
    model = report["models"]["publication_ridge"]
    day = next(d for d in model["daily_metrics"] if d["target_day"] == "2026-01-22")
    assert day["metrics"]["all"]["n"] == 3
    pair = report["paired_comparisons"]["publication_ridge_minus_state_ridge"]["strata"]["all"]
    assert len(pair["daily"]) == 20
    assert pair["n"] == 22
    assert pair["daily_mean_mae_delta"] == pytest.approx(np.mean([d["mae_delta"] for d in pair["daily"]]))
    assert pair["mae_delta"] == pytest.approx(np.average([d["mae_delta"] for d in pair["daily"]], weights=[d["n"] for d in pair["daily"]]))
    assert pair["bootstrap"]["status"] == "ok"
    assert pair["bootstrap"]["n_valid_resamples"] == 200
    assert pair == evaluate(list(reversed(rows)), **CUTS)["paired_comparisons"]["publication_ridge_minus_state_ridge"]["strata"]["all"]


def test_timezone_normalization_groups_same_instant():
    rows = [row(2), row(12), row(22), row(22, unit="other")]
    rows[-1]["target_at"] = "2026-01-22T08:00:00+08:00"
    report = evaluate(rows, **CUTS)
    assert report["splits"]["test"]["n_target_days"] == 1


def test_builder_schema_with_requested_private_cutoffs(tmp_path):
    from flowmirror.analysis.temporal_dataset import build_dataset

    start = datetime(2026, 1, 1)
    nav = [{"fund_code": unit, "nav_date": (start + timedelta(days=d)).date().isoformat(),
            "nav": 1 + d * .001 + (d % 5) * .0002}
           for unit in ("000001", "000002") for d in range(248)]
    (tmp_path / "nav.jsonl").write_text("\n".join(json.dumps(r) for r in nav), encoding="utf-8")
    (tmp_path / "posts.jsonl").write_text(json.dumps({
        "post_id": "p", "market": "CN", "published_at": "2026-05-02T10:00:00+08:00",
        "fund_codes": ["000001"], "image_refs": ["local.jpg"]}), encoding="utf-8")
    dataset = build_dataset(tmp_path)
    report = evaluate(dataset["rows"], train_until="2026-03-31T23:59:59+08:00",
                      calibration_until="2026-04-30T23:59:59+08:00", end="2026-09-05T23:59:59+08:00")
    assert report["status"] == "ok"
    assert report["target_definition"] == dataset["settings"]["target"]
    assert report["splits"]["test"]["n_units"] == 2
    assert report["splits"]["test"]["n_publication_exposed"] > 0
    assert report["splits"]["purged"]["n_rows"] == 4
    assert report["models"]["publication_ridge"]["fit"]["feature_names"] == dataset["support"]["feature_names"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("field,value", [
    ("target", float("nan")), ("target", float("inf")), ("target", "1"), ("target", True),
    ("origin_at", "2026-01-01T00:00:00"), ("target_at", "2026-01-01T00:00:00Z"),
    ("state_features", ["state", "state"]), ("publication_features", ["state"]),
    ("support_post_ids", "post"), ("features", {"state": 2, "pub": float("nan")}),
    ("features", {"state": 2}), ("row_id", 1), ("unit_id", None),
])
def test_invalid_rows_raise_value_error(field, value):
    bad = row(2)
    bad[field] = value
    with pytest.raises(ValueError):
        evaluate([bad], **CUTS)


def test_duplicate_rows_and_inconsistent_schema():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate([row(2), row(2)], **CUTS)
    second = row(3)
    second.update(state_features=["pub"], publication_features=["state"])
    with pytest.raises(ValueError, match="identical"):
        evaluate([row(2), second], **CUTS)
    with pytest.raises(ValueError):
        evaluate(None, **CUTS)


@pytest.mark.parametrize("override", [
    {"train_until": "2026-01-10"}, {"calibration_until": "2026-01-09T00:00:00Z"},
    {"end": "2026-01-20T00:00:00Z"}, {"end": "2026-03-01"},
    {"alpha": -1}, {"alpha": float("nan")}, {"coverage": 1}, {"coverage": 0},
    {"seed": -1}, {"seed": 1.2},
])
def test_invalid_options(override):
    with pytest.raises(ValueError):
        evaluate([], **(CUTS | override))
