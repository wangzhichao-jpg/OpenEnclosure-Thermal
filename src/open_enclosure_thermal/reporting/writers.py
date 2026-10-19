"""Report writers: self-contained HTML, Excel workbook and JSON."""

from __future__ import annotations

import html
import json
import math
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import DISCLAIMER_EN, DISCLAIMER_ZH
from ..ventilation import ASSUMPTIONS_ZH
from . import plots

STATUS_ZH = {"success": "成功", "warning": "有警告", "rejected": "拒绝给出结论", "failed": "失败"}
STATUS_COLOR = {"success": "#2e7d32", "warning": "#ef6c00", "rejected": "#6a1b9a", "failed": "#c62828"}

CSS = """
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;margin:24px auto;max-width:1100px;
color:#222;line-height:1.5;padding:0 16px}
h1{font-size:22px;border-bottom:2px solid #4e79a7;padding-bottom:6px}h2{font-size:18px;margin-top:28px;color:#284b73}
h3{font-size:15px;margin-top:18px}table{border-collapse:collapse;margin:8px 0;font-size:12.5px}
th,td{border:1px solid #ccc;padding:3px 7px;text-align:left;vertical-align:top}th{background:#eef3f9}
.badge{display:inline-block;padding:1px 8px;border-radius:9px;color:#fff;font-size:12px}
.note{background:#fff8e1;border-left:4px solid #f9a825;padding:8px 12px;margin:10px 0}
.small{font-size:12px;color:#555}img{max-width:100%;margin:6px 0}code{background:#f3f3f3;padding:0 3px}
"""


# ------------------------------------------------------------------ helpers
def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, (bool, np.bool_)):
        return "是" if v else "否"
    if isinstance(v, (float, np.floating)):
        if math.isnan(v):
            return "—"
        a = abs(v)
        if a == 0:
            return "0"
        if a >= 1e5 or a < 1e-3:
            return f"{v:.{nd}e}"
        if a >= 100:
            return f"{v:.1f}"
        return f"{v:.{nd}f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt(x, nd) for x in v)
    return html.escape(str(v))


