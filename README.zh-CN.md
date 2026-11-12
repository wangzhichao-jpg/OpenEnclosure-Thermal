# OpenEnclosure-Thermal: 开关柜与箱变温升分析及通风散热计算工具

开关柜、箱变与低压成套设备的温升试验分析和柜体通风散热开源工程工具。

简体中文 | [English](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/openenclosure-thermal?label=PyPI%20(pre-release))](https://pypi.org/project/openenclosure-thermal/)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12-blue)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-v0.1%20development-orange)](CHANGELOG.md)

[GitHub 仓库](https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal) · [Gitee 仓库](https://gitee.com/bigtaurus/OpenEnclosure-Thermal)

![OpenEnclosure-Thermal：应用对象与开放基准模型箱](docs/images/hero.png)

## 适用人群与用途

- **电气成套与变压器设计工程师**：开关柜、箱式变电站、预装式变电站通风口开孔面积、过滤网与风机选型快速比选。
- **电气试验与质检分析人员**：自动化清洗、对齐与分析型式试验温升曲线，辨识单测点热时间常数与电流—温升指数。
- **数据中心与户外柜体热设计工程师**：综合考虑外部日照辐射（sol-air 综合温度）与发热元件功耗下的柜内空气平衡温升。
- **热工与流体仿真工程师**：通过一维集中参数热平衡模型快速缩小设计空间，与高精 CFD 仿真形成分级验证链。

## 主要功能

- **温升试验分析器（`oet analyze-test`）**：
  - 数据导入：支持 CSV/Excel 导入，原始列名映射，自动解析日期时间格式与单位（°C/K/°F）。
  - 数据质量检查：自动检测倒序、重复、恒定死值、跳点与超量程，异常点全部记录在审计日志中，不改动原始文件。
  - 动态参数识别：基于一阶响应模型辨识各测点稳态温升 Δθ∞、热时间常数 τ 及参数标准误差；未充分发展曲线自动拒绝或预警。
  - 电流—温升指数拟合：同一配置 ≥2 个电流水平时拟合指数 n，默认不外推。
  - 试验对比与报告：支持改进前后测点对比、曲线对齐，导出包含 SHA-256 校验的 HTML、Excel 与 JSON 溯源报告。
- **单室箱体通风快算（`oet ventilate`）**：
  - 通风模式：支持自然通风（热浮力驱动）、机械强制通风（含浮力辅助或阻碍）与封闭箱体。
  - 阻力与风机耦合：风机特性曲线与系统流动阻力求交点，支持并联风机及转速比相似律换算，默认不外推曲线。
  - 壁面散热与日照：计入箱壁各朝向 U 值与日照辐射综合温度（sol-air）。
  - 方案比选与敏感性扫描：多方案按空气温升、风量与风机能耗排序；提供开口面积、过滤网积尘阻力、环境温度等单因素敏感性分析。

## 快速开始

### 1. 安装

软件已发布至 PyPI（当前为开发预览版 `0.1.0.dev0`），推荐使用 Python 3.11 或 3.12：

```bash
# 方式 A：从 PyPI 直接安装
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple openenclosure-thermal --pre

# 方式 B：从本地源码以可编辑模式安装
git clone https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal.git
cd OpenEnclosure-Thermal
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .
```

### 2. 快速运行命令行示例

```bash
# 生成合成演示示例
oet init demo

# 运行温升试验分析并生成对比报告
oet analyze-test demo/switchgear_test/switchgear_test.yaml --compare R1:R2 -o out_test

# 运行单室箱体通风快算
oet ventilate demo/box_ventilation/box_ventilation.yaml -o out_box

# 导出中文模板
oet template test 温升试验模板.xlsx
oet template box  箱体通风模板.xlsx
```

![合成算例输出：温升曲线与参数识别、风机—系统工作点、方案比较](docs/images/overview.png)

## 验证与当前状态

- **软件验证现状**：
  - L0 软件验证：61 项自动化回归测试覆盖解析解（封闭箱、给定风量、线性风机阻力求交、日照综合温度）、合成数据参数恢复与物理单调性。
  - 上游对照：与开源项目 switchgear-thermal-model 在相同基准参数下完成互相复现验证。
  - L2 开放基准：电加热模型箱公开试验方案（建科环能科技有限公司环境舱）处于规划中，见 [试验方案](docs/开放模型箱基准试验方案.md)。
- **定性说明与边界约束**：
  - 本工具为开源研究预览版（v0.1 开发版），计算结果是**充分混合的箱内空气节点温度**，非导体热点温度。
  - 本工具不替代法定型式试验、认证检测或工程安全审查；不内置标准判据，限值由用户录入。
  - 不从导体几何结构计算电磁发热，损耗功率由用户提供或由试验识别；拟合度不等于工况外推预测精度。

## 文档、引用与许可

- **项目文档**：详见 [快速上手文档](docs/快速上手.md)、[模型理论说明](docs/模型说明.md)、[数据与合作指南](docs/数据与合作.md)。
- **引用说明**：在学术论文或技术报告中使用本工具，请参考 [CITATION.cff](CITATION.cff)。
- **开源许可证**：本项目采用 [MIT 许可证](LICENSE)，版权所有 © 2026 Jianke Environment and Energy Technology Co., Ltd. (建科环能科技有限公司) and contributors。第三方依赖许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- **作者与联系**：
  - 项目负责人：王智超（ORCID [0000-0001-6662-835X](https://orcid.org/0000-0001-6662-835X)）
  - 联系邮箱：wangzhichao@chinaibee.com
  - 问题与建议：欢迎通过 [GitHub Issues](https://github.com/wangzhichao-jpg/OpenEnclosure-Thermal/issues) 或 [Gitee 仓库](https://gitee.com/bigtaurus/OpenEnclosure-Thermal) 提交。
