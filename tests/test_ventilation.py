import math

import pytest
import yaml

from open_enclosure_thermal.io import model_from_mapping
from open_enclosure_thermal.schema import ResultStatus
from open_enclosure_thermal.synthetic import BOX_EXAMPLE
from open_enclosure_thermal.ventilation import air_density, solve
from open_enclosure_thermal.ventilation.model import G


def box(**kw):
    base = {
        "project": {"name": "t", "source_type": "synthetic"},
        "internal_heat_W": 1000.0,
        "ambient": {"temperature_C": 30.0},
        "surfaces": [{"id": "S", "area_m2": 10.0, "U_W_m2K": 5.0}],
        "elements": [],
        "fans": [],
        "ventilation": {"mode": "closed"},
    }
    base.update(kw)
    return model_from_mapping(base)


def test_closed_box_analytic():
    r = solve(box())
    assert r.status == ResultStatus.success
    assert r.air_rise_K == pytest.approx(1000 / 50.0, rel=1e-9)
    assert r.heat_balance_W["residual"] == pytest.approx(0, abs=1e-6)


def test_closed_box_with_sun_sol_air():
    m = box(surfaces=[{"id": "S", "area_m2": 10.0, "U_W_m2K": 5.0, "solar_irradiance_W_m2": 600,
                       "absorptance": 0.5, "h_out_W_m2K": 15}])
    r = solve(m)
    assert r.air_rise_K == pytest.approx(1000 / 50 + 0.5 * 600 / 15, rel=1e-9)


def test_closed_without_heat_path_rejected():
    assert solve(box(surfaces=[])).status == ResultStatus.rejected


def test_fixed_flow_analytic():
    m = box(ventilation={"mode": "fixed_flow", "fixed_flow_m3h": 500.0})
    r = solve(m)
    mdot = air_density(30.0, 101325, 287.05) * 500 / 3600
    assert r.air_rise_K == pytest.approx(1000 / (mdot * 1006 + 50), rel=1e-9)


def test_fan_resistance_intersection_analytic():
    # linear fan p = 200 (1 - V/1000) [V m3/h], inlet opening only, no buoyancy -> quadratic in V
    K, A = 4.0, 0.05
    m = box(surfaces=[], internal_heat_W=500.0,
            elements=[{"id": "IN", "kind": "opening", "location": "inlet", "area_m2": A, "K": K}],
            fans=[{"id": "F", "location": "inlet", "curve": [{"flow_m3h": 0, "static_pressure_Pa": 200},
                                                              {"flow_m3h": 1000, "static_pressure_Pa": 0}]}],
            ventilation={"mode": "forced", "stack_height_m": 0.0})
    r = solve(m)
    rho = air_density(30.0, 101325, 287.05)
    # 200 - 0.2 V = K rho /2 (V/3600/A)^2
    a = K * rho / 2 / (3600 * A) ** 2
    V = (-0.2 + math.sqrt(0.04 + 4 * a * 200)) / (2 * a)
    assert r.flow_inlet_m3h == pytest.approx(V, rel=1e-6)
    assert r.fan_points["F"]["static_pressure_Pa"] == pytest.approx(200 - 0.2 * V, rel=1e-6)
    assert r.air_rise_K == pytest.approx(500 / (rho * V / 3600 * 1006), rel=1e-6)


def test_filter_loading_monotonic():
    base = model_from_mapping(BOX_EXAMPLE)
    prev_flow, prev_rise = float("inf"), -1.0
    for lf in (1, 2, 4, 8):
        data = base.model_dump(mode="json")
        data["elements"][1]["loading_factor"] = lf
        r = solve(model_from_mapping(data))
        assert r.ok()
        assert r.flow_inlet_m3h < prev_flow and r.air_rise_K > prev_rise
        prev_flow, prev_rise = r.flow_inlet_m3h, r.air_rise_K


def test_energy_and_pressure_balance_example():
    m = model_from_mapping(BOX_EXAMPLE)
    r = solve(m)
    assert abs(r.heat_balance_W["residual"]) < 1e-6
    fan_p = r.fan_points["F1"]["static_pressure_Pa"]
    assert fan_p + r.dp_stack_Pa == pytest.approx(sum(r.element_dp_Pa.values()), rel=1e-6)


def test_natural_ventilation_balance_and_height():
    def nat(H):
        return box(surfaces=[], elements=[
            {"id": "IN", "kind": "opening", "location": "inlet", "area_m2": 0.05, "Cd": 0.6},
            {"id": "OUT", "kind": "opening", "location": "outlet", "area_m2": 0.05, "Cd": 0.6}],
            ventilation={"mode": "natural", "stack_height_m": H})

    r1, r2 = solve(nat(1.0)), solve(nat(2.0))
    assert r1.ok() and r2.ok()
    assert r2.flow_inlet_m3h > r1.flow_inlet_m3h and r2.air_rise_K < r1.air_rise_K
    rho_in = air_density(30.0, 101325, 287.05)
    rho_z = air_density(r1.T_zone_C, 101325, 287.05)
    assert r1.dp_stack_Pa == pytest.approx(G * 1.0 * (rho_in - rho_z), rel=1e-9)
    assert r1.dp_stack_Pa == pytest.approx(sum(r1.element_dp_Pa.values()), rel=1e-6)
    assert r1.mass_flow_kg_s * 1006 * r1.air_rise_K == pytest.approx(1000, rel=1e-6)


def test_fan_curve_extrapolation_refused_then_allowed():
    # fan curve ends at 1200 m3/h / 20 Pa; this resistance meets the extended curve at ~1250 m3/h
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    data["elements"] = [{"id": "IN", "kind": "opening", "location": "inlet", "area_m2": 0.1, "K": 1.5}]
    r = solve(model_from_mapping(data))
    assert r.status == ResultStatus.rejected
    assert {m.code for m in r.messages} & {"no_operating_point_high", "fan_curve_high_end"}
    data["fans"][0]["allow_extrapolation"] = True
    r2 = solve(model_from_mapping(data))
    assert r2.ok() and r2.status == ResultStatus.warning
    assert "fan_curve_extrapolated" in {m.code for m in r2.messages}
    assert 1200 < r2.flow_outlet_m3h < 1300


def test_fan_cannot_overcome_resistance():
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    data["fans"][0]["curve"] = [{"flow_m3h": 0, "static_pressure_Pa": 5}, {"flow_m3h": 100, "static_pressure_Pa": 4}]
    data["ventilation"]["stack_assists"] = False
    data["ventilation"]["stack_height_m"] = 20.0
    r = solve(model_from_mapping(data))
    assert r.status == ResultStatus.rejected


def test_multiple_fan_groups_same_location_rejected():
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    f2 = dict(data["fans"][0], id="F2")
    data["fans"].append(f2)
    r = solve(model_from_mapping(data))
    assert r.status == ResultStatus.rejected


def test_speed_ratio_affinity():
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    data["fans"][0]["speed_ratio"] = 1.0
    r1 = solve(model_from_mapping(data))
    data["fans"][0]["speed_ratio"] = 0.8
    data["fans"][0]["power_W"] = 60
    r2 = solve(model_from_mapping(data))
    assert r2.flow_inlet_m3h < r1.flow_inlet_m3h
    assert r2.fan_power_W == pytest.approx(60 * 0.8**3)


def test_invalid_fan_curve_rejected_by_schema():
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    data["fans"][0]["curve"][1]["static_pressure_Pa"] = 500
    with pytest.raises(Exception, match="non-increasing"):
        model_from_mapping(data)