def _table(rows: list[dict], cols: list[tuple[str, str]] | None = None) -> str:
    if not rows:
        return "<p class='small'>（无）</p>"
    cols = cols or [(k, k) for k in rows[0]]
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in cols)
    body = "".join("<tr>" + "".join(f"<td>{_fmt(r.get(k))}</td>" for k, _ in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _badge(status: str) -> str:
    return f"<span class='badge' style='background:{STATUS_COLOR.get(status, '#555')}'>{STATUS_ZH.get(status, status)}</span>"


def _img(b64: str | None) -> str:
    return f"<img src='data:image/png;base64,{b64}'/>" if b64 else ""


def _messages(msgs) -> str:
    if not msgs:
        return ""
    items = "".join(f"<li>[{html.escape(m.level)}] {html.escape(m.text)} <span class='small'>({m.code})</span></li>"
                    for m in msgs)
    return f"<ul>{items}</ul>"


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(float(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "model_dump"):
        return _jsonable(o.model_dump(mode="json"))
    return o


def _page(title: str, body: str) -> str:
    return (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _header(title: str, project, prov, status: str) -> str:
    inputs = _table(prov.inputs, [("role", "用途"), ("file", "文件"), ("sha256", "SHA-256"), ("source_type", "数据来源")])
    return (
        f"<h1>{html.escape(title)} {_badge(status)}</h1>"
        f"<p>项目：<b>{html.escape(project.project.name)}</b>　数据来源：<code>{project.project.source_type.value}</code>"
        f"　访问级别：<code>{project.project.data_access}</code></p>"
        f"<p class='small'>OpenEnclosure-Thermal {prov.software_version} · Python {prov.python} · 运行时间 {prov.run_time}</p>"
        f"<div class='note'>{DISCLAIMER_ZH}<br><span class='small'>{DISCLAIMER_EN}</span></div>"
        f"<h2>输入文件与指纹</h2>{inputs}"
    )


# ------------------------------------------------------------------ test analysis
STAT_COLS = [("sensor", "测点"), ("name", "名称"), ("max_temperature_C", "最高温度/°C"), ("max_rise_K", "最大温升/K"),
             ("time_of_max_min", "最高点时间/min"), ("final_rise_K", "最终温升/K"), ("mean_rate_K_per_h", "平均速率/(K/h)"),
             ("max_rate_K_per_h", "最大速率/(K/h)"), ("t50_min", "t50/min"), ("t63_min", "t63/min"), ("t90_min", "t90/min"),
             ("flagged_points", "疑似异常点"), ("max_is_flagged", "最大值在异常点")]


def _fit_rows(fits) -> list[dict]:
    rows = []
    for f in fits:
        p = f.parameters
        rows.append({
            "run": f.run, "sensor": f.sensor, "status": STATUS_ZH[f.status.value],
            "rise_inf_K": p["rise_inf_K"].value if "rise_inf_K" in p else None,
            "se_rise_inf_K": p["rise_inf_K"].std_error if "rise_inf_K" in p else None,
            "tau_min": p["tau_min"].value if "tau_min" in p else None,
            "se_tau_min": p["tau_min"].std_error if "tau_min" in p else None,
            "rmse_K": f.rmse_K, "duration_over_tau": f.duration_over_tau, "corr": f.correlation_rinf_tau,
            "warnings": "；".join(m.text for m in f.messages),
        })
    return rows


FIT_COLS = [("run", "试验"), ("sensor", "测点"), ("status", "状态"), ("rise_inf_K", "稳态温升/K"), ("se_rise_inf_K", "标准误差/K"),
            ("tau_min", "时间常数/min"), ("se_tau_min", "标准误差/min"), ("rmse_K", "RMSE/K"),
            ("duration_over_tau", "时长/τ"), ("corr", "参数相关系数"), ("warnings", "说明")]


def test_report_html(rep) -> str:
    pj = rep.project
    st = pj.stability
    parts = [_header("温升试验分析报告 / Temperature-rise test analysis", pj, rep.provenance, rep.status.value)]
    parts.append(_messages(rep.messages))
    parts.append("<h2>用户设定判据与限值</h2>")
    parts.append(_table([{
        "window": st.window_min, "change": st.max_change_K, "rate": st.max_rate_K_per_h,
        "sensors": st.sensors if isinstance(st.sensors, str) else ",".join(st.sensors), "source": st.source}],
        [("window", "窗口/min"), ("change", "最大变化/K"), ("rate", "最大变化率/(K/h)"), ("sensors", "适用测点"), ("source", "来源")]))
    parts.append("<p class='small'>报告仅陈述“是否满足用户设定的稳定判据”，不判定试验合格与否。</p>")
    parts.append(_table([{"id": l.id, "q": l.quantity, "s": l.sensor, "v": l.value, "src": l.source or "用户临时设定（未注明来源）"}
                         for l in pj.limits], [("id", "编号"), ("q", "量"), ("s", "测点"), ("v", "数值"), ("src", "来源")]))
    for a in rep.runs:
        q = a.quality
        load = a.run.load
        parts.append(f"<h2>试验 {html.escape(a.run.id)} {_badge(a.status.value)}</h2>")
        parts.append(f"<p>{html.escape(a.run.description)}　电流：{_fmt(load.current_A if load else None)} A　"
                     f"环境参考：{html.escape(', '.join(a.run.ambient) or '无')}</p>")
        parts.append("<h3>数据质量</h3>" + _table([q.summary()], [
            ("n_samples", "样本数"), ("duration_min", "时长/min"), ("median_interval_min", "中位间隔/min"),
            ("excluded_points", "排除点数"), ("clean_sha256", "清洗后数据SHA-256")]))
        parts.append(_messages(q.messages + a.messages))
        if q.exclusions:
            parts.append("<details><summary>排除记录</summary>" + _table(
                [e.__dict__ for e in q.exclusions[:200]],
                [("sensor", "测点"), ("time_min", "时间/min"), ("value_C", "原值/°C"), ("reason", "原因")]) + "</details>")
        if not a.stats:
            continue
        parts.append("<h3>测点统计（按最大温升排序）</h3>")
        rows = sorted([s.as_dict() for s in a.stats], key=lambda r: -(r["max_rise_K"] or r["max_temperature_C"] or 0))
        parts.append(_table(rows, STAT_COLS))
        if a.stability:
            parts.append(f"<h3>稳定判据评价：{'全部测点满足' if a.run_stable else '未全部满足'}用户设定判据</h3>")
            parts.append(_table([s.__dict__ for s in a.stability], [
                ("sensor", "测点"), ("evaluable", "可评价"), ("stable_at_end", "末段满足"), ("end_change_K", "末窗变化/K"),
                ("end_rate_K_per_h", "末窗速率/(K/h)"), ("stable_since_min", "持续满足起点/min")]))
        if a.limits:
            parts.append("<h3>与用户设定限值比较</h3>" + _table([l.__dict__ for l in a.limits], [
                ("limit_id", "限值"), ("sensor", "测点"), ("quantity", "量"), ("limit_value", "限值"), ("final_value", "最终值"),
                ("margin_final", "最终裕量"), ("max_value", "过程最大"), ("statement", "比较结果"), ("source", "限值来源")]))
        parts.append(_img(plots.run_curves(a, rise=False)))
        parts.append(_img(plots.run_curves(a, rise=True)) if a.has_rise else "")
    if rep.fits:
        parts.append("<h2>热响应参数识别（一阶模型）</h2>")
        parts.append("<p class='small'>模型：Δθ(t)=Δθ₀+(Δθ∞−Δθ₀)(1−e^{−(t−t₀)/τ})，恒定负荷、单测点。标准误差基于残差独立假设，"
                     "残差呈系统性时会被低估。参数只适用于识别时的电流和环境范围。</p>")
        parts.append(_table(_fit_rows(rep.fits), FIT_COLS))
        for f in rep.fits:
            parts.append(_img(plots.fit_plot(f)))
    if rep.exponents:
        parts.append("<h2>电流—稳态温升关系</h2><p class='small'>Δθ∞(I)=Δθr(I/Ir)^n；只在识别电流范围内使用，默认拒绝外推。</p>")
        parts.append(_table([{
            "sensor": e.sensor, "cfg": e.configuration, "status": STATUS_ZH[e.status.value], "levels": e.n_levels,
            "n": e.exponent_n.value if e.exponent_n else None, "se": e.exponent_n.std_error if e.exponent_n else None,
            "ir": e.current_ref_A, "rr": e.rise_ref_K.value if e.rise_ref_K else None, "range": e.current_range_A,
            "w": "；".join(m.text for m in e.messages)} for e in rep.exponents],
            [("sensor", "测点"), ("cfg", "配置"), ("status", "状态"), ("levels", "电流水平数"), ("n", "指数n"), ("se", "标准误差"),
             ("ir", "参考电流/A"), ("rr", "参考温升/K"), ("range", "适用电流/A"), ("w", "说明")]))
        for e in rep.exponents:
            parts.append(_img(plots.exponent_plot(e)))
    for c in rep.comparisons:
        parts.append(f"<h2>试验对比：{html.escape(c.run_b)} 相对 {html.escape(c.run_a)}</h2>")
        parts.append(_messages(c.messages))
        parts.append(_table([s.__dict__ for s in c.sensors], [
            ("sensor", "测点"), ("final_rise_a", f"{c.run_a} 最终温升"), ("final_rise_b", f"{c.run_b} 最终温升"),
            ("delta_final_K", "差值/K"), ("max_rise_a", f"{c.run_a} 最大"), ("max_rise_b", f"{c.run_b} 最大"),
            ("delta_max_K", "差值/K"), ("max_abs_curve_diff_K", "曲线最大差/K"), ("time_of_max_diff_min", "发生时间/min")]))
        parts.append(_img(plots.comparison_plot(c)))
    parts.append(f"<hr><p class='small'>{DISCLAIMER_ZH}</p>")
    return _page(f"温升试验分析 - {pj.project.name}", "".join(parts))


def test_report_json(rep) -> dict:
    return _jsonable({
        "report_type": "test_analysis",
        "status": rep.status.value,
        "disclaimer": DISCLAIMER_ZH,
        "provenance": rep.provenance.__dict__,
        "project": rep.project.model_dump(mode="json"),
        "messages": [m.model_dump() for m in rep.messages],
        "runs": [{
            "run": a.run.id, "status": a.status.value, "quality": a.quality.summary(),
            "quality_messages": [m.model_dump() for m in a.quality.messages],
            "exclusions": [e.__dict__ for e in a.quality.exclusions],
            "stats": [s.as_dict() for s in a.stats], "stability": [s.__dict__ for s in a.stability],
            "run_stable": a.run_stable, "limits": [l.__dict__ for l in a.limits],
        } for a in rep.runs],
        "fits": [f.as_dict() for f in rep.fits],
        "current_exponents": [e.as_dict() for e in rep.exponents],
        "comparisons": [{"run_a": c.run_a, "run_b": c.run_b, "common_range_min": c.common_range_min,
                         "sensors": [s.__dict__ for s in c.sensors]} for c in rep.comparisons],
    })


def test_report_excel(rep, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([{"项目": rep.project.project.name, "状态": rep.status.value, "版本": rep.provenance.software_version,
                       "运行时间": rep.provenance.run_time, "声明": DISCLAIMER_ZH}]).to_excel(xw, sheet_name="概要", index=False)
        pd.DataFrame(rep.provenance.inputs).to_excel(xw, sheet_name="输入指纹", index=False)
        for a in rep.runs:
            a.stats_frame().to_excel(xw, sheet_name=f"{a.run.id}_统计"[:31], index=False)
            a.curves.to_excel(xw, sheet_name=f"{a.run.id}_曲线"[:31], index=False)
            pd.DataFrame([m.model_dump() for m in a.quality.messages]).to_excel(xw, sheet_name=f"{a.run.id}_质量"[:31], index=False)
        if rep.fits:
            pd.DataFrame(_fit_rows(rep.fits)).to_excel(xw, sheet_name="参数识别", index=False)
        if rep.exponents:
            pd.DataFrame([{**e.as_dict(), "exponent_n": e.exponent_n.value if e.exponent_n else None}
                          for e in rep.exponents]).astype(str).to_excel(xw, sheet_name="电流指数", index=False)
        for c in rep.comparisons:
            pd.DataFrame([s.__dict__ for s in c.sensors]).to_excel(xw, sheet_name=f"对比_{c.run_b}-{c.run_a}"[:31], index=False)


# ------------------------------------------------------------------ ventilation
def _case_cols(rank_rule: str) -> list[tuple[str, str]]:
    return [("rank", "排名"), ("case", "方案"), ("description", "说明"), ("status", "状态"), ("air_rise_K", "空气温升/K"),
            ("T_zone_C", "空气节点/°C"), ("flow_inlet_m3h", "风量/(m³/h)"), ("fan_power_W", "风机功率/W"),
            ("margin_K", "限值裕量/K"), ("reason", "失败/拒绝原因")]


HB_ZH = {"internal": "内部发热", "fan_heat_to_zone": "风机电功率计入", "solar_equivalent": "日照等效得热（相对室外）",
         "ventilation_out": "通风排热", "walls_out_net": "壁面净散热（负值为得热）", "residual": "能量平衡残差"}


def vent_report_html(rep) -> str:
    m, s = rep.model, rep.study
    b = s.base.result
    parts = [_header("单室箱体通风散热快算 / Single-zone enclosure ventilation", m, rep.provenance, b.status.value)]
    parts.append("<h2>模型假设与适用范围</h2><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in ASSUMPTIONS_ZH) + "</ul>")
    parts.append("<h2>输入概要</h2>")
    parts.append(_table([{"q": m.internal_heat_W, "t": m.ambient.temperature_C, "mode": m.ventilation.mode,
                          "H": m.ventilation.stack_height_m, "UA": b.UA_W_K}],
                        [("q", "内部发热/W"), ("t", "室外温度/°C"), ("mode", "通风方式"), ("H", "浮力高差/m"), ("UA", "壁面UA/(W/K)")]))
    parts.append(_table([e.model_dump() for e in m.elements]))
    if m.fans:
        parts.append(_table([{**f.model_dump(exclude={"curve"}), "curve": "; ".join(
            f"{p.flow_m3h:g}m³/h@{p.static_pressure_Pa:g}Pa" for p in f.curve)} for f in m.fans]))
    parts.append(f"<h2>基准方案结果 {_badge(b.status.value)}</h2>")
    parts.append(_messages(b.messages))
    if b.ok():
        parts.append(_table([{"tz": b.T_zone_C, "rise": b.air_rise_K, "q": b.flow_inlet_m3h, "m": b.mass_flow_kg_s,
                              "stack": b.dp_stack_Pa, "fp": b.fan_power_W}],
                            [("tz", "空气节点温度/°C"), ("rise", "空气温升/K"), ("q", "进风量/(m³/h)"), ("m", "质量流量/(kg/s)"),
                             ("stack", "浮力压差/Pa"), ("fp", "风机功率/W")]))
        parts.append("<h3>热平衡</h3>" + _table([{"k": HB_ZH.get(k, k), "v": v} for k, v in b.heat_balance_W.items()],
                                                [("k", "项目"), ("v", "W")]))
        parts.append("<h3>阻力分项</h3>" + _table([{"k": k, "v": v} for k, v in b.element_dp_Pa.items()],
                                                [("k", "阻力件"), ("v", "Δp/Pa")]))
        if b.fan_points:
            parts.append("<h3>风机工作点</h3>" + _table([{"id": k, **v} for k, v in b.fan_points.items()]))
        parts.append(_img(plots.fan_system_plot(m, b)))
    parts.append(f"<h2>方案比较（排序：{html.escape(s.rank_rule)}）</h2>")
    if m.study.air_rise_limit:
        lim = m.study.air_rise_limit
        parts.append(f"<p>空气温升比较值：{lim.value_K:g} K（来源：{html.escape(lim.source or '用户临时设定（未注明来源）')}）</p>")
    parts.append(_messages(s.messages))
    rows = sorted([c.row() for c in s.cases], key=lambda r: (r["rank"] is None, r["rank"] or 0))
    parts.append(_table(rows, _case_cols(s.rank_rule)))
    parts.append("<p class='small'>失败或被拒绝的方案保留在表中并注明原因，不参与排名。</p>")
    parts.append(_img(plots.scenario_bar(s.cases)))
    for sid, rows in s.sweeps.items():
        sw = next(x for x in m.study.sweeps if x.id == sid)
        parts.append(f"<h3>参数扫描 {html.escape(sid)}：<code>{html.escape(sw.parameter)}</code></h3>")
        parts.append(_table(rows, [("value", "取值"), ("status", "状态"), ("air_rise_K", "空气温升/K"),
                                   ("flow_inlet_m3h", "风量/(m³/h)"), ("reason", "原因")]))
        parts.append(_img(plots.sweep_plot(sid, sw.parameter, rows)))
    if s.sensitivity:
        parts.append("<h2>单因素敏感性（相对基准）</h2>" + _table(s.sensitivity, [
            ("change", "变化"), ("status", "状态"), ("air_rise_K", "空气温升/K"), ("delta_air_rise_K", "温升变化/K"),
            ("flow_inlet_m3h", "风量/(m³/h)"), ("delta_flow_m3h", "风量变化/(m³/h)"), ("reason", "原因")]))
    parts.append(f"<hr><p class='small'>{DISCLAIMER_ZH}</p>")
    return _page(f"箱体通风快算 - {m.project.name}", "".join(parts))


def vent_report_json(rep) -> dict:
    s = rep.study
    return _jsonable({
        "report_type": "box_ventilation",
        "status": s.base.result.status.value,
        "disclaimer": DISCLAIMER_ZH,
        "assumptions": ASSUMPTIONS_ZH,
        "provenance": rep.provenance.__dict__,
        "model": rep.model.model_dump(mode="json"),
        "base": s.base.result.as_dict(),
        "rank_rule": s.rank_rule,
        "cases": [{**c.row(), "overrides": c.overrides, "result": c.result.as_dict()} for c in s.cases],
        "sweeps": s.sweeps,
        "sensitivity": s.sensitivity,
        "messages": [m.model_dump() for m in s.messages],
    })


def vent_report_excel(rep, path: Path) -> None:
    s = rep.study
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([{"项目": rep.model.project.name, "状态": s.base.result.status.value,
                       "版本": rep.provenance.software_version, "声明": DISCLAIMER_ZH}]).to_excel(xw, sheet_name="概要", index=False)
        pd.DataFrame(rep.provenance.inputs).to_excel(xw, sheet_name="输入指纹", index=False)
        pd.DataFrame([c.row() for c in s.cases]).to_excel(xw, sheet_name="方案比较", index=False)
        pd.DataFrame([{"项目": k, "W": v} for k, v in s.base.result.heat_balance_W.items()]).to_excel(
            xw, sheet_name="基准热平衡", index=False)
        for sid, rows in s.sweeps.items():
            pd.DataFrame(rows).to_excel(xw, sheet_name=f"扫描_{sid}"[:31], index=False)
        if s.sensitivity:
            pd.DataFrame(s.sensitivity).to_excel(xw, sheet_name="敏感性", index=False)
        pd.DataFrame({"假设": ASSUMPTIONS_ZH}).to_excel(xw, sheet_name="模型假设", index=False)


# ------------------------------------------------------------------ entry points
def write_report(rep, outdir: str | Path, formats: tuple[str, ...] = ("html", "xlsx", "json"),
                 stem: str | None = None) -> list[Path]:
    from ..workflows import TestAnalysisReport

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    is_test = isinstance(rep, TestAnalysisReport)
    stem = stem or ("test_report" if is_test else "ventilation_report")
    written = []
    if "html" in formats:
        p = outdir / f"{stem}.html"
        p.write_text(test_report_html(rep) if is_test else vent_report_html(rep), encoding="utf-8")
        written.append(p)
    if "json" in formats:
        p = outdir / f"{stem}.json"
        p.write_text(json.dumps(test_report_json(rep) if is_test else vent_report_json(rep), ensure_ascii=False,
                                indent=2), encoding="utf-8")
        written.append(p)
    if "xlsx" in formats:
        p = outdir / f"{stem}.xlsx"
        (test_report_excel if is_test else vent_report_excel)(rep, p)
        written.append(p)
    return written
