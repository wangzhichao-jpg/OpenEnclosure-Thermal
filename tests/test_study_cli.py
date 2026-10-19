import json

import pytest
import yaml

from open_enclosure_thermal.cli import main
from open_enclosure_thermal.io import model_from_mapping
from open_enclosure_thermal.release_check import check_tree
from open_enclosure_thermal.study import OverrideError, apply_overrides, run_study
from open_enclosure_thermal.synthetic import BOX_EXAMPLE


def test_override_paths():
    m = model_from_mapping(BOX_EXAMPLE)
    m2 = apply_overrides(m, {"fans[F1].count": 2, "elements[FL1].loading_factor": 3,
                             "surfaces[*].U_W_m2K": 4.0, "ambient.temperature_C": 40})
    assert m2.fans[0].count == 2 and m2.elements[1].loading_factor == 3
    assert all(s.U_W_m2K == 4.0 for s in m2.surfaces) and m2.ambient.temperature_C == 40
    with pytest.raises(OverrideError):
        apply_overrides(m, {"fans[NOPE].count": 2})
    with pytest.raises(OverrideError):
        apply_overrides(m, {"elements[FL1].loading_factor": 0.5})  # schema: >= 1


def test_study_ranking_keeps_failures():
    data = yaml.safe_load(yaml.safe_dump(BOX_EXAMPLE))
    data["study"]["scenarios"].append({"id": "BAD", "set": {"fans[F1].allow_extrapolation": False,
                                                           "elements[IN].area_m2": 5.0, "elements[OUT].area_m2": 5.0,
                                                           "elements[FL1].rated_dp_Pa": 1.0}})
    res = run_study(model_from_mapping(data))
    by = {c.case_id: c for c in res.cases}
    assert by["BAD"].rank is None and by["BAD"].row()["reason"]
    ranked = sorted([c for c in res.cases if c.rank], key=lambda c: c.rank)
    margins = [c.margin_K for c in ranked]
    assert margins == sorted(margins, reverse=True)
    assert by["S3_two_fans"].rank == 1
    assert by["S6_closed"].margin_K < 0  # closed box exceeds the demo limit
    assert len(res.sweeps["filter_loading"]) == 6
    assert res.sensitivity and all(r["status"] in ("success", "warning") for r in res.sensitivity)


def test_cli_end_to_end(tmp_path, capsys):
    ex = tmp_path / "ex"
    assert main(["init", str(ex)]) == 0
    pj = ex / "switchgear_test" / "switchgear_test.yaml"
    assert main(["validate", str(pj)]) == 0
    out = tmp_path / "rep"
    assert main(["analyze-test", str(pj), "-o", str(out), "--compare", "R1:R2"]) == 0
    for suffix in ("html", "xlsx", "json"):
        assert (out / f"test_report.{suffix}").stat().st_size > 1000
    rep = json.loads((out / "test_report.json").read_text(encoding="utf-8"))
    assert rep["provenance"]["inputs"][0]["sha256"]
    assert "不代替" in rep["disclaimer"]
    html = (out / "test_report.html").read_text(encoding="utf-8")
    assert "用户设定" in html and "合格" not in html.replace("不判定试验合格与否", "")

    box = ex / "box_ventilation" / "box_ventilation.yaml"
    out2 = tmp_path / "vrep"
    assert main(["ventilate", str(box), "-o", str(out2)]) == 0
    v = json.loads((out2 / "ventilation_report.json").read_text(encoding="utf-8"))
    assert v["base"]["status"] == "success" and len(v["cases"]) == 8

    assert main(["fit", str(pj), "--run", "R3", "--sensor", "T01"]) == 0
    assert '"tau_min"' in capsys.readouterr().out


def test_cli_box_template_runs(tmp_path):
    x = tmp_path / "box.xlsx"
    assert main(["template", "box", str(x)]) == 0
    assert main(["ventilate", str(x), "-o", str(tmp_path / "o"), "--format", "json"]) == 0
    y = tmp_path / "box.yaml"
    assert main(["convert", str(x), str(y)]) == 0


def test_cli_input_error_exit_code(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("kind: box_ventilation\nproject: {name: a}\n", encoding="utf-8")
    assert main(["validate", str(p)]) == 2


def test_release_check(tmp_path, examples):
    assert check_tree(examples["test"].parent) == []
    bad = tmp_path / "bad"
    bad.mkdir()
    data = yaml.safe_load(examples["box"].read_text(encoding="utf-8"))
    data["project"]["source_type"] = "enterprise_private"
    (bad / "p.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    drive_path = "D" + ":\\" + "Users\\someone\\secret.csv"
    (bad / "notes.md").write_text(f"see {drive_path}\nACME Corp internal", encoding="utf-8")
    (bad / "private_data").mkdir()
    (bad / "private_data" / "x.csv").write_text("1", encoding="utf-8")
    rules = {f.rule for f in check_tree(bad, denylist=["ACME Corp"])}
    assert {"enterprise_private_data", "local_windows_user_path", "private_directory", "denylisted_term"} <= rules
    assert main(["check-release", str(bad)]) == 1


def test_committed_examples_run(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ex = root / "examples"
    assert main(["analyze-test", str(ex / "switchgear_test" / "switchgear_test.yaml"), "-o", str(tmp_path / "a"),
                 "--format", "json"]) == 0
    assert main(["ventilate", str(ex / "box_ventilation" / "box_ventilation.xlsx"), "-o", str(tmp_path / "b"),
                 "--format", "json"]) == 0
    assert check_tree(root) == []
