# Repository Guidelines

## 项目结构与模块组织
这是一个将文档转换结果同步到 LightRAG 的 Python CLI。
- `src/sync2rag/`: 核心包（CLI 入口在 `cli.py`，包含配置、扫描、同步）。
- `config.yaml`: 本地运行配置，使用 `config.example.yaml` 作为模板。
- `manifests/`: 生成的 `manifest.json` 与 `manifest.rag.json`。
- `data/`: 生成的产物（markdown、docling JSON/ZIP、图片）。
- `tmp/` 等目录：本地临时空间，不要提交到仓库。

## 构建、测试与开发命令
- `uv sync`: 安装依赖并准备运行环境。
- `uv sync --extra dev`: 安装开发依赖（含 pytest）。
- `uv run sync2rag scan -c config.yaml`: 扫描输入并生成清单。
- `uv run sync2rag changes -c config.yaml`: 查看自上次扫描以来的变更。
- `uv run sync2rag sync -c config.yaml`: 同步 markdown/元数据到 LightRAG。
- `uv run sync2rag run -c config.yaml`: 扫描并同步，一步完成。
- `uv run sync2rag clear --all -c config.yaml`: 清理状态与生成文件。
- `uv run pytest`: 运行测试。

## 代码风格与命名
- Python 3.12+，4 空格缩进，遵循 PEP 8。
- 函数/变量用 snake_case，类名用 PascalCase，模块用 lower_snake_case。
- 优先使用类型标注与 f-string。

## 测试指南
当前测试采用 `pytest`，覆盖配置解析与 CLI 参数处理。
- 测试放在 `tests/` 下，文件名为 `test_*.py`。
- 运行方式：`uv run pytest`。
- 新增测试避免真实网络调用，优先使用临时目录或模拟数据。

## 安全与配置提示
- `config.yaml` 可能包含 API Key，不要提交敏感信息。
- 新增配置项时同步更新 `config.example.yaml`。
