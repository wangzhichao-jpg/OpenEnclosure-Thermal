# 贡献指南

欢迎提交 Issue 和 Pull Request。

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[test]"
pytest -q
```

## 规则

1. **单位**：内部统一用 SI 单位；温差用 K；绝对温度、温升、环境温度必须分开命名。
2. **方程**：新增模型须在 `docs/模型说明.md` 写明方程、假设、适用范围和拒绝规则，并补充解析解或物理一致性测试。
3. **不静默**：剔除数据、外推、数值失败都要写进结果信息，不能悄悄处理。
4. **结论口径**：不输出“合格/不合格”，不内置受版权保护的标准条文和表格。
5. **数据**：只提交合成数据、自建试验数据，或许可允许再发布的公开数据。提交前运行 `oet check-release .`。
6. **依赖**：核心模块不得依赖可选的外部包；MPL/GPL 组件只能通过 `adapters/` 以可选方式接入，不复制其源码。
