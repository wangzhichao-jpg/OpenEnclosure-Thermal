"""Data quality checks for test records.

Rules (see docs): the raw file is never changed; every exclusion is logged; suspected sensor
swaps are never corrected automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..io.files import sha256_bytes
from ..io.testdata import RunData
from ..schema import Message, QualityConfig, ResultStatus, worst_status


@dataclass
class Exclusion:
    sensor: str
    time_min: float
    value_C: float
    reason: str


@dataclass
class QualityReport:
    run_id: str
    status: ResultStatus
    messages: list[Message]
    n_samples: int
    duration_min: float
    median_interval_min: float
    table: pd.DataFrame  # cleaned table actually used for analysis
    exclusions: list[Exclusion] = field(default_factory=list)
    flags: pd.DataFrame | None = None  # per-sample flags (sensor -> str or "")
    raw_sha256: str = ""
    clean_sha256: str = ""

    def summary(self) -> dict:
        return {
            "run": self.run_id,
            "status": self.status.value,
            "n_samples": self.n_samples,
            "duration_min": round(self.duration_min, 3),
            "median_interval_min": round(self.median_interval_min, 4),
            "excluded_points": len(self.exclusions),
            "raw_sha256": self.raw_sha256,
            "clean_sha256": self.clean_sha256,
        }


def _longest_constant_run(x: np.ndarray) -> int:
    x = x[~np.isnan(x)]
    if x.size == 0:
        return 0
    best = cur = 1
    for a, b in zip(x[:-1], x[1:]):
        cur = cur + 1 if a == b else 1
        best = max(best, cur)
    return best


def check_run(data: RunData, cfg: QualityConfig) -> QualityReport:
    rid = data.run.id
    msgs: list[Message] = []
    status = ResultStatus.success
    table = data.table.copy()
    exclusions: list[Exclusion] = []

    def add(code: str, text: str, level: str = "warning", **ctx) -> None:
        nonlocal status
        msgs.append(Message(code=code, text=text, level=level, context=ctx))
        status = worst_status(status, ResultStatus(level) if level != "info" else ResultStatus.success)

    for sid in data.missing_sensors:
        add("sensor_column_missing", f"测点 {sid} 在数据文件中没有对应列 / mapped column not found", sensor=sid)
    for sid, n in data.nonnumeric_counts.items():
        add("non_numeric", f"测点 {sid} 有 {n} 个非数值单元格，按缺测处理", sensor=sid, count=n)

    # --- time checks
    nan_t = table["time_min"].isna()
    if nan_t.any():
        add("time_missing", f"{int(nan_t.sum())} 行时间缺失，已排除这些行", rows=int(nan_t.sum()))
        table = table.loc[~nan_t].reset_index(drop=True)
    t = table["time_min"].to_numpy(dtype=float)
    dt = np.diff(t)
    back = np.where(dt < 0)[0]
    if back.size:
        add(
            "time_backwards",
            f"时间在第 {int(back[0]) + 2} 行附近倒退（共 {back.size} 处），停止分析该试验；请检查是否拼接了多个文件",
            level="rejected",
            first_row=int(back[0]) + 2,
        )
    dup = np.where(dt == 0)[0]
    if dup.size:
        add("time_duplicate", f"{dup.size} 个重复时间点，保留首次出现的记录", count=int(dup.size))
        keep = np.ones(len(table), dtype=bool)
        keep[dup + 1] = False
        table = table.loc[keep].reset_index(drop=True)
        t = table["time_min"].to_numpy(dtype=float)
        dt = np.diff(t)
    pos = dt[dt > 0]
    med = float(np.median(pos)) if pos.size else float("nan")
    if pos.size:
        gaps = np.where(dt > cfg.gap_factor * med)[0]
        if gaps.size:
            add(
                "time_gap",
                f"{gaps.size} 处采样间隔超过中位间隔 {cfg.gap_factor:g} 倍（最大 {dt.max():.3g} min）",
                count=int(gaps.size),
                max_gap_min=float(dt.max()),
            )
    if len(table) < 5:
        add("too_few_samples", "有效样本少于5个，无法分析", level="rejected", n=len(table))

    # --- per-sensor value checks
    flags = pd.DataFrame("", index=table.index, columns=data.sensors)
    for sid in data.sensors:
        x = table[sid].to_numpy(dtype=float)
        n_nan = int(np.isnan(x).sum())
        if n_nan == len(x):
            add("sensor_all_missing", f"测点 {sid} 全部缺测", sensor=sid)
            continue
        if n_nan:
            add("sensor_missing_values", f"测点 {sid} 缺测 {n_nan} 点", sensor=sid, count=n_nan)
        oor = (x < cfg.min_C) | (x > cfg.max_C)
        for i in np.where(oor)[0]:
            exclusions.append(Exclusion(sid, float(t[i]), float(x[i]), "超出物理范围 out of range"))
            flags.iat[i, flags.columns.get_loc(sid)] = "out_of_range"
        if oor.any():
            add("out_of_range", f"测点 {sid} 有 {int(oor.sum())} 点超出 [{cfg.min_C:g}, {cfg.max_C:g}] °C，已排除",
                sensor=sid, count=int(oor.sum()))
            x = np.where(oor, np.nan, x)
            table[sid] = x
        valid = x[~np.isnan(x)]
        if valid.size and np.nanmax(valid) == np.nanmin(valid):
            add("constant_signal", f"测点 {sid} 全程恒定值，疑似未接入或断线", sensor=sid)
            continue
        longest = _longest_constant_run(x)
        if longest >= max(20, int(0.25 * valid.size)):
            add("stuck_signal", f"测点 {sid} 连续 {longest} 个相同读数，疑似卡值", sensor=sid, samples=longest)
        s = pd.Series(x)
        med_roll = s.rolling(5, center=True, min_periods=3).median()
        dev = (s - med_roll).abs().to_numpy()
        spikes = np.where(dev > cfg.spike_K)[0]
        if spikes.size:
            for i in spikes:
                flags.iat[i, flags.columns.get_loc(sid)] = "spike"
            if cfg.exclude_spikes:
                for i in spikes:
                    exclusions.append(Exclusion(sid, float(t[i]), float(x[i]), "跳点 spike"))
                x = x.copy()
                x[spikes] = np.nan
                table[sid] = x
                add("spike_excluded", f"测点 {sid} 有 {spikes.size} 个跳点（>{cfg.spike_K:g} K），已按设置排除",
                    sensor=sid, count=int(spikes.size))
            else:
                add("spike_flagged", f"测点 {sid} 有 {spikes.size} 个疑似跳点（>{cfg.spike_K:g} K），仅标记未排除",
                    sensor=sid, count=int(spikes.size), first_time_min=float(t[spikes[0]]))

    amb = [a for a in data.run.ambient if a in data.sensors]
    if not data.run.ambient:
        add("no_ambient", "未指定环境参考测点，只输出绝对温度，不计算温升", sensor=None)
    elif not amb:
        add("ambient_missing", "环境参考测点均不在数据中，只输出绝对温度，不计算温升")

    clean_bytes = table.to_csv(index=False, float_format="%.6g").encode("utf-8")
    return QualityReport(
        run_id=rid,
        status=status,
        messages=msgs,
        n_samples=len(table),
        duration_min=float(t[-1] - t[0]) if len(t) else 0.0,
        median_interval_min=med,
        table=table,
        exclusions=exclusions,
        flags=flags,
        raw_sha256=data.sha256,
        clean_sha256=sha256_bytes(clean_bytes),
    )
