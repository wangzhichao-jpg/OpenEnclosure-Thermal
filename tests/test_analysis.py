import numpy as np
import pandas as pd
import pytest

from open_enclosure_thermal.schema import StabilityCriterion
from open_enclosure_thermal.test_analysis import evaluate_stability
from open_enclosure_thermal.workflows import analyze_test_project


def heat_run(hours=8.0, rinf=40.0, tau=60.0, amb=20.0, step=1.0):
    t = np.arange(0, hours * 60 + 1e-9, step)
    return pd.DataFrame({"time_min": t, "TA": amb + 0 * t, "T1": amb + rinf * (1 - np.exp(-t / tau))})


def test_rise_statistics_exact(simple_project):
    p = simple_project(heat_run())
    rep = analyze_test_project(p)
    s = rep.runs[0].stats[0]
    assert s.sensor == "T1"
    assert s.final_rise_K == pytest.approx(40 * (1 - np.exp(-8)), abs=0.02)
    assert s.t63_min == pytest.approx(60.0 * -np.log(1 - 0.632) / 1.0, rel=0.01)
    assert s.max_rise_K == pytest.approx(40, abs=0.02)
    assert s.mean_rate_K_per_h == pytest.approx(s.final_rise_K / 8, rel=1e-3)


def test_stable_and_unstable_classification():
    t = np.arange(0, 481.0)
    stable = 40 * (1 - np.exp(-t / 60))
    rising = 0.05 * t  # 3 K/h, never stable
    crit = StabilityCriterion(window_min=60, max_change_K=1.0)
    assert evaluate_stability(t, stable, crit, "a").stable_at_end
    r = evaluate_stability(t, rising, crit, "b")
    assert r.evaluable and not r.stable_at_end and r.stable_since_min is None
    since = evaluate_stability(t, stable, crit, "a").stable_since_min
    # 60 min window change <= 1 K: 40*exp(-(t-60)/60)*(1-exp(-1)) <= 1
    expected = 60 + 60 * np.log(40 * (1 - np.exp(-1)))
    assert since == pytest.approx(expected, abs=1.5)


def test_criterion_changes_result_and_is_reported(simple_project):
    df = heat_run(hours=4)
    strict = simple_project(df, stability={"window_min": 60, "max_change_K": 0.05, "source": "strict demo"})
    rep = analyze_test_project(strict)
    assert rep.runs[0].run_stable is False
    loose = simple_project(df, stability={"window_min": 60, "max_change_K": 2.0, "source": "loose demo"})
    assert analyze_test_project(loose).runs[0].run_stable is True
    # mean slope over the last hour is 40*(e^-3 - e^-4) ~= 1.26 K/h
    rate = simple_project(df, stability={"window_min": 60, "max_change_K": None, "max_rate_K_per_h": 1.5})
    assert analyze_test_project(rate).runs[0].run_stable is True
    rate = simple_project(df, stability={"window_min": 60, "max_change_K": None, "max_rate_K_per_h": 1.0})
    assert analyze_test_project(rate).runs[0].run_stable is False


def test_short_run_not_evaluable(simple_project):
    rep = analyze_test_project(simple_project(heat_run(hours=0.5), stability={"window_min": 60, "max_change_K": 1}))
    a = rep.runs[0]
    assert a.run_stable is False
    assert any(m.code == "stability_not_evaluable" for m in a.messages)


def test_limits_statement_never_says_pass(simple_project):
    p = simple_project(heat_run(), limits=[{"id": "L", "quantity": "temperature_rise", "sensor": "T1", "value": 35}])
    rep = analyze_test_project(p)
    lc = rep.runs[0].limits[0]
    assert lc.margin_final < 0 and "超过用户设定限值" in lc.statement
    assert "合格" not in lc.statement
    assert lc.source.startswith("用户临时设定")


def test_comparison_with_alignment(simple_project, tmp_path):
    a = heat_run(rinf=40)
    # second run: logging started 15 min before the load was switched on
    t = np.arange(0, 496.0)
    b = pd.DataFrame({"time_min": t, "TA": 20.0 + 0 * t,
                      "T1": 20 + np.where(t < 15, 0.0, 35 * (1 - np.exp(-(t - 15) / 60)))})
    b.to_csv(tmp_path / "R2.csv", index=False)
    p = simple_project(a, extra_runs=[{"id": "R2", "file": "R2.csv", "ambient": ["TA"], "align_offset_min": 15,
                                                    "time": {"column": "time_min", "kind": "minutes"}}])
    rep = analyze_test_project(p, compare=[("R1", "R2")])
    c = rep.comparisons[0]
    s = c.sensors[0]
    assert s.delta_final_K == pytest.approx(-5 * (1 - np.exp(-8)), abs=0.05)
    assert c.common_range_min[0] == pytest.approx(0) and c.common_range_min[1] == pytest.approx(480)
    assert s.max_abs_curve_diff_K == pytest.approx(-5, abs=0.05)


def test_no_ambient_no_rise(simple_project):
    rep = analyze_test_project(simple_project(heat_run(), ambient=()))
    a = rep.runs[0]
    assert not a.has_rise
    assert a.stats and all(s.max_rise_K is None for s in a.stats)


def test_example_end_to_end(examples):
    rep = analyze_test_project(examples["test"], compare=[("R1", "R2")])
    assert {a.run.id for a in rep.runs} == {"R1", "R2", "R3", "R4"}
    r1 = rep.run("R1")
    assert any(m.code == "spike_flagged" for m in r1.quality.messages)
    t02 = next(s for s in r1.stats if s.sensor == "T02")
    assert t02.max_is_flagged
    # exponent only pools baseline configuration runs (R1, R3, R4)
    for e in rep.exponents:
        assert e.configuration == "baseline"
        assert {p.source for p in e.points} == {"R1", "R3", "R4"}
        assert e.exponent_n.value == pytest.approx(1.8, abs=0.06)
    c = rep.comparisons[0]
    assert all(s.delta_final_K < 0 for s in c.sensors)
