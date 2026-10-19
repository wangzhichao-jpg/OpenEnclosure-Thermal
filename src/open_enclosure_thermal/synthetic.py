"""Deterministic synthetic example data. Nothing here represents any manufacturer's product."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .identification import first_order

#: sensor id -> (name, category, rise_inf at 1250 A [K], tau [min], fast-component fraction, fast tau [min])
SWITCHGEAR_SENSORS = {
    "T01": ("母排连接处", "conductor", 52.0, 95.0, 0.0, 0.0),
    "T02": ("断路器触头", "contact", 58.0, 80.0, 0.12, 12.0),
    "T03": ("电缆端子", "terminal", 44.0, 110.0, 0.0, 0.0),
    "T04": ("柜内上部空气", "air", 22.0, 60.0, 0.0, 0.0),
    "T05": ("柜顶外壳", "wall", 11.0, 70.0, 0.0, 0.0),
}
RATED_CURRENT_A = 1250.0
EXPONENT_N = 1.8


def synthetic_rise(t_min: np.ndarray, rise_inf: float, tau: float, fast_frac: float = 0.0,
                   fast_tau: float = 0.0) -> np.ndarray:
    slow = first_order(t_min, 0.0, rise_inf * (1 - fast_frac), tau)
    fast = first_order(t_min, 0.0, rise_inf * fast_frac, fast_tau) if fast_frac > 0 else 0.0
    return slow + fast


def switchgear_run(current_A: float, *, hours: float = 8.0, step_min: float = 1.0, improvement: float = 0.0,
                   seed: int = 0, noise_K: float = 0.15, ambient_C: float = 24.0) -> pd.DataFrame:
    """One constant-current heat run. ``improvement`` reduces internal rises (e.g. better ventilation)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, hours * 60.0 + 1e-9, step_min)
    amb = ambient_C + 1.0 * t / t[-1] + 0.3 * np.sin(2 * np.pi * t / 240.0)
    data = {"time_min": t,
            "TA1": amb + rng.normal(0, 0.08, t.size),
            "TA2": amb + 0.2 + rng.normal(0, 0.08, t.size)}
    scale = (current_A / RATED_CURRENT_A) ** EXPONENT_N
    for sid, (_, cat, rinf, tau, ff, ft) in SWITCHGEAR_SENSORS.items():
        factor = 1.0 - improvement * (1.0 if cat in ("air", "contact", "conductor") else 0.6)
        rise = synthetic_rise(t, rinf * scale * factor, tau, ff, ft)
        data[sid] = amb + rise + rng.normal(0, noise_K, t.size)
    return pd.DataFrame(data).round(2)


