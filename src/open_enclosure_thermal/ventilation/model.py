"""Single-zone enclosure ventilation and heat balance (model M3).

Assumptions (also printed in reports)
-------------------------------------
* The enclosure air is one fully mixed node; outlet air temperature equals the node temperature.
* Inlet air is at outdoor temperature. Walls are lumped by an overall U (convection + radiation +
  conduction, supplied by the user). Solar gain enters through the sol-air temperature.
* Openings: dp = K * m^2 / (2 * rho * A^2); filters: dp = dp_rated * (V/V_rated)^n * loading.
  Element density is outdoor density at the inlet and node density at the outlet.
* Buoyancy: dp_stack = g * H * (rho_out - rho_node), ideal gas, constant pressure.
* At most one running fan entry per location (identical fans in parallel via ``count``);
  inlet and outlet fans act in series. Affinity laws for speed ratio. No curve extrapolation by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

from ..schema import BoxModel, Fan, Filter, Message, Opening, ResultStatus, worst_status

G = 9.80665
MODEL_ID = "single_zone_air_node"
MODEL_VERSION = "0.1.0"

ASSUMPTIONS_ZH = [
    "箱内空气为一个充分混合节点，出口空气温度等于节点温度；结果不是设备或导体热点温度。",
    "进风温度等于室外空气温度；壁面以用户给定的总传热系数U综合对流、辐射和导热。",
    "日照得热通过综合温度（sol-air）计入：T_sa = T_amb + α·G / h_out。",
    "开口阻力 Δp = K·ṁ²/(2ρA²)；过滤网阻力 Δp = Δp_rated·(V/V_rated)^n × 积尘倍数。",
    "浮力压差 Δp_stack = g·H·(ρ_out − ρ_zone)，理想气体、常压。",
    "同一位置最多一组运行风机（同型号台数并联），进、出风位置的风机串联；转速比按相似律换算；默认不外推风机曲线。",
]


class SolveError(Exception):
    def __init__(self, message: Message):
        super().__init__(message.text)
        self.msg = message


def air_density(T_C: float, p_Pa: float, R: float) -> float:
    return p_Pa / (R * (T_C + 273.15))


def sol_air_temperature(T_amb_C: float, irradiance: float, absorptance: float, h_out: float) -> float:
    return T_amb_C + absorptance * irradiance / h_out


def element_dp(el: Opening | Filter, m_dot: float, rho: float) -> float:
    if m_dot <= 0:
        return 0.0
    if isinstance(el, Opening):
        return el.loss_coefficient * m_dot**2 / (2.0 * rho * el.area_m2**2)
    V = m_dot / rho * 3600.0
    return el.rated_dp_Pa * (V / el.rated_flow_m3h) ** el.exponent * el.loading_factor


@dataclass
class FanCurve:
    fan: Fan
    flow: np.ndarray  # per fan, m3/h, scaled by speed
    pressure: np.ndarray  # Pa, scaled by speed

    @classmethod
    def from_fan(cls, fan: Fan) -> "FanCurve":
        s = fan.speed_ratio
        q = np.array([p.flow_m3h for p in fan.curve]) * s
        p = np.array([p.static_pressure_Pa for p in fan.curve]) * s**2
        return cls(fan, q, p)

    @property
    def max_flow(self) -> float:
        """Largest per-fan flow the curve may be evaluated at (m3/h)."""
        if not self.fan.allow_extrapolation:
            return float(self.flow[-1])
        q1, q2, p1, p2 = self.flow[-2], self.flow[-1], self.pressure[-2], self.pressure[-1]
        if p2 <= 0:
            return float(q2)
        slope = (p2 - p1) / (q2 - q1)
        return float(q2 - p2 / slope) if slope < 0 else float(q2 * 1.5)

    def static_pressure(self, V_per_fan: float) -> float:
        q, p = self.flow, self.pressure
        tol = 1e-9 * max(float(q[-1]), 1.0)  # round-off at the curve ends
        if q[0] - tol <= V_per_fan < q[0]:
            V_per_fan = float(q[0])
        elif q[-1] < V_per_fan <= q[-1] + tol:
            V_per_fan = float(q[-1])
        if V_per_fan < q[0]:
            if not self.fan.allow_extrapolation and q[0] > 0:
                raise SolveError(Message(code="fan_curve_low_end",
                                         text=f"风机 {self.fan.id} 工作点低于曲线最小风量 {q[0]:g} m³/h，曲线默认不外推",
                                         level="rejected"))
            return float(p[0])
        if V_per_fan <= q[-1]:
            return float(np.interp(V_per_fan, q, p))
        if not self.fan.allow_extrapolation:
            raise SolveError(Message(code="fan_curve_high_end",
                                     text=f"风机 {self.fan.id} 工作点超出曲线最大风量 {q[-1]:g} m³/h，曲线默认不外推",
                                     level="rejected"))
        slope = (p[-1] - p[-2]) / (q[-1] - q[-2])
        return float(max(p[-1] + slope * (V_per_fan - q[-1]), 0.0))

    def power_W(self) -> float:
        return self.fan.power_W * self.fan.speed_ratio**3 * self.fan.count


@dataclass
class Hydraulics:
    m_dot: float
    dp_stack: float
    element_dp: dict[str, float]
    fan_points: dict[str, dict]


@dataclass
class VentilationResult:
    model_id: str
    model_version: str
    status: ResultStatus
    messages: list[Message] = field(default_factory=list)
    T_ambient_C: float | None = None
    T_zone_C: float | None = None
    air_rise_K: float | None = None
    mass_flow_kg_s: float | None = None
    flow_inlet_m3h: float | None = None
    flow_outlet_m3h: float | None = None
    dp_stack_Pa: float | None = None
    element_dp_Pa: dict[str, float] = field(default_factory=dict)
    fan_points: dict[str, dict] = field(default_factory=dict)
    fan_power_W: float = 0.0
    heat_balance_W: dict[str, float] = field(default_factory=dict)
    UA_W_K: float = 0.0

    def ok(self) -> bool:
        return self.status in (ResultStatus.success, ResultStatus.warning)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "messages"}
        d["status"] = self.status.value
        d["messages"] = [m.model_dump() for m in self.messages]
        return d


def _running_fans(model: BoxModel) -> dict[str, FanCurve]:
    fans: dict[str, FanCurve] = {}
    for f in model.fans:
        if f.count <= 0:
            continue
        if f.location in fans:
            raise SolveError(Message(code="multiple_fan_groups",
                                     text=f"{f.location} 位置有多组运行风机；v0.1 每个位置只支持一组同型号风机",
                                     level="rejected"))
        fans[f.location] = FanCurve.from_fan(f)
    return fans


def _hydraulics(model: BoxModel, T_zone: float, fans: dict[str, FanCurve]) -> Hydraulics:
    amb, R = model.ambient, model.air.R_J_kgK
    rho_in = air_density(amb.temperature_C, amb.pressure_Pa, R)
    rho_z = air_density(T_zone, amb.pressure_Pa, R)
    vent = model.ventilation
    rho_at = {"inlet": rho_in, "outlet": rho_z}
    dp_stack = G * vent.stack_height_m * (rho_in - rho_z)
    sign = 1.0 if vent.stack_assists else -1.0

    def resistance(m: float) -> dict[str, float]:
        return {e.id: element_dp(e, m, rho_at[e.location]) for e in model.elements}

    if vent.mode == "closed":
        return Hydraulics(0.0, 0.0, {}, {})
    if vent.mode == "fixed_flow":
        m = rho_in * vent.fixed_flow_m3h / 3600.0
        return Hydraulics(m, dp_stack, resistance(m), {})

    def fan_dp(m: float) -> float:
        total = 0.0
        for loc, fc in fans.items():
            V = m / rho_at[loc] * 3600.0 / fc.fan.count
            total += fc.static_pressure(V)
        return total

    drive_stack = sign * dp_stack if vent.mode == "forced" else dp_stack

    def g(m: float) -> float:
        return fan_dp(m) + drive_stack - sum(resistance(m).values())

    if vent.mode == "natural" or not fans:
        if dp_stack <= 0:
            return Hydraulics(0.0, dp_stack, resistance(0.0), {})
        m_hi = 1e-3
        while g(m_hi) > 0:
            m_hi *= 2.0
            if m_hi > 1e4:
                raise SolveError(Message(code="no_resistance", text="系统阻力过小，自然通风无有限解", level="failed"))
        m = brentq(g, 0.0, m_hi, xtol=1e-12)
        return Hydraulics(m, dp_stack, resistance(m), {})

    # forced
    m_max = min(fc.max_flow * fc.fan.count / 3600.0 * rho_at[loc] for loc, fc in fans.items())
    m_min = max(fc.flow[0] * fc.fan.count / 3600.0 * rho_at[loc] if not fc.fan.allow_extrapolation else 0.0
                for loc, fc in fans.items())
    g_lo = g(m_min)
    if g_lo <= 0:
        raise SolveError(Message(code="no_operating_point_low",
                                 text="在风机曲线最小风量处系统阻力已大于风机静压（含浮力），无正向工作点",
                                 level="rejected"))
    g_hi = g(m_max)
    if g_hi > 0:
        raise SolveError(Message(code="no_operating_point_high",
                                 text="在风机曲线最大风量处风机静压仍大于系统阻力，工作点超出曲线范围；"
                                      "请补充曲线或显式允许外推",
                                 level="rejected"))
    m = brentq(g, m_min, m_max, xtol=1e-12)
    points = {}
    for loc, fc in fans.items():
        V = m / rho_at[loc] * 3600.0 / fc.fan.count
        points[fc.fan.id] = {
            "location": loc,
            "flow_per_fan_m3h": V,
            "static_pressure_Pa": fc.static_pressure(V),
            "fraction_of_curve_max_flow": V / float(fc.flow[-1]),
            "count": fc.fan.count,
        }
    return Hydraulics(m, dp_stack, resistance(m), points)


def solve(model: BoxModel) -> VentilationResult:
    res = VentilationResult(MODEL_ID, MODEL_VERSION, ResultStatus.success)
    amb = model.ambient
    T_amb = amb.temperature_C
    cp = model.air.cp_J_kgK
    res.T_ambient_C = T_amb
    try:
        fans = _running_fans(model) if model.ventilation.mode == "forced" else {}
    except SolveError as err:
        res.status, res.messages = ResultStatus.rejected, [err.msg]
        return res
    fan_power = sum(fc.power_W() for fc in fans.values())
    fan_heat = sum(fc.power_W() for fc in fans.values() if fc.fan.heat_to_zone)
    res.fan_power_W = fan_power
    UA = [(s.U_W_m2K * s.area_m2, sol_air_temperature(T_amb, s.solar_irradiance_W_m2, s.absorptance, s.h_out_W_m2K))
          for s in model.surfaces]
    UA_total = sum(u for u, _ in UA)
    res.UA_W_K = UA_total
    Q = model.internal_heat_W + fan_heat

    def f(Tz: float) -> float:
        h = _hydraulics(model, Tz, fans)
        return Q - h.m_dot * cp * (Tz - T_amb) - sum(u * (Tz - tsa) for u, tsa in UA)

    if model.ventilation.mode == "closed" and UA_total <= 0:
        res.status = ResultStatus.rejected
        res.messages.append(Message(code="no_heat_path", text="封闭箱体且壁面传热为零，无稳态解", level="rejected"))
        return res
    try:
        T_lo = T_amb
        f_lo = f(T_lo)
        if f_lo <= 0:
            Tz = T_amb if f_lo == 0 else brentq(f, min([T_amb] + [t for _, t in UA]) - 1.0, T_amb, xtol=1e-9)
        else:
            T_hi, f_hi = T_amb + 10.0, None
            while True:
                f_hi = f(T_hi)
                if f_hi < 0:
                    break
                T_hi = T_amb + 2.0 * (T_hi - T_amb)
                if T_hi - T_amb > 2000:
                    raise SolveError(Message(code="no_steady_state", text="空气温升超过2000 K仍未平衡，检查散热路径",
                                             level="failed"))
            Tz = brentq(f, T_lo, T_hi, xtol=1e-9)
        h = _hydraulics(model, Tz, fans)
    except SolveError as err:
        res.status = ResultStatus(err.msg.level) if err.msg.level in ("rejected", "failed") else ResultStatus.failed
        res.messages.append(err.msg)
        return res

    rho_in = air_density(T_amb, amb.pressure_Pa, model.air.R_J_kgK)
    rho_z = air_density(Tz, amb.pressure_Pa, model.air.R_J_kgK)
    q_vent = h.m_dot * cp * (Tz - T_amb)
    q_wall = sum(u * (Tz - tsa) for u, tsa in UA)
    q_solar = sum(u * (tsa - T_amb) for u, tsa in UA)
    res.T_zone_C = Tz
    res.air_rise_K = Tz - T_amb
    res.mass_flow_kg_s = h.m_dot
    res.flow_inlet_m3h = h.m_dot / rho_in * 3600.0
    res.flow_outlet_m3h = h.m_dot / rho_z * 3600.0
    res.dp_stack_Pa = h.dp_stack
    res.element_dp_Pa = h.element_dp
    res.fan_points = h.fan_points
    res.heat_balance_W = {
        "internal": model.internal_heat_W,
        "fan_heat_to_zone": fan_heat,
        "solar_equivalent": q_solar,
        "ventilation_out": q_vent,
        "walls_out_net": q_wall,
        "residual": Q - q_vent - q_wall,
    }
    msgs = res.messages
    msgs.append(Message(code="air_node_not_hotspot", text="结果为充分混合空气节点温度，不是设备热点温度", level="info"))
    status = ResultStatus.success
    if res.air_rise_K > 40:
        msgs.append(Message(code="large_air_rise", text=f"空气温升 {res.air_rise_K:.1f} K 较大，单节点混合假设误差增大"))
        status = worst_status(status, ResultStatus.warning)
    for fid, pt in h.fan_points.items():
        if pt["fraction_of_curve_max_flow"] < 0.3:
            msgs.append(Message(code="fan_near_shutoff",
                                text=f"风机 {fid} 工作在曲线最大风量的 {pt['fraction_of_curve_max_flow']:.0%}，接近关断区，宜复核风机选型"))
            status = worst_status(status, ResultStatus.warning)
        if pt["flow_per_fan_m3h"] > float(FanCurve.from_fan(next(f for f in model.fans if f.id == fid)).flow[-1]):
            msgs.append(Message(code="fan_curve_extrapolated", text=f"风机 {fid} 工作点位于外推段（用户允许）"))
            status = worst_status(status, ResultStatus.warning)
    if model.ventilation.mode == "forced" and h.dp_stack and not model.ventilation.stack_assists:
        msgs.append(Message(code="stack_opposes", text="浮力与风机方向相反（上进下出），已按削弱风机计算", level="info"))
    if model.ventilation.mode == "natural" and h.m_dot == 0:
        msgs.append(Message(code="no_natural_flow", text="无浮力驱动（箱内不高于室外），自然通风风量为零", level="info"))
    if abs(res.heat_balance_W["residual"]) > 1e-3 * max(Q, 1.0):
        msgs.append(Message(code="energy_residual", text="能量平衡残差偏大", level="failed"))
        status = ResultStatus.failed
    _ = rho_z
    res.status = status
    return res
