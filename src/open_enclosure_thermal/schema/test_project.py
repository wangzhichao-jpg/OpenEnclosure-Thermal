"""Schema for temperature-rise test analysis projects."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ProjectInfo, StrictModel

SensorCategory = Literal["conductor", "contact", "terminal", "air", "wall", "ambient", "other"]


class Sensor(StrictModel):
    id: str
    name: str = ""
    category: SensorCategory = "other"
    location: str = ""


class TimeSpec(StrictModel):
    column: str = "time"
    kind: Literal["seconds", "minutes", "hours", "datetime"] = "minutes"
    datetime_format: str | None = Field(default=None, description="strftime format; None = automatic parsing")


class LoadSpec(StrictModel):
    current_A: float | None = Field(default=None, gt=0)
    power_W: float | None = Field(default=None, ge=0)
    description: str = ""


class TestRun(StrictModel):
    __test__ = False  # not a pytest class
    id: str
    file: str = Field(description="CSV/XLSX path, relative to the project file")
    sheet: str | int | None = None
    time: TimeSpec = Field(default_factory=TimeSpec)
    columns: dict[str, str] = Field(
        default_factory=dict,
        description="sensor id -> raw column name; sensors not listed are looked up by id",
    )
    temperature_unit: Literal["degC", "K", "degF"] = "degC"
    ambient: list[str] = Field(default_factory=list, description="sensor ids averaged as ambient reference")
    load: LoadSpec | None = None
    description: str = ""
    configuration: str = Field(
        default="default",
        description="product/ventilation configuration; current-exponent fits only pool runs of one configuration",
    )
    align_offset_min: float = Field(default=0.0, description="shift subtracted from time when comparing runs")


class StabilityCriterion(StrictModel):
    """User-defined steady-state criterion. No standard criterion is built in."""

    window_min: float = Field(default=60.0, gt=0)
    max_change_K: float | None = Field(default=1.0, gt=0, description="max (max-min) of rise within window")
    max_rate_K_per_h: float | None = Field(default=None, gt=0, description="max |linear slope| within window")
    sensors: list[str] | Literal["all"] = "all"
    source: str = "用户临时设定 / user-defined"

    @model_validator(mode="after")
    def _at_least_one(self) -> "StabilityCriterion":
        if self.max_change_K is None and self.max_rate_K_per_h is None:
            raise ValueError("stability criterion needs max_change_K and/or max_rate_K_per_h")
        return self


class QualityConfig(StrictModel):
    min_C: float = -50.0
    max_C: float = 300.0
    spike_K: float = Field(default=5.0, gt=0, description="deviation from rolling median flagged as spike")
    gap_factor: float = Field(default=3.0, gt=1, description="interval > factor x median interval flagged")
    exclude_spikes: bool = False


class Limit(StrictModel):
    id: str
    quantity: Literal["temperature_rise", "temperature"]
    sensor: str = Field(description="sensor id, or '*' for all non-ambient sensors")
    value: float
    source: str | None = Field(default=None, description="where the value comes from; None = 用户临时设定")


class FitRequest(StrictModel):
    run: str
    sensor: str
    start_min: float | None = None
    end_min: float | None = None
    fix_initial: bool = False


class TestProject(StrictModel):
    __test__ = False  # not a pytest class
    schema_version: str = "0.1"
    kind: Literal["test_analysis"] = "test_analysis"
    project: ProjectInfo
    sensors: list[Sensor]
    test_runs: list[TestRun]
    stability: StabilityCriterion = Field(default_factory=StabilityCriterion)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    limits: list[Limit] = Field(default_factory=list)
    final_window_min: float = Field(default=10.0, gt=0, description="window used for the 'final rise' average")
    fits: list[FitRequest] | Literal["auto", "none"] = Field(
        default="auto", description="'auto' fits every non-ambient sensor of every run"
    )

    @field_validator("sensors")
    @classmethod
    def _unique_sensors(cls, v: list[Sensor]) -> list[Sensor]:
        ids = [s.id for s in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate sensor ids: {sorted(dup)}")
        return v

    @model_validator(mode="after")
    def _references(self) -> "TestProject":
        ids = {s.id for s in self.sensors}
        run_ids = [r.id for r in self.test_runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("duplicate test run ids")
        for r in self.test_runs:
            unknown = (set(r.columns) | set(r.ambient)) - ids
            if unknown:
                raise ValueError(f"run {r.id}: unknown sensor ids {sorted(unknown)}")
        for lim in self.limits:
            if lim.sensor != "*" and lim.sensor not in ids:
                raise ValueError(f"limit {lim.id}: unknown sensor {lim.sensor}")
        if isinstance(self.stability.sensors, list):
            unknown = set(self.stability.sensors) - ids
            if unknown:
                raise ValueError(f"stability: unknown sensor ids {sorted(unknown)}")
        if isinstance(self.fits, list):
            for f in self.fits:
                if f.run not in run_ids or f.sensor not in ids:
                    raise ValueError(f"fit request {f.run}/{f.sensor} refers to unknown run or sensor")
        return self

    def sensor(self, sid: str) -> Sensor:
        return next(s for s in self.sensors if s.id == sid)

    def run(self, rid: str) -> TestRun:
        for r in self.test_runs:
            if r.id == rid:
                return r
        raise KeyError(f"unknown run {rid}")
