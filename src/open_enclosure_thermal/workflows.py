"""High-level workflows used by the CLI and the Python API."""

from __future__ import annotations

import datetime as _dt
import platform
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .identification import CurrentExponentFit, CurrentPoint, FirstOrderFit, fit_current_exponent, fit_first_order
from .io import InputError, load_project, read_run, sha256_file
from .quality import check_run
from .schema import BoxModel, Message, ResultStatus, TestProject, worst_status
from .study import StudyResult, run_study
from .test_analysis import RunAnalysis, RunComparison, analyse_run, compare_runs


@dataclass
class Provenance:
    software_version: str
    run_time: str
    python: str
    inputs: list[dict] = field(default_factory=list)  # {role, path(name), sha256, source_type}

    @classmethod
    def new(cls) -> "Provenance":
        return cls(__version__, _dt.datetime.now().astimezone().isoformat(timespec="seconds"), platform.python_version())

    def add(self, role: str, path: Path, source_type: str, sha: str | None = None) -> None:
        self.inputs.append({"role": role, "file": path.name, "sha256": sha or sha256_file(path),
                            "source_type": source_type})


@dataclass
class TestAnalysisReport:
    project: TestProject
    project_path: Path
    provenance: Provenance
    runs: list[RunAnalysis]
    fits: list[FirstOrderFit]
    exponents: list[CurrentExponentFit]
    comparisons: list[RunComparison]
    status: ResultStatus
    messages: list[Message] = field(default_factory=list)

    def run(self, rid: str) -> RunAnalysis:
        return next(r for r in self.runs if r.run.id == rid)


def analyze_test_project(path: str | Path, *, runs: list[str] | None = None,
                         compare: list[tuple[str, str]] | None = None) -> TestAnalysisReport:
    path = Path(path)
    project = load_project(path)
    if not isinstance(project, TestProject):
        raise InputError(f"{path}: not a test analysis project (kind={project.kind})")
    prov = Provenance.new()
    prov.add("project", path, project.project.source_type.value)
    selected = [r for r in project.test_runs if runs is None or r.id in runs]
    if runs:
        unknown = set(runs) - {r.id for r in project.test_runs}
        if unknown:
            raise InputError(f"unknown run ids: {sorted(unknown)}")
    analyses: list[RunAnalysis] = []
    seen: set[str] = set()
    for run in selected:
        data = read_run(project, run, path.parent)
        if str(data.path.resolve()) not in seen and data.path.resolve() != path.resolve():
            prov.add(f"data:{run.id}", data.path, project.project.source_type.value, data.sha256)
            seen.add(str(data.path.resolve()))
        qr = check_run(data, project.quality)
        analyses.append(analyse_run(project, qr))

    fits: list[FirstOrderFit] = []
    by_id = {a.run.id: a for a in analyses}
    if project.fits == "auto":
        requests = [(a.run.id, s, None, None, False) for a in analyses if a.has_rise for s in a.sensors
                    if s in a.curves]
    elif project.fits == "none":
        requests = []
    else:
        requests = [(f.run, f.sensor, f.start_min, f.end_min, f.fix_initial) for f in project.fits if f.run in by_id]
    for rid, sid, t0, t1, fix in requests:
        a = by_id[rid]
        if not a.has_rise or sid not in a.curves:
            continue
        validity = {}
        if a.run.load and a.run.load.current_A:
            validity["current_A"] = [a.run.load.current_A, a.run.load.current_A]
        if "ambient_C" in a.curves:
            validity["ambient_C"] = [float(a.curves["ambient_C"].min()), float(a.curves["ambient_C"].max())]
        fits.append(fit_first_order(a.curves["time_min"].to_numpy(), a.curves[sid].to_numpy(), run=rid, sensor=sid,
                                    start_min=t0, end_min=t1, fix_initial=fix, validity=validity))

    exponents: list[CurrentExponentFit] = []
    currents = {a.run.id: a.run.load.current_A for a in analyses if a.run.load and a.run.load.current_A}
    config = {a.run.id: a.run.configuration for a in analyses}
    for cfg in sorted(set(config.values())):
        runs_cfg = {rid for rid in currents if config[rid] == cfg}
        if len({currents[r] for r in runs_cfg}) < 2:
            continue
        for sid in sorted({f.sensor for f in fits if f.run in runs_cfg}):
            pts = [CurrentPoint(currents[f.run], f.value("rise_inf_K"), f.run) for f in fits
                   if f.sensor == sid and f.run in runs_cfg
                   and f.status in (ResultStatus.success, ResultStatus.warning)]
            e = fit_current_exponent(sid, pts)
            e.configuration = cfg
            exponents.append(e)

    comps: list[RunComparison] = []
    for a_id, b_id in compare or []:
        if a_id not in by_id or b_id not in by_id:
            raise InputError(f"comparison {a_id} vs {b_id}: run not analysed")
        comps.append(compare_runs(by_id[a_id], by_id[b_id]))

    status = worst_status(*(a.status for a in analyses)) if analyses else ResultStatus.rejected
    msgs: list[Message] = []
    if project.project.source_type.value == "enterprise_private":
        msgs.append(Message(code="private_data", text="数据来源为企业私有：报告不得用于公开示例或发布", level="info"))
    return TestAnalysisReport(project, path, prov, analyses, fits, exponents, comps, status, msgs)


@dataclass
class VentilationReport:
    model: BoxModel
    model_path: Path | None
    provenance: Provenance
    study: StudyResult


def ventilate_project(path_or_model: str | Path | BoxModel) -> VentilationReport:
    prov = Provenance.new()
    if isinstance(path_or_model, BoxModel):
        model, path = path_or_model, None
    else:
        path = Path(path_or_model)
        model = load_project(path)
        if not isinstance(model, BoxModel):
            raise InputError(f"{path}: not a box ventilation model (kind={model.kind})")
        prov.add("model", path, model.project.source_type.value)
    return VentilationReport(model, path, prov, run_study(model))
