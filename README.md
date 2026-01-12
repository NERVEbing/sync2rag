# sync2rag

sync2rag is a Python CLI that scans documents, converts them with Docling, and
syncs the resulting markdown into LightRAG. It generates manifests for change
tracking and can optionally caption images via a VLM endpoint.

[English](README.md) | [中文](README.zh.md)

## Requirements
- Python 3.12+
- uv
- Docling service reachable at `docling.base_url`
- LightRAG service reachable at `lightrag.base_url`
- Optional: captioning endpoint for images

## Installation
```bash
uv sync
```

For development dependencies (tests):
```bash
uv sync --extra dev
```

## Configuration
```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your environment values, at minimum:
- `input.root_dir`
- `docling.base_url`
- `lightrag.base_url`
- `lightrag.api_key`

Optional settings:
- `captioning.*` for image captions
- `output.*` for storage paths and public URLs

## Usage
```bash
uv run sync2rag scan -c config.yaml
uv run sync2rag changes -c config.yaml
uv run sync2rag sync -c config.yaml
uv run sync2rag run -c config.yaml
uv run sync2rag clear --all -c config.yaml
```

## Outputs
- `manifests/`: `manifest.json` and `manifest.rag.json`
- `data/`: markdown, docling JSON/ZIP, extracted images
- `.state/`: local scan and sync caches

## Docker
Pull from GHCR (tag or latest):
```bash
docker pull ghcr.io/nervebing/sync2rag:<tag>
docker pull ghcr.io/nervebing/sync2rag:latest
```

Run with local config and data:
```bash
docker run --rm \
  -v /path/to/input:/input \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/manifests:/app/manifests \
  -v $(pwd)/.state:/app/.state \
  ghcr.io/nervebing/sync2rag:latest scan -c /app/config.yaml
```

## Testing
```bash
uv run pytest
```

## Security Notes
`config.yaml` may contain API keys. Keep it out of version control.
