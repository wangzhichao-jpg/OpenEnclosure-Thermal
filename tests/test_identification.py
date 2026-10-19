import numpy as np
import pytest

from open_enclosure_thermal.identification import CurrentPoint, first_order, fit_current_exponent, fit_first_order
from open_enclosure_thermal.schema import ResultStatus


@pytest.mark.parametrize("seed", range(5))
def test_parameter_recovery_with_noise(seed):
    rng = np.random.default_rng(seed)
    t = np.arange(0, 480.0, 1.0)
    y = first_order(t, 0.0, 45.0, 80.0) + rng.normal(0, 0.2, t.size)
    fit = fit_first_order(t, y)
    assert fit.status == ResultStatus.success, [m.text for m in fit.messages]
    assert fit.value("rise_inf_K") == pytest.approx(45.0, rel=0.01)
    assert fit.value("tau_min") == pytest.approx(80.0, rel=0.03)
    se = fit.parameters["rise_inf_K"].std_error
    assert 0 < se < 0.2
    assert fit.rmse_K == pytest.approx(0.2, rel=0.15)


def test_fix_initial_and_window():
    t = np.arange(0, 600.0, 2.0)
    y = np.where(t < 100, 5.0, first_order(t, 5.0, 30.0, 50.0, 100.0))
    fit = fit_first_order(t, y, start_min=100, fix_initial=True)
    assert fit.value("rise_inf_K") == pytest.approx(30.0, rel=1e-4)
    assert fit.value("tau_min") == pytest.approx(50.0, rel=1e-4)
    assert fit.window_min[0] == 100


def test_cooling_curve():
    t = np.arange(0, 400.0, 1.0)
    fit = fit_first_order(t, first_order(t, 40.0, 0.0, 70.0))
    assert fit.value("tau_min") == pytest.approx(70.0, rel=1e-4)


def test_truncated_curve_rejected():
    rng = np.random.default_rng(1)
    t = np.arange(0, 40.0, 1.0)  # 0.5 tau
    fit = fit_first_order(t, first_order(t, 0, 45.0, 80.0) + rng.normal(0, 0.2, t.size))
    assert fit.status == ResultStatus.rejected
    codes = {m.code for m in fit.messages}
    assert codes & {"not_identifiable_truncated", "parameter_at_bound"}


def test_partially_developed_curve_warns():
    rng = np.random.default_rng(2)
    t = np.arange(0, 160.0, 1.0)  # 2 tau
    fit = fit_first_order(t, first_order(t, 0, 45.0, 80.0) + rng.normal(0, 0.1, t.size))
    assert fit.status == ResultStatus.warning
    assert "extrapolated_steady_state" in {m.code for m in fit.messages}


def test_two_time_constants_flag_structured_residuals():
    t = np.arange(0, 600.0, 1.0)
    y = first_order(t, 0, 30.0, 120.0) + first_order(t, 0, 15.0, 8.0)
    fit = fit_first_order(t, y)
    assert "structured_residuals" in {m.code for m in fit.messages}


def test_flat_signal_rejected():
    t = np.arange(0, 100.0)
    assert fit_first_order(t, np.full(t.size, 3.0)).status == ResultStatus.rejected


def test_exponent_single_level_rejected():
    e = fit_current_exponent("T", [CurrentPoint(1000, 40), CurrentPoint(1005, 41)])
    assert e.status == ResultStatus.rejected and e.exponent_n is None
    with pytest.raises(ValueError):
        e.predict(1000)


def test_exponent_two_levels_warns_three_levels_recovers():
    n = 1.75
    pts = [CurrentPoint(i, 50 * (i / 1250) ** n) for i in (800, 1250)]
    e2 = fit_current_exponent("T", pts)
    assert e2.status == ResultStatus.warning and e2.exponent_n.value == pytest.approx(n)
    pts.append(CurrentPoint(1000, 50 * 0.8**n * 1.002))
    e3 = fit_current_exponent("T", pts)
    assert e3.status == ResultStatus.success
    assert e3.exponent_n.value == pytest.approx(n, abs=0.02)
    assert e3.exponent_n.std_error is not None


def test_exponent_prediction_refuses_extrapolation():
    pts = [CurrentPoint(i, 50 * (i / 1250) ** 1.8) for i in (800, 1000, 1250)]
    e = fit_current_exponent("T", pts)
    assert e.predict(900) == pytest.approx(50 * (900 / 1250) ** 1.8, rel=1e-6)
    with pytest.raises(ValueError, match="extrapolation refused"):
        e.predict(1600)
    assert e.predict(1600, allow_extrapolation=True) > 50


def test_cross_check_with_switchgear_thermal_model():
    """Our first-order fit recovers the parameters used by the upstream MPL-2.0 model (optional)."""
    pytest.importorskip("switchgear_thermal_model")
    import pandas as pd

    from open_enclosure_thermal.adapters.switchgear_thermal_model import run_upstream, switchgear_parameters

    idx = pd.date_range("2026-01-01", periods=601, freq="1min").to_pydatetime()
    amb = np.full(601, 25.0)
    params = {"rated_current": 1250.0, "measured_temperature_rise": 48.0, "temp_rise_exp": 1.7,
              "thermal_time_constant": 85.0}
    fits = {}
    for current in (800.0, 1000.0, 1250.0):
        temps = run_upstream(params, idx, np.full(601, current), amb).to_numpy()
        t = np.arange(601.0)
        f = fit_first_order(t, temps - amb, run=f"I{current:g}", sensor="T", fix_initial=True,
                            validity={"current_A": [current, current]})
        assert f.value("tau_min") == pytest.approx(85.0, rel=1e-3)
        fits[current] = f
    e = fit_current_exponent("T", [CurrentPoint(c, f.value("rise_inf_K")) for c, f in fits.items()])
    assert e.exponent_n.value == pytest.approx(1.7, abs=1e-3)
    back = switchgear_parameters(fits[1250.0], e, 1250.0)
    assert back["measured_temperature_rise"] == pytest.approx(48.0, rel=1e-3)
    assert back["thermal_time_constant"] == pytest.approx(85.0, rel=1e-3)
