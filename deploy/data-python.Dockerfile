# Administrator-built base for CSV/JSON analysis. Build is never automatic.
# Pin this base by digest in deployments; add scientific packages from locks
# only when needed. No platform configuration or credentials are copied in.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
WORKDIR /output
