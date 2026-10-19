"""Command line interface: ``oet``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import DISCLAIMER_ZH, __version__

STATUS_ZH = {"success": "成功", "warning": "有警告", "rejected": "拒绝给出结论", "failed": "失败"}


def _print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc))


def _formats(s: str) -> tuple[str, ...]:
    out = tuple(x.strip().lower() for x in s.split(",") if x.strip())
    bad = set(out) - {"html", "xlsx", "json"}
    if bad:
        raise argparse.ArgumentTypeError(f"unknown format(s): {sorted(bad)}")
    return out


def cmd_init(args) -> int:
    from .synthetic import write_box_example, write_switchgear_example

    out = Path(args.directory)
    written = []
    if args.example in ("all", "switchgear-test"):
        written.append(write_switchgear_example(out / "switchgear_test"))
    if args.example in ("all", "box-ventilation"):
        written.append(write_box_example(out / "box_ventilation"))
    for p in written:
        _print(f"已生成示例 / example written: {p}")
    _print("示例均为合成数据，不代表任何企业产品。")
    return 0


def cmd_template(args) -> int:
    from .io import model_from_mapping, save_project
    from .synthetic import BOX_EXAMPLE, switchgear_run, write_switchgear_example

    out = Path(args.output)
    if args.kind == "box":
        save_project(model_from_mapping(dict(BOX_EXAMPLE)), out)
    else:
        import tempfile

        from .io import load_project

        with tempfile.TemporaryDirectory() as tmp:
            pj = load_project(write_switchgear_example(tmp))
        runs = [r.model_copy(update={"file": "", "columns": {}, "time": r.time.model_copy(
            update={"column": "time_min", "kind": "minutes"})}) for r in pj.test_runs[:2]]
        pj = pj.model_copy(update={"test_runs": runs})
        frames = {"R1": switchgear_run(1250, seed=1, hours=6), "R2": switchgear_run(1250, seed=2, hours=6,
                                                                                    improvement=0.12)}
        save_project(pj, out, data_frames=frames)
    _print(f"模板已生成 / template written: {out}")
    return 0


def cmd_validate(args) -> int:
    from .io import load_project, read_run
    from .schema import TestProject

    model = load_project(args.file)
    _print(f"格式有效 / valid: kind={model.kind}, project={model.project.name}, source={model.project.source_type.value}")
    if isinstance(model, TestProject):
        for run in model.test_runs:
            data = read_run(model, run, Path(args.file).parent)
            miss = f"，缺列 {data.missing_sensors}" if data.missing_sensors else ""
            _print(f"  试验 {run.id}: {len(data.table)} 行，测点 {len(data.sensors)} 个{miss}")
    return 0


def cmd_convert(args) -> int:
    from .io import load_project, save_project

    model = load_project(args.input)
    save_project(model, args.output)
    _print(f"已转换 / converted: {args.input} -> {args.output}")
    return 0


def cmd_analyze(args) -> int:
    from .reporting import write_report
    from .workflows import analyze_test_project

    compare = []
    for c in args.compare or []:
        if ":" not in c:
            raise SystemExit(f"--compare expects A:B, got {c}")
        compare.append(tuple(c.split(":", 1)))
    rep = analyze_test_project(args.file, runs=args.runs, compare=compare)
    out = Path(args.output or Path(args.file).with_suffix("").name + "_report")
    files = write_report(rep, out, args.format)
    _print(f"总体状态 / status: {STATUS_ZH[rep.status.value]}")
    for a in rep.runs:
        top = max(a.stats, key=lambda s: s.max_rise_K or -1e9, default=None)
        hot = f"最大温升 {top.sensor} {top.max_rise_K:.2f} K" if top and top.max_rise_K is not None else ""
        stable = "" if a.run_stable is None else ("；满足用户稳定判据" if a.run_stable else "；未全部满足用户稳定判据")
        _print(f"  {a.run.id}: {STATUS_ZH[a.status.value]} {hot}{stable}")
    n_ok = sum(f.status.value in ("success", "warning") for f in rep.fits)
    if rep.fits:
        _print(f"  参数识别：{n_ok}/{len(rep.fits)} 个可用")
    for e in rep.exponents:
        n = f"n={e.exponent_n.value:.3f}" if e.exponent_n else "未识别"
        _print(f"  电流指数 {e.configuration}/{e.sensor}: {n} [{STATUS_ZH[e.status.value]}]")
    for f in files:
        _print(f"报告 / report: {f}")
    _print(DISCLAIMER_ZH)
    return 0


def cmd_fit(args) -> int:
    from .identification import fit_first_order
    from .workflows import analyze_test_project

    rep = analyze_test_project(args.file, runs=[args.run])
    a = rep.run(args.run)
    if args.sensor not in a.curves:
        raise SystemExit(f"sensor {args.sensor} has no rise curve in run {args.run}")
    fit = fit_first_order(a.curves["time_min"].to_numpy(), a.curves[args.sensor].to_numpy(), run=args.run,
                          sensor=args.sensor, start_min=args.start, end_min=args.end, fix_initial=args.fix_initial)
    _print(json.dumps(fit.as_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_ventilate(args) -> int:
    from .reporting import write_report
    from .workflows import ventilate_project

    rep = ventilate_project(args.file)
    b = rep.study.base.result
    out = Path(args.output or Path(args.file).with_suffix("").name + "_report")
    files = write_report(rep, out, args.format)
    _print(f"基准方案 / base: {STATUS_ZH[b.status.value]}")
    if b.ok():
        _print(f"  空气节点 {b.T_zone_C:.2f} °C，温升 {b.air_rise_K:.2f} K，进风量 {b.flow_inlet_m3h:.1f} m³/h")
    for m in b.messages:
        if m.level in ("rejected", "failed", "warning"):
            _print(f"  [{m.level}] {m.text}")
    _print(f"方案比较（{rep.study.rank_rule}）：")
    for c in sorted(rep.study.cases, key=lambda c: (c.rank is None, c.rank or 0)):
        r = c.result
        val = f"温升 {r.air_rise_K:.2f} K，风量 {r.flow_inlet_m3h:.0f} m³/h" if r.ok() else c.row()["reason"]
        _print(f"  {c.rank or '-'}. {c.case_id}: {val}")
    for f in files:
        _print(f"报告 / report: {f}")
    _print(DISCLAIMER_ZH)
    return 0


def cmd_check_release(args) -> int:
    from .release_check import check_tree

    deny = []
    if args.denylist:
        deny = [ln.strip() for ln in Path(args.denylist).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
    findings = check_tree(args.directory, deny)
    if not findings:
        _print("发布检查通过 / release check passed")
        return 0
    for f in findings:
        _print(f"[{f.rule}] {f.path}: {f.detail}")
    _print(f"发布检查未通过：{len(findings)} 项 / release check failed")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oet", description="OpenEnclosure-Thermal：温升试验分析与箱体通风快算")
    p.add_argument("--version", action="version", version=f"OpenEnclosure-Thermal {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="生成合成示例")
    s.add_argument("directory")
    s.add_argument("--example", choices=["all", "switchgear-test", "box-ventilation"], default="all")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("template", help="生成中文Excel模板")
    s.add_argument("kind", choices=["test", "box"])
    s.add_argument("output")
    s.set_defaults(func=cmd_template)

    s = sub.add_parser("validate", help="检查项目文件")
    s.add_argument("file")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("convert", help="YAML/JSON/Excel 互转")
    s.add_argument("input")
    s.add_argument("output")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("analyze-test", help="温升试验分析并生成报告")
    s.add_argument("file")
    s.add_argument("-o", "--output")
    s.add_argument("--runs", nargs="+")
    s.add_argument("--compare", nargs="+", metavar="A:B", help="对比试验，如 R1:R2")
    s.add_argument("--format", type=_formats, default=("html", "xlsx", "json"))
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("fit", help="单测点一阶响应拟合")
    s.add_argument("file")
    s.add_argument("--run", required=True)
    s.add_argument("--sensor", required=True)
    s.add_argument("--start", type=float)
    s.add_argument("--end", type=float)
    s.add_argument("--fix-initial", action="store_true")
    s.set_defaults(func=cmd_fit)

    s = sub.add_parser("ventilate", help="单室箱体通风散热快算与方案比较")
    s.add_argument("file")
    s.add_argument("-o", "--output")
    s.add_argument("--format", type=_formats, default=("html", "xlsx", "json"))
    s.set_defaults(func=cmd_ventilate)

    s = sub.add_parser("check-release", help="公开发布前检查私有数据、本机路径和凭据")
    s.add_argument("directory")
    s.add_argument("--denylist", help="本地敏感词表（每行一个，不要提交到仓库）")
    s.set_defaults(func=cmd_check_release)
    return p


def main(argv: list[str] | None = None) -> int:
    from .io import InputError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InputError as err:
        _print(f"输入错误 / input error:\n{err}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
