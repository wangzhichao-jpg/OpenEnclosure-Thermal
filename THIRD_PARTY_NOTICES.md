# 第三方组件

运行依赖（通过 pip 安装，不随本仓库分发源码）：

| 包 | 许可 |
|---|---|
| numpy | BSD-3-Clause |
| pandas | BSD-3-Clause |
| scipy | BSD-3-Clause |
| matplotlib | Matplotlib License（PSF-based） |
| openpyxl | MIT |
| pydantic | MIT |
| PyYAML | MIT |

可选依赖：

| 包 | 许可 | 用途 |
|---|---|---|
| switchgear-thermal-model（Alliander） | MPL-2.0 | `adapters/switchgear_thermal_model.py` 参数对接与对照测试（需 Python ≥ 3.12）；本仓库不包含其源码 |
| pytest | MIT | 测试 |

OpenFOAM（GPL）计划在 v0.4 以外部调用和案例生成的方式接入，不链接其源码。
