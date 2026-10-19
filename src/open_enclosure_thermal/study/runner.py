"""Scenario comparison, one-parameter sweeps and one-at-a-time sensitivity for the box model."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..schema import BoxModel, Message, ResultStatus
from ..ventilation import VentilationResult, solve

_SEG = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(\[(?P<sel>[^\]]+)\])?$")


class OverrideError(ValueError):
    pass


def set_path(data: dict, path: str, value: Any) -> None:
    """Set ``value`` in a model mapping. Supports ``a.b``, ``list[id].field``, ``list[0].field``, ``list[*].field``."""
    parts = path.split(".")
    targets: list[Any] = [data]
    for i, part in enumerate(parts):
        m = _SEG.match(part.strip())
        if not m:
            raise OverrideError(f"bad path segment '{part}' in '{path}'")
        name, sel = m.group("name"), m.group("sel")
        last = i == len(parts) - 1
        nxt: list[Any] = []
        for t in targets:
            if not isinstance(t, dict) or (name not in t and not last) or (sel is not None and name not in t):
                raise OverrideError(f"'{path}': '{name}' not found")
            if sel is None:
                if last:
                    t[name] = value
                else:
                    nxt.append(t[name])
                continue
            seq = t[name]
            if not isinstance(seq, list):
                raise OverrideError(f"'{path}': '{name}' is not a list")
            if sel == "*":
                chosen = seq
            elif sel.isdigit():
                chosen = [seq[int(sel)]]
            else:
                chosen = [x for x in seq if isinstance(x, dict) and str(x.get("id")) == sel]
            if not chosen:
                raise OverrideError(f"'{path}': no item '{sel}' in '{name}'")
            if last:
                raise OverrideError(f"'{path}': cannot replace list items directly")
            nxt.extend(chosen)
        targets = nxt


def get_path(data: dict, path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        m = _SEG.match(part.strip())
        if not m:
            raise OverrideError(f"bad path segment '{part}'")
        name, sel = m.group("name"), m.group("sel")
        cur = cur[name]
        if sel is not None:
            if sel == "*":
                cur = cur[0]
            elif sel.isdigit():
                cur = cur[int(sel)]
            else:
                cur = next(x for x in cur if str(x.get("id")) == sel)
    return cur


def apply_overrides(model: BoxModel, overrides: dict[str, Any]) -> BoxModel:
    data = model.model_dump(mode="json")
    data["study"] = {}
    for path, value in overrides.items():
        set_path(data, path, value)
    try:
        return BoxModel.model_validate(data)
    except ValidationError as err:
        raise OverrideError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())) from None


@dataclass
class CaseResult:
    case_id: str
    description: str
    overrides: dict[str, Any]
    result: VentilationResult
    rank: int | None = None
    margin_K: float | None = None

    def row(self) -> dict:
        r = self.result
        return {
            "case": self.case_id,
            "description": self.description,
            "status": r.status.value,
            "rank": self.rank,
            "air_rise_K": r.air_rise_K,
            "T_zone_C": r.T_zone_C,
            "flow_inlet_m3h": r.flow_inlet_m3h,
            "fan_power_W": r.fan_power_W,
            "margin_K": self.margin_K,
            "ventilation_out_W": r.heat_balance_W.get("ventilation_out"),
            "walls_out_net_W": r.heat_balance_W.get("walls_out_net"),
            "reason": "; ".join(m.text for m in r.messages if m.level in ("rejected", "failed")),
        }


def _run_case(model: BoxModel, cid: str, desc: str, overrides: dict[str, Any]) -> CaseResult:
    try:
        m = apply_overrides(model, overrides) if overrides else model
        res = solve(m)
    except OverrideError as err:
        res = VentilationResult("single_zone_air_node", "0.1.0", ResultStatus.failed,
                                [Message(code="bad_override", text=f"参数设置无效：{err}", level="failed")])
    return CaseResult(cid, desc, overrides, res)


RANK_RULES = {
    "air_rise": ("空气温升升序 / air rise ascending", lambda c: c.result.air_rise_K, False),
    "flow": ("风量降序 / flow descending", lambda c: c.result.flow_inlet_m3h, True),
    "fan_power": ("风机功率升序 / fan power ascending", lambda c: c.result.fan_power_W, False),
    "margin": ("限值裕量降序 / margin descending", lambda c: c.margin_K, True),
}


@dataclass
class StudyResult:
    base: CaseResult
    cases: list[CaseResult]
    rank_rule: str
    sweeps: dict[str, list[dict]] = field(default_factory=dict)
    sensitivity: list[dict] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


def run_study(model: BoxModel) -> StudyResult:
    study = model.study
    base = _run_case(model, "base", "基准方案 / base case", {})
    cases = [base] + [_run_case(model, s.id, s.description, s.set) for s in study.scenarios]
    lim = study.air_rise_limit
    for c in cases:
        if lim and c.result.air_rise_K is not None:
            c.margin_K = lim.value_K - c.result.air_rise_K
    msgs: list[Message] = []
    rank_by = study.rank_by
    if rank_by == "margin" and lim is None:
        msgs.append(Message(code="no_limit_for_margin", text="未设置空气温升限值，改按空气温升排序"))
        rank_by = "air_rise"
    label, key, desc = RANK_RULES[rank_by]
    ok = [c for c in cases if c.result.ok() and key(c) is not None]
    for i, c in enumerate(sorted(ok, key=key, reverse=desc), start=1):
        c.rank = i
    sweeps: dict[str, list[dict]] = {}
    scen = {s.id: s.set for s in study.scenarios}
    for sw in study.sweeps:
        base_over = dict(scen.get(sw.base_scenario, {})) if sw.base_scenario else {}
        rows = []
        for v in sw.values:
            c = _run_case(model, f"{sw.id}={v:g}", "", {**base_over, sw.parameter: v})
            rows.append({"value": v, **c.row()})
        sweeps[sw.id] = rows
    return StudyResult(base, cases, label, sweeps, sensitivity(model), msgs)


def sensitivity(model: BoxModel) -> list[dict]:
    """One-at-a-time perturbations around the base case."""
    base = solve(model)
    if not base.ok():
        return []
    perturb: list[tuple[str, dict[str, Any]]] = [
        ("内部发热 +10%", {"internal_heat_W": model.internal_heat_W * 1.1}),
        ("室外温度 +5 K", {"ambient.temperature_C": model.ambient.temperature_C + 5.0}),
    ]
    openings = [e for e in model.elements if e.kind == "opening"]
    if openings:
        perturb.append(("全部开口面积 +20%", {f"elements[{e.id}].area_m2": e.area_m2 * 1.2 for e in openings}))
    for e in model.elements:
        if e.kind == "filter":
            perturb.append((f"过滤网 {e.id} 阻力 ×2（积尘）", {f"elements[{e.id}].loading_factor": e.loading_factor * 2}))
    for f in model.fans:
        if f.count > 0 and model.ventilation.mode == "forced":
            perturb.append((f"风机 {f.id} 台数 +1", {f"fans[{f.id}].count": f.count + 1}))
    if model.surfaces:
        perturb.append(("全部外表面日照 +300 W/m²", {
            f"surfaces[{s.id}].solar_irradiance_W_m2": s.solar_irradiance_W_m2 + 300.0 for s in model.surfaces}))
    rows = []
    for label, over in perturb:
        c = _run_case(model, label, label, over)
        r = c.result
        rows.append({
            "change": label,
            "status": r.status.value,
            "air_rise_K": r.air_rise_K,
            "delta_air_rise_K": None if r.air_rise_K is None else r.air_rise_K - base.air_rise_K,
            "flow_inlet_m3h": r.flow_inlet_m3h,
            "delta_flow_m3h": None if r.flow_inlet_m3h is None else r.flow_inlet_m3h - base.flow_inlet_m3h,
            "reason": c.row()["reason"],
        })
    return rows
