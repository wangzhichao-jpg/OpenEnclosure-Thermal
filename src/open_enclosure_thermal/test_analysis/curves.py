"""Temperature-rise curve analysis (model M0: data algebra only, no physical model)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..quality import QualityReport
from ..schema import Limit, Message, ResultStatus, StabilityCriterion, TestProject, TestRun, worst_status


@dataclass
class SensorStats:
    sensor: str
    name: str
    max_temperature_C: float
    max_rise_K: float | None
    time_of_max_min: float
    initial_rise_K: float | None
    final_rise_K: float | None
    final_temperature_C: float
    mean_rate_K_per_h: float | None
    max_rate_K_per_h: float | None
    t50_min: float | None
    t63_min: float | None
    t90_min: float | None
    flagged_points: int = 0
    max_is_flagged: bool = False

    def as_dict(self) -> dict:
        return {k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in self.__dict__.items()}


@dataclass
class StabilityResult:
    sensor: str
    evaluable: bool
    stable_at_end: bool
    end_change_K: float | None
    end_rate_K_per_h: float | None
    stable_since_min: float | None  # from this window end on, all windows satisfied the criterion


@dataclass
class LimitCheck:
    limit_id: str
    sensor: str
    quantity: str
    limit_value: float
    source: str
    final_value: float | None
    max_value: float | None
    margin_final: float | None
    margin_max: float | None
    statement: str


@dataclass
class RunAnalysis:
    run: TestRun
    quality: QualityReport
    curves: pd.DataFrame  # time_min, ambient_C (optional), <sensor>_C absolute, <sensor> rise columns
    has_rise: bool
    sensors: list[str]  # analysed (non-ambient) sensors
    stats: list[SensorStats]
    stability: list[StabilityResult]
    run_stable: bool | None
    limits: list[LimitCheck]
    status: ResultStatus
    messages: list[Message] = field(default_factory=list)

    def stats_frame(self) -> pd.DataFrame:
        return pd.DataFrame([s.as_dict() for s in self.stats])


def ambient_reference(table: pd.DataFrame, ambient: list[str]) -> pd.Series | None:
    cols = [a for a in ambient if a in table.columns]
    if not cols:
        return None
    return table[cols].mean(axis=1, skipna=True)


def _crossing_time(t: np.ndarray, x: np.ndarray, level: float, rising: bool) -> float | None:
    ok = ~np.isnan(x)
    t, x = t[ok], x[ok]
    hit = np.where(x >= level)[0] if rising else np.where(x <= level)[0]
    if hit.size == 0:
        return None
    i = int(hit[0])
    if i == 0:
        return float(t[0])
    x0, x1 = x[i - 1], x[i]
    if x1 == x0:
        return float(t[i])
    return float(t[i - 1] + (level - x0) * (t[i] - t[i - 1]) / (x1 - x0))


def _max_rate(t: np.ndarray, x: np.ndarray, span_min: float = 5.0) -> float | None:
    ok = ~np.isnan(x)
    t, x = t[ok], x[ok]
    if t.size < 3:
        return None
    s = pd.Series(x, index=pd.to_timedelta(t, unit="m"))
    smooth = s.rolling(pd.Timedelta(minutes=span_min), min_periods=1).mean().to_numpy()
    j = np.searchsorted(t, t + span_min)
    j = np.clip(j, 0, t.size - 1)
    dt = t[j] - t
    good = dt >= 0.5 * span_min
    if not good.any():
        return None
    rates = (smooth[j][good] - smooth[good]) / dt[good] * 60.0
    return float(np.max(np.abs(rates)))


def sensor_statistics(t: np.ndarray, temp: np.ndarray, rise: np.ndarray | None, final_window_min: float,
                      sid: str, name: str) -> SensorStats:
    valid = ~np.isnan(temp)
    tv = t[valid]
    imax = int(np.nanargmax(temp))
    end_mask = t >= (tv[-1] - final_window_min)
    final_T = float(np.nanmean(temp[end_mask]))
    if rise is None:
        return SensorStats(sid, name, float(temp[imax]), None, float(t[imax]), None, None, final_T,
                           None, None, None, None, None)
    rv = rise[~np.isnan(rise)]
    r0 = float(rv[0]) if rv.size else np.nan
    rf = float(np.nanmean(rise[end_mask]))
    duration = float(tv[-1] - tv[0]) if tv.size > 1 else np.nan
    mean_rate = (rf - r0) / duration * 60.0 if duration and duration > 0 else None
    t50 = t63 = t90 = None
    span = rf - r0
    if abs(span) >= 0.5:
        rising = span > 0
        t50 = _crossing_time(t, rise, r0 + 0.5 * span, rising)
        t63 = _crossing_time(t, rise, r0 + 0.632 * span, rising)
        t90 = _crossing_time(t, rise, r0 + 0.9 * span, rising)
    return SensorStats(
        sensor=sid, name=name, max_temperature_C=float(temp[imax]), max_rise_K=float(np.nanmax(rise)),
        time_of_max_min=float(t[imax]), initial_rise_K=r0, final_rise_K=rf, final_temperature_C=final_T,
        mean_rate_K_per_h=mean_rate, max_rate_K_per_h=_max_rate(t, rise), t50_min=t50, t63_min=t63, t90_min=t90,
    )


def evaluate_stability(t: np.ndarray, x: np.ndarray, crit: StabilityCriterion, sid: str) -> StabilityResult:
    ok = ~np.isnan(x)
    t, x = t[ok], x[ok]
    W = crit.window_min
    if t.size < 3 or t[-1] - t[0] < W:
        return StabilityResult(sid, False, False, None, None, None)
    idx = pd.to_timedelta(t - t[0], unit="m")
    s = pd.Series(x, index=idx)
    win = pd.Timedelta(minutes=W)
    rng = (s.rolling(win, closed="both").max() - s.rolling(win, closed="both").min()).to_numpy()
    ts = pd.Series(t, index=idx)
    n = s.rolling(win, closed="both").count().to_numpy()
    st = ts.rolling(win, closed="both").sum().to_numpy()
    sx = s.rolling(win, closed="both").sum().to_numpy()
    stt = (ts**2).rolling(win, closed="both").sum().to_numpy()
    stx = (ts * s).rolling(win, closed="both").sum().to_numpy()
    denom = n * stt - st**2
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = np.where(denom > 0, (n * stx - st * sx) / denom, np.nan) * 60.0  # K/h
    full = (t - t[0]) >= W - 1e-9
    ok_change = np.ones_like(full) if crit.max_change_K is None else rng <= crit.max_change_K
    ok_rate = np.ones_like(full) if crit.max_rate_K_per_h is None else np.abs(slope) <= crit.max_rate_K_per_h
    stable = full & ok_change & ok_rate & (n >= 3)
    at_end = bool(stable[-1])
    since = None
    if at_end:
        k = len(stable) - 1
        while k - 1 >= 0 and stable[k - 1]:
            k -= 1
        since = float(t[k])
    return StabilityResult(sid, True, at_end, float(rng[-1]), float(abs(slope[-1])), since)


def _limit_source(lim: Limit) -> str:
    return lim.source or "用户临时设定（未注明来源）"


def check_limits(stats: list[SensorStats], limits: list[Limit], ambient: set[str]) -> list[LimitCheck]:
    out = []
    for lim in limits:
        for s in stats:
            if s.sensor in ambient or (lim.sensor != "*" and lim.sensor != s.sensor):
                continue
            if lim.quantity == "temperature_rise":
                fv, mv = s.final_rise_K, s.max_rise_K
            else:
                fv, mv = s.final_temperature_C, s.max_temperature_C
            if fv is None:
                out.append(LimitCheck(lim.id, s.sensor, lim.quantity, lim.value, _limit_source(lim), None, None,
                                      None, None, "无温升数据，未比较"))
                continue
            mf, mm = lim.value - fv, lim.value - mv
            stmt = "最终值低于用户设定限值" if mf >= 0 else "最终值超过用户设定限值"
            if mm < 0 <= mf:
                stmt += "；过程最大值超过限值"
            if s.max_is_flagged:
                stmt += "（过程最大值落在疑似跳点上，请人工复核）"
            out.append(LimitCheck(lim.id, s.sensor, lim.quantity, lim.value, _limit_source(lim), fv, mv, mf, mm, stmt))
    return out


def analyse_run(project: TestProject, quality: QualityReport) -> RunAnalysis:
    run = project.run(quality.run_id)
    msgs: list[Message] = []
    table = quality.table
    t = table["time_min"].to_numpy(dtype=float)
    amb_ids = set(run.ambient)
    sensors = [c for c in table.columns if c != "time_min" and c not in amb_ids]
    if quality.status in (ResultStatus.rejected, ResultStatus.failed):
        return RunAnalysis(run, quality, pd.DataFrame({"time_min": t}), False, sensors, [], [], None, [],
                           quality.status, [Message(code="quality_rejected", text="数据质量检查未通过，未做曲线分析",
                                                    level="rejected")])
    amb = ambient_reference(table, run.ambient)
    curves = pd.DataFrame({"time_min": t})
    if amb is not None:
        curves["ambient_C"] = amb.to_numpy()
    stats: list[SensorStats] = []
    stability: list[StabilityResult] = []
    crit = project.stability
    targets = sensors if crit.sensors == "all" else [s for s in crit.sensors if s in sensors]
    for sid in sensors:
        temp = table[sid].to_numpy(dtype=float)
        curves[f"{sid}_C"] = temp
        if np.isnan(temp).all():
            continue
        rise = None
        if amb is not None:
            rise = temp - amb.to_numpy()
            curves[sid] = rise
        name = project.sensor(sid).name
        st = sensor_statistics(t, temp, rise, project.final_window_min, sid, name)
        if quality.flags is not None and sid in quality.flags:
            fl = quality.flags[sid].to_numpy() != ""
            st.flagged_points = int(fl.sum())
            st.max_is_flagged = bool(fl[int(np.nanargmax(temp))])
        stats.append(st)
        if rise is not None and sid in targets:
            stability.append(evaluate_stability(t, rise, crit, sid))
    status = ResultStatus.success
    run_stable = None
    if stability:
        evaluable = [s for s in stability if s.evaluable]
        if len(evaluable) < len(stability):
            msgs.append(Message(code="stability_not_evaluable",
                                text=f"试验时长短于稳定判据窗口 {crit.window_min:g} min，部分测点无法评价"))
            status = ResultStatus.warning
        run_stable = bool(evaluable) and all(s.stable_at_end for s in evaluable) and len(evaluable) == len(stability)
    limits = check_limits(stats, project.limits, amb_ids)
    return RunAnalysis(run, quality, curves, amb is not None, sensors, stats, stability, run_stable, limits,
                       worst_status(status, quality.status), msgs)


# ------------------------------------------------------------------ comparison
@dataclass
class SensorComparison:
    sensor: str
    final_rise_a: float | None
    final_rise_b: float | None
    delta_final_K: float | None
    max_rise_a: float | None
    max_rise_b: float | None
    delta_max_K: float | None
    max_abs_curve_diff_K: float | None
    time_of_max_diff_min: float | None


@dataclass
class RunComparison:
    run_a: str
    run_b: str
    common_range_min: tuple[float, float] | None
    sensors: list[SensorComparison]
    diff_curves: pd.DataFrame  # time_min + sensor: rise_b - rise_a
    messages: list[Message]


def compare_runs(a: RunAnalysis, b: RunAnalysis) -> RunComparison:
    msgs: list[Message] = []
    if not (a.has_rise and b.has_rise):
        msgs.append(Message(code="no_rise", text="至少一次试验无环境参考，只能比较绝对温度统计", level="warning"))
    ta = a.curves["time_min"].to_numpy() - a.run.align_offset_min
    tb = b.curves["time_min"].to_numpy() - b.run.align_offset_min
    lo, hi = max(ta.min(), tb.min()), min(ta.max(), tb.max())
    common = [s for s in a.sensors if s in b.sensors]
    if set(a.sensors) ^ set(b.sensors):
        msgs.append(Message(code="sensor_mismatch", text="两次试验测点不完全一致，只比较同名测点",
                            level="info", context={"only_a": sorted(set(a.sensors) - set(b.sensors)),
                                                   "only_b": sorted(set(b.sensors) - set(a.sensors))}))
    sa = {s.sensor: s for s in a.stats}
    sb = {s.sensor: s for s in b.stats}
    diff = pd.DataFrame()
    rng = None
    if hi > lo:
        step = max(a.quality.median_interval_min, b.quality.median_interval_min)
        grid = np.arange(lo, hi + 1e-9, step)
        diff = pd.DataFrame({"time_min": grid})
        rng = (float(lo), float(hi))
    else:
        msgs.append(Message(code="no_overlap", text="两次试验对齐后的时间段不重叠，只比较统计量"))
    out = []
    for sid in common:
        x, y = sa.get(sid), sb.get(sid)
        if x is None or y is None:
            continue
        maxdiff = tmax = None
        if rng is not None and a.has_rise and b.has_rise and sid in a.curves and sid in b.curves:
            ra = np.interp(diff["time_min"], ta, a.curves[sid].to_numpy())
            rb = np.interp(diff["time_min"], tb, b.curves[sid].to_numpy())
            d = rb - ra
            diff[sid] = d
            k = int(np.nanargmax(np.abs(d)))
            maxdiff, tmax = float(d[k]), float(diff["time_min"].iat[k])

        def delta(p, q):
            return None if p is None or q is None else q - p

        out.append(SensorComparison(sid, x.final_rise_K, y.final_rise_K, delta(x.final_rise_K, y.final_rise_K),
                                    x.max_rise_K, y.max_rise_K, delta(x.max_rise_K, y.max_rise_K), maxdiff, tmax))
    return RunComparison(a.run.id, b.run.id, rng, out, diff, msgs)
