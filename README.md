# OpenEnclosure-Thermal: Switchgear & Substation Ventilation & Temperature-Rise Analysis

Open-source thermal analysis and ventilation sizing tools for electrical switchgear, prefabricated substations, and low-voltage enclosure systems.

English | [中文版 (Simplified Chinese)](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/openenclosure-thermal?label=PyPI%20(pre-release))](https://pypi.org/project/openenclosure-thermal/)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12-blue)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-v0.1%20development-orange)](CHANGELOG.md)

[GitHub Repository](https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal) · [Gitee Mirror](https://gitee.com/bigtaurus/OpenEnclosure-Thermal)

![OpenEnclosure-Thermal: Application Scope and Open Benchmark Model Box](docs/images/hero.png)

## Intended Audience and Use Cases

- **Electrical Switchgear and Substation Design Engineers**: Sizing enclosure louvers, filters, and forced ventilation fans for switchgear lineups and package substations.
- **Electrical Test and Metrology Engineers**: Automating the ingestion, quality screening, and analysis of type-test temperature-rise curves, identifying sensor-specific thermal time constants and current exponents.
- **Data Center and Outdoor Enclosure Thermal Engineers**: Estimating indoor enclosure air equilibrium temperature rises under combined internal heat dissipation and external sol-air radiation.
- **Thermal and CFD Engineers**: Rapidly narrowing design exploration spaces using lumped-parameter models prior to high-fidelity CFD simulations.

## Key Features

- **Temperature-Rise Test Analyser (`oet analyze-test`)**:
  - Data ingestion: Supports CSV/Excel files, original column-to-sensor mapping, and automatic handling of time stamps and engineering units (°C/K/°F).
  - Data quality screening: Flags time inversions, duplicate rows, constant sensor drift, outliers, and range violations without altering raw source data; every exclusion is documented in an audit log.
  - Parameter identification: Identifies steady-state rise Δθ∞, thermal time constant τ, and parameter covariance based on first-order thermal models; issues identifiability warnings or rejections on under-developed curves.
  - Current-exponent fitting: Derives current-scaling exponent n when ≥2 current levels are provided, rejecting unwarranted extrapolations.
  - Traceable reporting: Generates reproducible HTML, Excel, and JSON reports with input SHA-256 hashes and explicit user stability criteria.
- **Single-Zone Enclosure Ventilation Calculator (`oet ventilate`)**:
  - Ventilation regimes: Supports sealed enclosures, natural buoyancy-driven airflow, and fan-forced ventilation (with aiding or opposing buoyancy).
  - Flow-resistance coupling: Computes intersection points of fan performance curves and duct/grille flow resistances, accommodating parallel fans and affinity-law scaling.
  - Envelope heat transfer: Incorporates directional surface U-values and sol-air temperatures accounting for solar irradiation.
  - Scenario comparison & parametric sweeps: Ranks candidate designs by air temperature rise, airflow rate, and fan power, providing sensitivity analysis across openings, dust loading, and ambient conditions.

## Quick Start

### 1. Installation

OpenEnclosure-Thermal is available on PyPI (current pre-release: `0.1.0.dev0`). Python 3.11 or 3.12 is recommended:

```bash
# Method A: Install directly from PyPI
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple openenclosure-thermal --pre

# Method B: Install in editable development mode from source
git clone https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal.git
cd OpenEnclosure-Thermal
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .
```

### 2. Run CLI Examples

```bash
# Generate synthetic demonstration case
oet init demo

# Run temperature-rise analysis and generate comparison reports
oet analyze-test demo/switchgear_test/switchgear_test.yaml --compare R1:R2 -o out_test

# Sizing single-zone enclosure ventilation
oet ventilate demo/box_ventilation/box_ventilation.yaml -o out_box

# Generate standard spreadsheet templates
oet template test test_template.xlsx
oet template box  box_template.xlsx
```

![Synthetic Case Output: Temperature-Rise Curve, Fan-System Operating Point, and Scenario Ranking](docs/images/overview.png)

## Validation and Current Status

- **Verification Status**:
  - L0 Software Verification: 61 automated tests covering analytical solutions (closed box, fixed flow, linear fan-resistance intersection, sol-air temperature), parameter recovery from synthetic tests, and physical monotonicity.
  - Upstream Benchmark: Reproduces benchmark cases against the open-source `switchgear-thermal-model` project under identical baseline parameters.
  - L2 Open Benchmark: An electric heating benchmark model box experimental campaign (in the environmental chamber of Jianke Environment and Energy Technology Co., Ltd.) is planned; see [Benchmark Plan](docs/开放模型箱基准试验方案.md).
- **Research Preview Disclaimer**:
  - This toolkit is a research preview (v0.1 development version). Computed results reflect **well-mixed interior bulk air temperature**, not conductor or busbar hotspot temperatures.
  - This software does not substitute for accredited type-testing, formal certification, or structural safety evaluations; normative limit criteria are user-supplied.
  - It does not calculate electromagnetic losses from raw conductor geometry; heat dissipation must be user-provided or extracted from test data.

## Documentation, Citation, and License

- **Documentation**: See [Quickstart Guide](docs/快速上手.md), [Model Formulation](docs/模型说明.md), and [Data Collaboration Guide](docs/数据与合作.md).
- **Citation**: If referencing this software in academic papers or technical reports, please cite via [CITATION.cff](CITATION.cff).
- **License**: Released under the [MIT License](LICENSE), Copyright (c) 2026 Jianke Environment and Energy Technology Co., Ltd. (建科环能科技有限公司) and contributors. Third-party notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Author & Contact**:
  - Project Lead: Zhichao Wang (ORCID: [0000-0001-6662-835X](https://orcid.org/0000-0001-6662-835X))
  - Contact Email: wangzhichao@chinaibee.com
  - Inquiries & Issues: Community issues and feature requests are welcomed via [GitHub Issues](https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal/issues) and [Gitee Repository](https://gitee.com/bigtaurus/OpenEnclosure-Thermal).
