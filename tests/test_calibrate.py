import numpy as np

from matchlens.calibrate import fit as fit_mod
from matchlens.calibrate.fit import NORMS, SC_WEIGHTS, deciles, loss
from matchlens.calibrate.real import bootstrap_ci, split


def stats_with(lengths):
    """Simulated stats that hit every average target exactly; only the pass-length distribution varies."""
    return {k: 1.0 for k in SC_WEIGHTS} | {k: v for k, (v, _) in NORMS.items()} | {
        "pass_length_quantiles_m": deciles(lengths)}


def test_loss_penalises_a_wrong_shape_with_the_right_mean():
    rng = np.random.default_rng(0)
    real = rng.gamma(2.0, 8.0, 5000)  # mean 16 m, long right tail like real passing
    tgt = {k: 1.0 for k in SC_WEIGHTS} | {"pass_length_quantiles_m": deciles(real.tolist())}

    same = loss(stats_with(real.tolist()), tgt)
    flat = rng.uniform(0.0, 32.0, 5000)  # same mean, no tail
    squeezed = 16.0 + (real - 16.0) * 0.5  # same mean, half the spread
    three_pct_miss = 0.03**2  # what missing one average target by 3% costs
    assert same < 1e-9
    assert loss(stats_with(flat.tolist()), tgt) > three_pct_miss
    assert loss(stats_with(squeezed.tolist()), tgt) > three_pct_miss


def test_deciles_handles_too_few_values():
    assert deciles([]) == [0.0] * 9
    assert len(deciles([1.0, 2.0, 3.0])) == 9


# ---------------------------------------------------------------- data splits (#46) and report terms (#47)


def test_split_is_deterministic_disjoint_and_complete():
    ids = [f"m{i}" for i in range(20)]
    a, b = split(ids), split(list(reversed(ids)) + ids[:3])  # order and duplicates don't matter
    assert a == b
    assert [len(a[k]) for k in ("train", "validation", "test")] == [12, 4, 4]
    assert sorted(a["train"] + a["validation"] + a["test"]) == sorted(ids)


def test_bootstrap_interval_covers_the_mean_and_shrinks_with_more_data():
    rng = np.random.default_rng(1)
    few, many = rng.normal(10, 2, 8).tolist(), rng.normal(10, 2, 400).tolist()
    lo, hi = bootstrap_ci(few, lambda xs: float(np.mean(xs)), n=500)
    lo2, hi2 = bootstrap_ci(many, lambda xs: float(np.mean(xs)), n=500)
    assert lo < np.mean(few) < hi and lo2 < 10 < hi2
    assert hi2 - lo2 < hi - lo


def test_search_keeps_the_round_with_the_best_validation_loss(monkeypatch):
    """Train loss keeps falling, but validation is best after round 0: that round is returned."""
    monkeypatch.setattr(fit_mod, "KNOBS", {"x": (0.0, 10.0)})
    monkeypatch.setattr(fit_mod, "loss", lambda st, tgt: st["train"])
    evaluate = lambda p: {"train": abs(p["x"] - 10.0)}  # noqa: E731
    validate = lambda p: abs(p["x"] - 2.5)  # noqa: E731
    best, _, _ = fit_mod.coordinate_search({"x": 0.0}, evaluate, {}, rounds=3, validate=validate)
    assert best["x"] == 2.5
    last, _, _ = fit_mod.coordinate_search({"x": 0.0}, evaluate, {}, rounds=3)
    assert last["x"] > 2.5


def test_xg_calibration_bins():
    shots = [(0.02, False)] * 50 + [(0.03, True)] * 2 + [(0.5, True), (0.6, False)]
    bins = fit_mod.xg_calibration(shots)
    assert [b["shots"] for b in bins] == [52, 2]
    assert abs(bins[0]["goals"] - 2 / 52) < 1e-9


def test_test_section_replaces_itself(tmp_path, monkeypatch):
    report = tmp_path / "REALISM.md"
    report.write_text("# Report\n\n## Against SkillCorner\n\nold\n\n## Against typical match values\n\nnorms\n")
    monkeypatch.setattr(fit_mod, "REPORT", report)
    fit_mod.write_test_section([fit_mod.TEST_START, "first", fit_mod.TEST_END])
    fit_mod.write_test_section([fit_mod.TEST_START, "second", fit_mod.TEST_END])
    text = report.read_text()
    assert "second" in text and "first" not in text and text.count(fit_mod.TEST_START) == 1
    assert text.index("second") < text.index("## Against typical match values")
