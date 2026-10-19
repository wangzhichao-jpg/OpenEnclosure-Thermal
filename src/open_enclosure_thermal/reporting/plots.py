"""Matplotlib figures (offline, Agg backend). Axis labels are English to avoid CJK font issues."""

from __future__ import annotations

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": 0.3, "font.size": 9})


def to_png_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def run_curves(analysis, rise: bool = True) -> str | None:
    c = analysis.curves
    fig, ax = plt.subplots(figsize=(8, 4))
    t = c["time_min"]
    plotted = 0
    for sid in analysis.sensors:
        col = sid if rise else f"{sid}_C"
        if col in c:
            ax.plot(t, c[col], lw=1.2, label=sid)
            plotted += 1
    if not rise and "ambient_C" in c:
        ax.plot(t, c["ambient_C"], "k--", lw=1, label="ambient")
    if not plotted:
        plt.close(fig)
        return None
    ax.set_xlabel("Time / min")
    ax.set_ylabel("Temperature rise / K" if rise else "Temperature / °C")
    ax.set_title(f"Run {analysis.run.id}: {'temperature rise' if rise else 'temperature'}")
    ax.legend(ncol=4, fontsize=7, loc="best")
    return to_png_b64(fig)


def fit_plot(fit) -> str | None:
    if fit.t_fit is None:
        return None
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7, 4.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    a1.plot(fit.t_fit, fit.y_obs, ".", ms=2.5, label="measured")
    a1.plot(fit.t_fit, fit.y_fit, "r-", lw=1.4, label="first-order model")
    rinf = fit.value("rise_inf_K")
    if rinf is not None:
        a1.axhline(rinf, color="r", ls=":", lw=1, label=f"rise_inf = {rinf:.2f} K")
    a1.set_ylabel("Rise / K")
    a1.set_title(f"{fit.run} / {fit.sensor}  tau = {fit.value('tau_min'):.1f} min  [{fit.status.value}]")
    a1.legend(fontsize=7)
    a2.plot(fit.t_fit, fit.y_obs - fit.y_fit, "k.", ms=2)
    a2.axhline(0, color="r", lw=0.8)
    a2.set_xlabel("Time / min")
    a2.set_ylabel("Residual / K")
    return to_png_b64(fig)


def comparison_plot(comp) -> str | None:
    d = comp.diff_curves
    cols = [c for c in d.columns if c != "time_min"]
    if not cols:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.5))
    for c in cols:
        ax.plot(d["time_min"], d[c], lw=1.2, label=c)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("Aligned time / min")
    ax.set_ylabel(f"Rise {comp.run_b} - {comp.run_a} / K")
    ax.legend(ncol=4, fontsize=7)
    return to_png_b64(fig)


def exponent_plot(exp) -> str | None:
    if exp.exponent_n is None:
        return None
    fig, ax = plt.subplots(figsize=(5, 3.5))
    I = np.array([p.current_A for p in exp.points])
    r = np.array([p.rise_K for p in exp.points])
    ax.loglog(I, r, "o", label="identified rise_inf")
    lo, hi = exp.current_range_A
    grid = np.linspace(lo, hi, 50)
    ax.loglog(grid, exp.rise_ref_K.value * (grid / exp.current_ref_A) ** exp.exponent_n.value, "r-",
              label=f"n = {exp.exponent_n.value:.2f} (fitted range only)")
    ax.set_xlabel("Current / A")
    ax.set_ylabel("Steady rise / K")
    ax.set_title(f"Sensor {exp.sensor}")
    ax.legend(fontsize=7)
    return to_png_b64(fig)


def fan_system_plot(model, result) -> str | None:
    """Fan curve(s) and system resistance at the solved zone temperature (forced ventilation)."""
    from ..ventilation.model import FanCurve, air_density, element_dp, G

    if model.ventilation.mode != "forced" or result.T_zone_C is None:
        return None
    fans = [FanCurve.from_fan(f) for f in model.fans if f.count > 0]
    amb = model.ambient
    rho_in = air_density(amb.temperature_C, amb.pressure_Pa, model.air.R_J_kgK)
    rho_z = air_density(result.T_zone_C, amb.pressure_Pa, model.air.R_J_kgK)
    rho_at = {"inlet": rho_in, "outlet": rho_z}
    vmax = min(fc.flow[-1] * fc.fan.count for fc in fans) * 1.1
    V = np.linspace(0, vmax, 120)
    sys_dp = []
    for v in V:
        m = v / 3600.0 * rho_in
        sys_dp.append(sum(element_dp(e, m, rho_at[e.location]) for e in model.elements))
    stack = G * model.ventilation.stack_height_m * (rho_in - rho_z) * (1 if model.ventilation.stack_assists else -1)
    fig, ax = plt.subplots(figsize=(6, 4))
    total = np.zeros_like(V)
    for fc in fans:
        q = fc.flow * fc.fan.count
        ax.plot(q, fc.pressure, "-o", ms=3, label=f"fan {fc.fan.id} x{fc.fan.count} (speed {fc.fan.speed_ratio:g})")
        total += np.interp(V, q, fc.pressure, right=np.nan)
    if len(fans) > 1:
        ax.plot(V, total, "b--", lw=1, label="fans in series")
    ax.plot(V, np.array(sys_dp) - stack, "k-", lw=1.4, label="system resistance - buoyancy")
    ax.plot([result.flow_inlet_m3h], [sum(p["static_pressure_Pa"] for p in result.fan_points.values())], "r*",
            ms=12, label="operating point")
    ax.set_xlabel("Flow (inlet conditions) / m³/h")
    ax.set_ylabel("Static pressure / Pa")
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=7)
    return to_png_b64(fig)


def scenario_bar(cases) -> str | None:
    ok = [c for c in cases if c.result.air_rise_K is not None]
    if not ok:
        return None
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(ok) + 1.2))
    names = [c.case_id for c in ok]
    vals = [c.result.air_rise_K for c in ok]
    ax.barh(names, vals, color="#4e79a7")
    for y, v in enumerate(vals):
        ax.text(v, y, f" {v:.1f}", va="center", fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Air node rise above outdoor / K")
    return to_png_b64(fig)


def sweep_plot(sweep_id: str, parameter: str, rows: list[dict]) -> str | None:
    pts = [(r["value"], r["air_rise_K"], r["flow_inlet_m3h"]) for r in rows if r["air_rise_K"] is not None]
    if not pts:
        return None
    x, y, q = map(np.array, zip(*pts))
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(x, y, "o-", color="#e15759", label="air rise")
    ax.set_xlabel(parameter)
    ax.set_ylabel("Air rise / K", color="#e15759")
    ax2 = ax.twinx()
    ax2.plot(x, q, "s--", color="#4e79a7", label="flow")
    ax2.set_ylabel("Flow / m³/h", color="#4e79a7")
    ax2.grid(False)
    ax.set_title(f"Sweep {sweep_id}")
    return to_png_b64(fig)
