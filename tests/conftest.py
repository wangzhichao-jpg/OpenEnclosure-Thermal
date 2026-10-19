from pathlib import Path

import pytest

from open_enclosure_thermal.synthetic import write_box_example, write_switchgear_example


@pytest.fixture(scope="session")
def examples(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("examples")
    return {
        "test": write_switchgear_example(root / "switchgear_test"),
        "box": write_box_example(root / "box_ventilation"),
    }


@pytest.fixture()
def simple_project(tmp_path):
    """Factory: write a CSV + minimal project and return the project path."""
    import pandas as pd
    import yaml

    def make(df: "pd.DataFrame", *, ambient=("TA",), stability=None, quality=None, limits=None, time=None,
             extra_runs=None) -> Path:
        df.to_csv(tmp_path / "R1.csv", index=False)
        sensors = [{"id": c, "category": "ambient" if c in ambient else "other"} for c in df.columns
                   if c not in ("time_min", "time_s", "time")]
        pj = {
            "kind": "test_analysis",
            "project": {"name": "t", "source_type": "synthetic"},
            "sensors": sensors,
            "test_runs": [{"id": "R1", "file": "R1.csv", "time": time or {"column": "time_min", "kind": "minutes"},
                           "ambient": list(ambient), "load": {"current_A": 1000}}] + (extra_runs or []),
            "fits": "none",
        }
        if stability:
            pj["stability"] = stability
        if quality:
            pj["quality"] = quality
        if limits:
            pj["limits"] = limits
        p = tmp_path / "project.yaml"
        p.write_text(yaml.safe_dump(pj, allow_unicode=True), encoding="utf-8")
        return p

    return make
