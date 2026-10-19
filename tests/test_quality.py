import numpy as np
import pandas as pd

from open_enclosure_thermal.io import load_project, read_run, sha256_file
from open_enclosure_thermal.quality import check_run
from open_enclosure_thermal.schema import ResultStatus


def _base(n=120):
    t = np.arange(n, dtype=float)
    return pd.DataFrame({"time_min": t, "TA": 20 + 0.0 * t + np.random.default_rng(0).normal(0, 0.05, n),
                         "T1": 20 + 30 * (1 - np.exp(-t / 30))})


def _check(project_path, **_):
    pj = load_project(project_path)
    data = read_run(pj, pj.test_runs[0], project_path.parent)
    return pj, data, check_run(data, pj.quality)


def codes(q):
    return {m.code for m in q.messages}


def test_clean_data_passes(simple_project):
    _, _, q = _check(simple_project(_base()))
    assert q.status == ResultStatus.success, q.messages
    assert q.exclusions == []


def test_planted_defects_are_flagged(simple_project):
    df = _base()
    df.loc[40, "T1"] = np.nan
    df.loc[60, "T1"] += 12
    _, _, q = _check(simple_project(df))
    assert {"sensor_missing_values", "spike_flagged"} <= codes(q)
    assert q.flags.loc[60, "T1"] == "spike"
    assert q.exclusions == []  # flagged only, not removed
    assert q.table.loc[60, "T1"] == df.loc[60, "T1"]


def test_spike_exclusion_is_logged(simple_project):
    df = _base()
    df.loc[60, "T1"] += 12
    _, _, q = _check(simple_project(df, quality={"exclude_spikes": True}))
    assert "spike_excluded" in codes(q)
    assert len(q.exclusions) == 1 and q.exclusions[0].time_min == 60
    assert np.isnan(q.table.loc[60, "T1"])


def test_backwards_time_rejects_run(simple_project):
    df = _base()
    df.loc[50:, "time_min"] -= 30
    _, _, q = _check(simple_project(df))
    assert "time_backwards" in codes(q)
    assert q.status == ResultStatus.rejected


def test_duplicate_and_gap(simple_project):
    df = _base()
    df.loc[10, "time_min"] = df.loc[9, "time_min"]
    df.loc[80:, "time_min"] += 20
    _, _, q = _check(simple_project(df))
    assert {"time_duplicate", "time_gap"} <= codes(q)
    assert q.n_samples == len(df) - 1


def test_constant_and_out_of_range(simple_project):
    df = _base()
    df["T2"] = 25.0
    df.loc[5, "T1"] = 999.0
    _, _, q = _check(simple_project(df))
    assert {"constant_signal", "out_of_range"} <= codes(q)
    assert any(e.reason.startswith("超出") for e in q.exclusions)


def test_no_ambient_only_absolute(simple_project):
    _, _, q = _check(simple_project(_base(), ambient=()))
    assert "no_ambient" in codes(q)


def test_raw_file_unchanged_and_fingerprints(simple_project):
    df = _base()
    df.loc[60, "T1"] += 12
    p = simple_project(df, quality={"exclude_spikes": True})
    csv = p.parent / "R1.csv"
    before = sha256_file(csv)
    _, data, q = _check(p)
    assert sha256_file(csv) == before == q.raw_sha256
    assert q.clean_sha256 and q.clean_sha256 != q.raw_sha256
