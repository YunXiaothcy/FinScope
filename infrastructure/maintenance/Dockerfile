ARG POSTGRES_IMAGE=postgres:18.6-bookworm
FROM ${POSTGRES_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY scripts/pg_backup.py /opt/finscope/pg_backup.py
USER 10001:10001
ENTRYPOINT ["python3", "/opt/finscope/pg_backup.py"]
