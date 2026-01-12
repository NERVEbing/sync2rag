FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config.example.yaml /app/config.example.yaml

RUN pip install --no-cache-dir .

ENTRYPOINT ["sync2rag"]
CMD ["--help"]
