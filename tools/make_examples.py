"""Regenerate committed examples/ and the README overview figure (synthetic data only)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from open_enclosure_thermal.io import load_project, save_project  # noqa: E402
from open_enclosure_thermal.synthetic import write_box_example, write_switchgear_example  # noqa: E402
from open_enclosure_thermal.ventilation.model import FanCurve, air_density, element_dp  # noqa: E402
from open_enclosure_thermal.workflows import analyze_test_project, ventilate_project  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ex = ROOT / "examples"
    test_yaml = write_switchgear_example(ex / "switchgear_test")
    box_yaml = write_box_example(ex / "box_ventilation")
    save_project(load_project(box_yaml), ex / "box_ventilation" / "box_ventilation.xlsx")

    rep = analyze_test_project(test_yaml, compare=[("R1", "R2")])
    vrep = ventilate_project(box_yaml)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    # (a) R1 rise curves + first-order fit of T01
    a = rep.run("R1")
    for sid in a.sensors:
        axes[0].plot(a.curves["time_min"], a.curves[sid], lw=1, label=sid)
    fit = next(f for f in rep.fits if f.run == "R1" and f.sensor == "T01")
    axes[0].plot(fit.t_fit, fit.y_fit, "k--", lw=1.2,
                 label=f"T01 fit: {fit.value('rise_inf_K'):.1f} K, tau {fit.value('tau_min'):.0f} min")
    axes[0].set(xlabel="Time / min", ylabel="Temperature rise / K", title="(a) Heat-run analysis (synthetic)")
    axes[0].legend(fontsize=7, ncol=2)
    # (b) fan curve vs system curve
    m, b = vrep.model, vrep.study.base.result
    fc = FanCurve.from_fan(m.fans[0])
    rho_in = air_density(m.ambient.temperature_C, m.ambient.pressure_Pa, 287.05)
    rho_z = air_density(b.T_zone_C, m.ambient.pressure_Pa, 287.05)
    V = np.linspace(0, 1250, 100)
    for lf, style in ((1, "-"), (3, "--")):
        sys = [sum(element_dp(e if e.kind != "filter" else e.model_copy(update={"loading_factor": lf}),
                              v / 3600 * rho_in, rho_in if e.location == "inlet" else rho_z) for e in m.elements)
               for v in V]
        axes[1].plot(V, sys, "k" + style, lw=1.2, label=f"system, filter loading x{lf}")
    axes[1].plot(fc.flow, fc.pressure, "o-", color="#4e79a7", label="fan F1")
    axes[1].plot(fc.flow * 2, fc.pressure, "s-", color="#59a14f", ms=3, label="2 fans in parallel")
    pt = b.fan_points["F1"]
    axes[1].plot([pt["flow_per_fan_m3h"]], [pt["static_pressure_Pa"]], "r*", ms=13, label="solved base point")
    axes[1].set(xlabel="Flow / m³/h", ylabel="Static pressure / Pa", title="(b) Fan - system operating point",
                ylim=(0, 200), xlim=(0, 2000))
    axes[1].legend(fontsize=7, loc="center right")
    # (c) scenarios
    cases = [c for c in vrep.study.cases if c.result.air_rise_K is not None]
    cases.sort(key=lambda c: c.result.air_rise_K)
    lim = m.study.air_rise_limit.value_K
    colors = ["#59a14f" if c.result.air_rise_K <= lim else "#e15759" for c in cases]
    axes[2].barh([c.case_id for c in cases], [c.result.air_rise_K for c in cases], color=colors)
    axes[2].axvline(lim, color="k", ls=":", label=f"user limit {lim:g} K (demo)")
    axes[2].invert_yaxis()
    axes[2].set(xlabel="Air node rise / K", title="(c) Scenario comparison")
    axes[2].legend(fontsize=7)
    fig.tight_layout()
    out = ROOT / "docs" / "images" / "overview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    print("written", out)


if __name__ == "__main__":
    main()
