import numpy as np

from matchlens.calibrate.fit import NORMS, SC_WEIGHTS, deciles, loss


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
