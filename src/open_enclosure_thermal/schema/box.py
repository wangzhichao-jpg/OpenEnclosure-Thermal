"""Schema for the single-zone enclosure ventilation model (model M3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .common import ProjectInfo, StrictModel


class Ambient(StrictModel):
    temperature_C: float
    pressure_Pa: float = Field(default=101325.0, gt=50000)


class Surface(StrictModel):
    """Enclosure wall/roof. U is overall heat transfer coefficient air(in)->air(out).

    Solar gain uses the sol-air temperature T_sa = T_amb + absorptance * G / h_out.
    """

    id: str
    area_m2: float = Field(gt=0)
    U_W_m2K: float = Field(ge=0)
    solar_irradiance_W_m2: float = Field(default=0.0, ge=0)
    absorptance: float = Field(default=0.6, ge=0, le=1)
    h_out_W_m2K: float = Field(default=17.0, gt=0)


class Opening(StrictModel):
    """Opening, louvre or rain hood: dp = K * rho * v^2 / 2 with v = flow / area."""

    id: str
    kind: Literal["opening"] = "opening"
    location: Literal["inlet", "outlet"]
    area_m2: float = Field(gt=0, description="free (net) flow area")
    K: float | None = Field(default=None, gt=0, description="loss coefficient based on area_m2")
    Cd: float | None = Field(default=None, gt=0, le=1, description="discharge coefficient; K = 1/Cd^2")

    @model_validator(mode="after")
    def _k_or_cd(self) -> "Opening":
        if (self.K is None) == (self.Cd is None):
            raise ValueError(f"opening {self.id}: give exactly one of K or Cd")
        return self

    @property
    def loss_coefficient(self) -> float:
        return self.K if self.K is not None else 1.0 / self.Cd**2


class Filter(StrictModel):
    """Filter mat: dp = dp_rated * (V / V_rated)^n * loading_factor."""

    id: str
    kind: Literal["filter"] = "filter"
    location: Literal["inlet", "outlet"] = "inlet"
    rated_flow_m3h: float = Field(gt=0)
    rated_dp_Pa: float = Field(gt=0)
    exponent: float = Field(default=1.5, ge=1.0, le=2.0)
    loading_factor: float = Field(default=1.0, ge=1.0, description="dust loading multiplier on clean dp")


class FanPoint(StrictModel):
    flow_m3h: float = Field(ge=0)
    static_pressure_Pa: float = Field(ge=0)


class Fan(StrictModel):
    id: str
    location: Literal["inlet", "outlet"] = "outlet"
    curve: list[FanPoint] = Field(min_length=2, description="static pressure vs flow at rated speed")
    count: int = Field(default=1, ge=0, description="identical fans in parallel; 0 = switched off")
    speed_ratio: float = Field(default=1.0, gt=0, description="N/N_rated; affinity laws applied")
    power_W: float = Field(default=0.0, ge=0, description="electric power per fan at rated speed")
    heat_to_zone: bool = Field(default=False, description="add fan electric power to the zone heat load")
    allow_extrapolation: bool = False

    @field_validator("curve")
    @classmethod
    def _monotone(cls, pts: list[FanPoint]) -> list[FanPoint]:
        q = [p.flow_m3h for p in pts]
        p = [x.static_pressure_Pa for x in pts]
        if any(b <= a for a, b in zip(q, q[1:])):
            raise ValueError("fan curve flow values must be strictly increasing")
        if any(b > a for a, b in zip(p, p[1:])):
            raise ValueError("fan curve static pressure must be non-increasing with flow (stable branch only)")
        return pts


class Ventilation(StrictModel):
    mode: Literal["closed", "natural", "forced", "fixed_flow"]
    stack_height_m: float = Field(
        default=0.0, ge=0, description="vertical distance between inlet and outlet centres (buoyancy)"
    )
    stack_assists: bool = Field(
        default=True, description="True when air enters low and leaves high, so buoyancy adds to the fan"
    )
    fixed_flow_m3h: float | None = Field(default=None, gt=0, description="measured/prescribed inflow (fixed_flow)")


class AirProperties(StrictModel):
    cp_J_kgK: float = 1006.0
    R_J_kgK: float = 287.05


class Scenario(StrictModel):
    id: str
    description: str = ""
    set: dict[str, Any] = Field(
        default_factory=dict,
        description="overrides by path, e.g. 'internal_heat_W', 'fans[F1].count', 'elements[FL1].loading_factor'",
    )


class Sweep(StrictModel):
    id: str
    parameter: str
    values: list[float] = Field(min_length=2)
    base_scenario: str | None = None


class AirRiseLimit(StrictModel):
    value_K: float = Field(gt=0)
    source: str | None = None


class Study(StrictModel):
    scenarios: list[Scenario] = Field(default_factory=list)
    sweeps: list[Sweep] = Field(default_factory=list)
    rank_by: Literal["air_rise", "flow", "fan_power", "margin"] = "air_rise"
    air_rise_limit: AirRiseLimit | None = None


class BoxModel(StrictModel):
    schema_version: str = "0.1"
    kind: Literal["box_ventilation"] = "box_ventilation"
    project: ProjectInfo
    description: str = ""
    internal_heat_W: float = Field(ge=0)
    ambient: Ambient
    surfaces: list[Surface] = Field(default_factory=list)
    elements: list[Opening | Filter] = Field(default_factory=list)
    fans: list[Fan] = Field(default_factory=list)
    ventilation: Ventilation
    air: AirProperties = Field(default_factory=AirProperties)
    study: Study = Field(default_factory=Study)

    @model_validator(mode="after")
    def _consistency(self) -> "BoxModel":
        ids = [s.id for s in self.surfaces] + [e.id for e in self.elements] + [f.id for f in self.fans]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate ids: {sorted(dup)}")
        mode = self.ventilation.mode
        if mode == "fixed_flow" and self.ventilation.fixed_flow_m3h is None:
            raise ValueError("fixed_flow mode needs ventilation.fixed_flow_m3h")
        if mode in ("natural", "forced") and not self.elements:
            raise ValueError(f"{mode} ventilation needs at least one opening/filter element")
        if mode == "natural" and self.ventilation.stack_height_m <= 0:
            raise ValueError("natural ventilation needs stack_height_m > 0")
        if mode == "forced" and not any(f.count > 0 for f in self.fans):
            raise ValueError("forced ventilation needs at least one running fan")
        return self
