"""Thermal response parameter identification.

M1: single-sensor first-order response under constant load
    rise(t) = r0 + (r_inf - r0) * (1 - exp(-(t - t0) / tau))
M2: steady rise vs current, rise_inf(I) = rise_r * (I / I_r) ** n, only from >= 2 current levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from ..schema import Message, ResultStatus, worst_status

MODEL_VERSION = "0.1.0"


def first_order(t: np.ndarray, r0: float, rinf: float, tau: float, t0: float = 0.0) -> np.ndarray:
    return r0 + (rinf - r0) * (1.0 - np.exp(-(np.asarray(t) - t0) / tau))


@dataclass
class Parameter:
    value: float | None
    std_error: float | None = None
    unit: str = ""

    def as_dict(self) -> dict:
        return {"value": self.value, "std_error": self.std_error, "unit": self.unit}


@dataclass
class FirstOrderFit:
    run: str
    sensor: str
    status: ResultStatus
    model_id: str = "first_order_response"
    model_version: str = MODEL_VERSION
    parameters: dict[str, Parameter] = field(default_factory=dict)
    rmse_K: float | None = None
    max_abs_residual_K: float | None = None
    correlation_rinf_tau: float | None = None
    residual_lag1_autocorr: float | None = None
    duration_over_tau: float | None = None
    fraction_reached: float | None = None
    n_points: int = 0
    window_min: tuple[float, float] | None = None
    validity: dict = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    t_fit: np.ndarray | None = None
    y_fit: np.ndarray | None = None
    y_obs: np.ndarray | None = None

    def value(self, name: str) -> float | None:
        p = self.parameters.get(name)
        return None if p is None else p.value

    def as_dict(self) -> dict:
        return {
            "run": self.run,
            "sensor": self.sensor,
            "status": self.status.value,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "parameters": {k: v.as_dict() for k, v in self.parameters.items()},
            "diagnostics": {
                "rmse_K": self.rmse_K,
                "max_abs_residual_K": self.max_abs_residual_K,
                "correlation_rinf_tau": self.correlation_rinf_tau,
                "residual_lag1_autocorr": self.residual_lag1_autocorr,
                "duration_over_tau": self.duration_over_tau,
                "fraction_reached": self.fraction_reached,
                "n_points": self.n_points,
                "warnings": [m.model_dump() for m in self.messages],
            },
            "window_min": self.window_min,
            "validity": self.validity,
        }


def fit_first_order(
    t_min: np.ndarray,
    rise_K: np.ndarray,
    *,
    run: str = "",
    sensor: str = "",
    start_min: float | None = None,
    end_min: float | None = None,
    fix_initial: bool = False,
    validity: dict | None = None,
) -> FirstOrderFit:
    t_all = np.asarray(t_min, dtype=float)
    y_all = np.asarray(rise_K, dtype=float)
    mask = ~(np.isnan(t_all) | np.isnan(y_all))
    if start_min is not None:
        mask &= t_all >= start_min
    if end_min is not None:
        mask &= t_all <= end_min
    t, y = t_all[mask], y_all[mask]
    res = FirstOrderFit(run=run, sensor=sensor, status=ResultStatus.success, n_points=int(t.size),
                        validity=dict(validity or {}))
    msgs = res.messages
    if t.size < 6:
        msgs.append(Message(code="too_few_points", text="拟合窗口内有效点少于6个", level="rejected"))
        res.status = ResultStatus.rejected
        return res
    t0 = float(t[0])
    duration = float(t[-1] - t0)
    res.window_min = (t0, float(t[-1]))
    k = max(3, t.size // 50)
    r0_guess = float(np.mean(y[:k])) if not fix_initial else float(y[0])
    rf_guess = float(np.mean(y[-k:]))
    span = rf_guess - r0_guess
    if abs(span) < 0.5:
        msgs.append(Message(code="no_response", text="窗口内温升变化小于0.5 K，无法识别时间常数", level="rejected"))
        res.status = ResultStatus.rejected
        return res
    target = r0_guess + 0.632 * span
    hit = np.where((y - target) * np.sign(span) >= 0)[0]
    tau_guess = float(t[hit[0]] - t0) if hit.size and t[hit[0]] > t0 else duration / 3.0
    tau_guess = max(tau_guess, duration / 50.0)
    dt_med = float(np.median(np.diff(t)))
    tau_lo, tau_hi = max(dt_med / 2.0, 1e-6), 20.0 * duration
    lo_y, hi_y = -500.0, 1000.0

    if fix_initial:
        r0_fixed = float(y[0])

        def model(p):
            return first_order(t, r0_fixed, p[0], p[1], t0)

        x0 = [rf_guess + 0.05 * span, min(max(tau_guess, tau_lo * 1.01), tau_hi * 0.99)]
        bounds = ([lo_y, tau_lo], [hi_y, tau_hi])
        names = ["rise_inf_K", "tau_min"]
    else:
        def model(p):
            return first_order(t, p[0], p[1], p[2], t0)

        x0 = [r0_guess, rf_guess + 0.05 * span, min(max(tau_guess, tau_lo * 1.01), tau_hi * 0.99)]
        bounds = ([lo_y, lo_y, tau_lo], [hi_y, hi_y, tau_hi])
        names = ["rise0_K", "rise_inf_K", "tau_min"]

    sol = least_squares(lambda p: model(p) - y, x0, bounds=bounds, method="trf", x_scale="jac", max_nfev=5000)
    if not sol.success:
        msgs.append(Message(code="fit_failed", text=f"最小二乘未收敛：{sol.message}", level="failed"))
        res.status = ResultStatus.failed
        return res
    p = sol.x
    resid = sol.fun
    n, m = t.size, p.size
    dof = max(n - m, 1)
    s2 = float(resid @ resid) / dof
    J = sol.jac
    cov = np.linalg.pinv(J.T @ J) * s2
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    i_inf, i_tau = names.index("rise_inf_K"), names.index("tau_min")
    denom = np.sqrt(cov[i_inf, i_inf] * cov[i_tau, i_tau])
    corr = float(cov[i_inf, i_tau] / denom) if denom > 0 else None

    for name, val, err in zip(names, p, se):
        unit = "min" if name == "tau_min" else "K"
        res.parameters[name] = Parameter(float(val), float(err), unit)
    if fix_initial:
        res.parameters["rise0_K"] = Parameter(float(y[0]), None, "K")
    tau = float(p[i_tau])
    res.parameters["tau_h"] = Parameter(tau / 60.0, float(se[i_tau]) / 60.0, "h")
    res.rmse_K = float(np.sqrt(np.mean(resid**2)))
    res.max_abs_residual_K = float(np.max(np.abs(resid)))
    res.correlation_rinf_tau = corr
    res.residual_lag1_autocorr = float(np.corrcoef(resid[:-1], resid[1:])[0, 1]) if n > 3 and np.std(resid) > 0 else None
    res.duration_over_tau = duration / tau
    res.fraction_reached = float(1.0 - np.exp(-duration / tau))
    res.t_fit, res.y_fit, res.y_obs = t, model(p), y
    res.validity.setdefault("time_min", [t0, float(t[-1])])

    status = ResultStatus.success
    at_bound = np.any(np.isclose(p, bounds[0], rtol=1e-6, atol=1e-9)) or np.any(
        np.isclose(p, bounds[1], rtol=1e-6, atol=1e-9))
    if at_bound:
        msgs.append(Message(code="parameter_at_bound", text="参数触碰搜索边界，数据不足以识别该模型", level="rejected"))
        status = ResultStatus.rejected
    if res.duration_over_tau < 1.0:
        msgs.append(Message(
            code="not_identifiable_truncated",
            text=f"窗口时长仅为时间常数的 {res.duration_over_tau:.2f} 倍（达到最终变化量的 {res.fraction_reached:.0%}），"
                 "稳态温升与时间常数不可分辨，结果不予采用",
            level="rejected"))
        status = ResultStatus.rejected
    elif res.duration_over_tau < 3.0:
        msgs.append(Message(
            code="extrapolated_steady_state",
            text=f"窗口时长为时间常数的 {res.duration_over_tau:.2f} 倍（约达 {res.fraction_reached:.0%}），"
                 "稳态温升为模型外推值，应延长试验或结合降温曲线复核"))
        status = worst_status(status, ResultStatus.warning)
    if corr is not None and abs(corr) > 0.95:
        msgs.append(Message(code="high_parameter_correlation",
                            text=f"稳态温升与时间常数估计强相关（r={corr:.2f}），二者不能独立解释"))
        status = worst_status(status, ResultStatus.warning)
    rinf, se_inf = float(p[i_inf]), float(se[i_inf])
    if rinf != 0 and se_inf / abs(rinf) > 0.10:
        msgs.append(Message(code="large_uncertainty_rinf", text=f"稳态温升标准误差达 {se_inf / abs(rinf):.0%}"))
        status = worst_status(status, ResultStatus.warning)
    if tau > 0 and float(se[i_tau]) / tau > 0.20:
        msgs.append(Message(code="large_uncertainty_tau", text=f"时间常数标准误差达 {float(se[i_tau]) / tau:.0%}"))
        status = worst_status(status, ResultStatus.warning)
    ac = res.residual_lag1_autocorr
    if ac is not None and ac > 0.8 and res.rmse_K > 0.2:
        msgs.append(Message(
            code="structured_residuals",
            text=f"残差呈系统性（滞后1自相关 {ac:.2f}），单一时间常数模型可能不足；标准误差可能被低估",
            level="warning"))
        status = worst_status(status, ResultStatus.warning)
    res.status = status
    return res


# ------------------------------------------------------------------ M2: current exponent
@dataclass
class CurrentPoint:
    current_A: float
    rise_K: float
    source: str = ""  # e.g. run id


@dataclass
class CurrentExponentFit:
    sensor: str
    status: ResultStatus
    configuration: str = "default"
    exponent_n: Parameter | None = None
    rise_ref_K: Parameter | None = None
    current_ref_A: float | None = None
    current_range_A: tuple[float, float] | None = None
    n_levels: int = 0
    points: list[CurrentPoint] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    def predict(self, current_A: float, *, allow_extrapolation: bool = False) -> float:
        if self.status in (ResultStatus.rejected, ResultStatus.failed) or self.exponent_n is None:
            raise ValueError(f"current exponent not identified for {self.sensor}: no prediction")
        lo, hi = self.current_range_A
        if not allow_extrapolation and not (lo * (1 - 1e-9) <= current_A <= hi * (1 + 1e-9)):
            raise ValueError(
                f"{current_A:g} A is outside the identified range [{lo:g}, {hi:g}] A; extrapolation refused")
        return self.rise_ref_K.value * (current_A / self.current_ref_A) ** self.exponent_n.value

    def as_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "configuration": self.configuration,
            "status": self.status.value,
            "exponent_n": None if self.exponent_n is None else self.exponent_n.as_dict(),
            "rise_ref_K": None if self.rise_ref_K is None else self.rise_ref_K.as_dict(),
            "current_ref_A": self.current_ref_A,
            "current_range_A": self.current_range_A,
            "n_levels": self.n_levels,
            "points": [p.__dict__ for p in self.points],
            "warnings": [m.model_dump() for m in self.messages],
        }


def fit_current_exponent(sensor: str, points: list[CurrentPoint], current_ref_A: float | None = None,
                         level_tolerance: float = 0.02) -> CurrentExponentFit:
    pts = [p for p in points if p.current_A > 0 and p.rise_K > 0]
    res = CurrentExponentFit(sensor=sensor, status=ResultStatus.success, points=pts)
    levels: list[float] = []
    for p in sorted(pts, key=lambda q: q.current_A):
        if not levels or p.current_A > levels[-1] * (1 + level_tolerance):
            levels.append(p.current_A)
    res.n_levels = len(levels)
    if len(levels) < 2:
        res.status = ResultStatus.rejected
        res.messages.append(Message(code="insufficient_current_levels",
                                    text="少于2个有效电流水平，电流—温升指数不足以识别", level="rejected"))
        return res
    current = np.array([p.current_A for p in pts])
    rise = np.array([p.rise_K for p in pts])
    i_ref = float(current_ref_A or current.max())
    X = np.column_stack([np.ones_like(current), np.log(current / i_ref)])
    Y = np.log(rise)
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    a, n = float(coef[0]), float(coef[1])
    se_n = se_a = None
    if len(pts) > 2:
        r = Y - X @ coef
        s2 = float(r @ r) / (len(pts) - 2)
        cov = np.linalg.pinv(X.T @ X) * s2
        se_a, se_n = float(np.sqrt(cov[0, 0])), float(np.sqrt(cov[1, 1]))
    res.exponent_n = Parameter(n, se_n, "-")
    res.rise_ref_K = Parameter(float(np.exp(a)), None if se_a is None else float(np.exp(a) * se_a), "K")
    res.current_ref_A = i_ref
    res.current_range_A = (float(current.min()), float(current.max()))
    if len(levels) == 2:
        res.status = ResultStatus.warning
        res.messages.append(Message(code="two_levels_only",
                                    text="仅2个电流水平：指数为两点精确解，无法估计不确定度，建议补充第3个电流水平"))
    if not 1.0 <= n <= 2.5:
        res.status = worst_status(res.status, ResultStatus.warning)
        res.messages.append(Message(code="exponent_unusual",
                                    text=f"识别指数 n={n:.2f} 超出常见范围 1.0~2.5，请检查环境修正、稳定性和测点"))
    return res