def write_switchgear_example(outdir: str | Path) -> Path:
    """Write the synthetic switchgear heat-run example (4 runs, 3 file formats) and its project YAML."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    # R1: 1250 A, logger-style export with date-time and Chinese headers, one spike and one gap.
    r1 = switchgear_run(1250, seed=1)
    r1.loc[200, "T02"] += 15.0
    r1.loc[150, "T03"] = np.nan
    start = pd.Timestamp("2026-06-01 08:00:00")
    raw_names = {sid: f"CH{i + 1:02d} {SWITCHGEAR_SENSORS[sid][0]}" for i, sid in enumerate(SWITCHGEAR_SENSORS)}
    raw_names.update({"TA1": "CH11 环境1", "TA2": "CH12 环境2"})
    r1_out = pd.DataFrame({"记录时间": (start + pd.to_timedelta(r1["time_min"], unit="m")).dt.strftime("%Y-%m-%d %H:%M:%S")})
    for sid, name in raw_names.items():
        r1_out[name] = r1[sid]
    r1_out.to_csv(out / "R1_1250A_logger.csv", index=False, encoding="utf-8-sig")
    # R2: same current, improved ventilation, minutes and sensor ids as headers.
    switchgear_run(1250, seed=2, improvement=0.12, ambient_C=25.0).to_csv(out / "R2_1250A_improved.csv", index=False)
    # R3/R4: lower currents for the current exponent, seconds.
    for rid, cur, seed in (("R3", 1000, 3), ("R4", 800, 4)):
        df = switchgear_run(cur, seed=seed)
        df.insert(0, "time_s", (df.pop("time_min") * 60).round(0))
        df.to_csv(out / f"{rid}_{cur}A.csv", index=False)

    sensors = [{"id": sid, "name": v[0], "category": v[1]} for sid, v in SWITCHGEAR_SENSORS.items()]
    sensors += [{"id": "TA1", "name": "环境温度1", "category": "ambient"},
                {"id": "TA2", "name": "环境温度2", "category": "ambient"}]
    project = {
        "schema_version": "0.1",
        "kind": "test_analysis",
        "project": {"name": "synthetic-switchgear-heat-run", "source_type": "synthetic", "data_access": "public",
                    "description": "合成算例：一台虚构开关柜的温升试验，不代表任何企业产品"},
        "sensors": sensors,
        "test_runs": [
            {"id": "R1", "file": "R1_1250A_logger.csv", "time": {"column": "记录时间", "kind": "datetime"},
             "columns": {sid: name for sid, name in raw_names.items()}, "ambient": ["TA1", "TA2"],
             "load": {"current_A": 1250}, "description": "额定电流，原始方案（记录仪导出格式）",
             "configuration": "baseline"},
            {"id": "R2", "file": "R2_1250A_improved.csv", "time": {"column": "time_min", "kind": "minutes"},
             "ambient": ["TA1", "TA2"], "load": {"current_A": 1250}, "description": "额定电流，改进通风后",
             "configuration": "improved"},
            {"id": "R3", "file": "R3_1000A.csv", "time": {"column": "time_s", "kind": "seconds"},
             "ambient": ["TA1", "TA2"], "load": {"current_A": 1000}, "description": "降低电流", "configuration": "baseline"},
            {"id": "R4", "file": "R4_800A.csv", "time": {"column": "time_s", "kind": "seconds"},
             "ambient": ["TA1", "TA2"], "load": {"current_A": 800}, "description": "进一步降低电流", "configuration": "baseline"},
        ],
        "stability": {"window_min": 60, "max_change_K": 1.0, "sensors": "all",
                      "source": "合成演示判据：60 min 内温升变化不超过 1 K（用户应按适用标准自行设定）"},
        "quality": {"spike_K": 5.0, "exclude_spikes": False},
        "limits": [{"id": "L1", "quantity": "temperature_rise", "sensor": "T02", "value": 65,
                    "source": "合成演示值，不代表标准限值"},
                   {"id": "L2", "quantity": "temperature_rise", "sensor": "T01", "value": 50,
                    "source": "合成演示值，不代表标准限值"}],
        "final_window_min": 10,
        "fits": "auto",
    }
    path = out / "switchgear_test.yaml"
    path.write_text(yaml.safe_dump(project, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


BOX_EXAMPLE = {
    "schema_version": "0.1",
    "kind": "box_ventilation",
    "project": {"name": "synthetic-outdoor-lv-enclosure", "source_type": "synthetic", "data_access": "public",
                "description": "合成算例：虚构户外低压柜体，参数仅用于演示"},
    "description": "2.0 m × 1.2 m × 2.2 m 户外柜，下部进风（百叶+过滤网），上部排风扇",
    "internal_heat_W": 1500,
    "ambient": {"temperature_C": 35.0, "pressure_Pa": 101325},
    "surfaces": [
        {"id": "roof", "area_m2": 2.4, "U_W_m2K": 5.5, "solar_irradiance_W_m2": 800, "absorptance": 0.6},
        {"id": "front", "area_m2": 4.4, "U_W_m2K": 5.5, "solar_irradiance_W_m2": 400, "absorptance": 0.6},
        {"id": "back", "area_m2": 4.4, "U_W_m2K": 5.5},
        {"id": "sides", "area_m2": 5.28, "U_W_m2K": 5.5, "solar_irradiance_W_m2": 150, "absorptance": 0.6},
    ],
    "elements": [
        {"id": "IN", "kind": "opening", "location": "inlet", "area_m2": 0.12, "K": 6.0},
        {"id": "FL1", "kind": "filter", "location": "inlet", "rated_flow_m3h": 1000, "rated_dp_Pa": 40,
         "exponent": 1.4, "loading_factor": 1.0},
        {"id": "OUT", "kind": "opening", "location": "outlet", "area_m2": 0.12, "K": 6.0},
    ],
    "fans": [
        {"id": "F1", "location": "outlet", "count": 1, "power_W": 60, "heat_to_zone": False,
         "curve": [{"flow_m3h": 0, "static_pressure_Pa": 180}, {"flow_m3h": 300, "static_pressure_Pa": 160},
                   {"flow_m3h": 600, "static_pressure_Pa": 125}, {"flow_m3h": 900, "static_pressure_Pa": 80},
                   {"flow_m3h": 1200, "static_pressure_Pa": 20}]},
    ],
    "ventilation": {"mode": "forced", "stack_height_m": 1.6, "stack_assists": True},
    "study": {
        "rank_by": "margin",
        "air_rise_limit": {"value_K": 15, "source": "合成演示值，不代表标准限值"},
        "scenarios": [
            {"id": "S1_natural", "description": "风机停运，仅自然通风", "set": {"ventilation.mode": "natural", "fans[F1].count": 0}},
            {"id": "S2_dusty", "description": "过滤网积尘，阻力为洁净时3倍", "set": {"elements[FL1].loading_factor": 3}},
            {"id": "S3_two_fans", "description": "两台同型风机并联", "set": {"fans[F1].count": 2}},
            {"id": "S4_bigger_openings", "description": "进出风口净面积 +50%",
             "set": {"elements[IN].area_m2": 0.18, "elements[OUT].area_m2": 0.18}},
            {"id": "S5_slow_fan", "description": "风机降速至70%", "set": {"fans[F1].speed_ratio": 0.7}},
            {"id": "S6_closed", "description": "封闭箱体，仅壁面散热", "set": {"ventilation.mode": "closed"}},
            {"id": "S7_blocked", "description": "过滤网严重堵塞（20倍）", "set": {"elements[FL1].loading_factor": 20}},
        ],
        "sweeps": [
            {"id": "filter_loading", "parameter": "elements[FL1].loading_factor", "values": [1, 1.5, 2, 3, 4, 5]},
            {"id": "heat_load", "parameter": "internal_heat_W", "values": [500, 1000, 1500, 2000, 2500, 3000]},
        ],
    },
}


def write_box_example(outdir: str | Path) -> Path:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "box_ventilation.yaml"
    path.write_text(yaml.safe_dump(BOX_EXAMPLE, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
