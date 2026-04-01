FROM apache/age:PG17_latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-17-pgvector && \
    rm -rf /var/lib/apt/lists/*
