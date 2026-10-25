# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.14.9-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY backend/requirements-production.lock ./requirements-production.lock
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements-production.lock

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FINSCOPE_ENV=production FINSCOPE_OCR_PROVIDER=disabled
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY infrastructure/runtime ./deploy
RUN groupadd -g 10001 finscope && useradd --no-create-home --uid 10001 --gid 10001 finscope
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "deploy.serve"]
