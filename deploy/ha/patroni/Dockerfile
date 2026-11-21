# Postgres 17 (as in the single-server compose) run by Patroni. Patroni and its etcd v3 client pinned exactly;
# psycopg-binary 3.2.3 has no wheel for the Python 3.14 that postgres:17-alpine now ships; 3.2.13 does.
FROM docker.io/postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b
RUN apk add --no-cache python3 py3-pip \
 && pip install --no-cache-dir --break-system-packages "patroni[etcd3]==4.0.4" "psycopg[binary]==3.2.13"
USER postgres
ENTRYPOINT ["patroni", "/etc/patroni.yml"]
