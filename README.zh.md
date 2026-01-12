# sync2rag

sync2rag 是一个 Python CLI，用于扫描文档、通过 Docling 转换，并将生成的 markdown 同步到 LightRAG。它会生成清单用于变更跟踪，并可选通过 VLM 接口为图片生成说明。

[English](README.md) | [中文](README.zh.md)

## 环境要求
- Python 3.12+
- uv
- Docling 服务可通过 `docling.base_url` 访问
- LightRAG 服务可通过 `lightrag.base_url` 访问
- 可选：用于图片说明的 captioning 端点

## 安装
```bash
uv sync
```

开发依赖（测试）：
```bash
uv sync --extra dev
```

## 配置
```bash
cp config.example.yaml config.yaml
```

至少需要在 `config.yaml` 中配置：
- `input.root_dir`
- `docling.base_url`
- `lightrag.base_url`
- `lightrag.api_key`

可选设置：
- `captioning.*`：图片说明
- `output.*`：存储路径与对外 URL

## 使用
```bash
uv run sync2rag scan -c config.yaml
uv run sync2rag changes -c config.yaml
uv run sync2rag sync -c config.yaml
uv run sync2rag run -c config.yaml
uv run sync2rag clear --all -c config.yaml
```

## 输出
- `manifests/`：`manifest.json` 与 `manifest.rag.json`
- `data/`：markdown、docling JSON/ZIP、抽取的图片
- `.state/`：本地扫描与同步缓存

## 测试
```bash
uv run pytest
```

## 安全提示
`config.yaml` 可能包含 API Key，请勿提交到版本控制。
