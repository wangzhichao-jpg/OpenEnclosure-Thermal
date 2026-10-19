import numpy as np
import pandas as pd
import pytest
import yaml

from open_enclosure_thermal.io import InputError, load_project, model_to_mapping, read_run, save_project
from open_enclosure_thermal.schema import BoxModel, TestProject


def test_examples_load(examples):
    pj = load_project(examples["test"])
    assert isinstance(pj, TestProject)
    assert pj.project.source_type.value == "synthetic"
    box = load_project(examples["box"])
    assert isinstance(box, BoxModel)


def test_three_column_styles_and_time_formats(examples):
    pj = load_project(examples["test"])
    base = examples["test"].parent
    lengths = set()
    for run in pj.test_runs:  # R1 datetime + mapped Chinese headers, R2 minutes + ids, R3/R4 seconds
        data = read_run(pj, run, base)
        assert data.sensors and "TA1" in data.sensors
        assert data.table["time_min"].iloc[0] == 0
        assert data.table["time_min"].iloc[-1] == pytest.approx(480.0)
        lengths.add(len(data.table))
    assert lengths == {481}


def test_unknown_key_rejected_with_location(tmp_path, examples):
    data = yaml.safe_load(examples["box"].read_text(encoding="utf-8"))
    data["elements"][0]["areaa_m2"] = 1
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(InputError) as err:
        load_project(p)
    assert "elements" in str(err.value)


def test_missing_required_field_reports_location(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"kind": "test_analysis", "project": {"name": "x"}, "sensors": [], "test_runs": []}),
                 encoding="utf-8")
    with pytest.raises(InputError) as err:
        load_project(p)
    assert "project.source_type" in str(err.value)


def test_unknown_sensor_reference_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({
        "kind": "test_analysis", "project": {"name": "x", "source_type": "synthetic"},
        "sensors": [{"id": "T1"}], "test_runs": [{"id": "R1", "file": "a.csv", "ambient": ["TA"]}]}), encoding="utf-8")
    with pytest.raises(InputError, match="unknown sensor"):
        load_project(p)


@pytest.mark.parametrize("unit,offset", [("K", 273.15), ("degF", None)])
def test_temperature_units(simple_project, unit, offset):
    t = np.arange(0, 30.0)
    c = 20 + 0.1 * t
    raw = c + 273.15 if unit == "K" else c * 9 / 5 + 32
    p = simple_project(pd.DataFrame({"time_min": t, "TA": raw, "T1": raw + (5 if unit == "K" else 9)}))
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["test_runs"][0]["temperature_unit"] = unit
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    pj = load_project(p)
    d = read_run(pj, pj.test_runs[0], p.parent)
    np.testing.assert_allclose(d.table["TA"], c, atol=1e-9)
    np.testing.assert_allclose(d.table["T1"] - d.table["TA"], 5.0, atol=1e-9)


def test_box_excel_roundtrip_is_lossless(tmp_path, examples):
    box = load_project(examples["box"])
    x = save_project(box, tmp_path / "box.xlsx")
    again = load_project(x)
    assert model_to_mapping(again) == model_to_mapping(box)
    y = save_project(again, tmp_path / "box.yaml")
    assert model_to_mapping(load_project(y)) == model_to_mapping(box)


def test_test_project_excel_roundtrip(tmp_path, examples):
    pj = load_project(examples["test"])
    x = save_project(pj, tmp_path / "pj.xlsx")
    again = load_project(x)
    a, b = model_to_mapping(pj), model_to_mapping(again)
    assert a == b


def test_excel_template_with_embedded_data(tmp_path):
    from open_enclosure_thermal.cli import main

    out = tmp_path / "tpl.xlsx"
    assert main(["template", "test", str(out)]) == 0
    pj = load_project(out)
    assert {r.id for r in pj.test_runs} == {"R1", "R2"}
    d = read_run(pj, pj.test_runs[0], out.parent)
    assert d.path.name == "tpl.xlsx" and len(d.table) == 361 and "T01" in d.sensors
