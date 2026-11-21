#!/bin/sh
# Run once by Patroni on the first leader, after initdb: the two databases a single install keeps in two
# Postgres containers (NetBox's and the platform's), each with its own owner and password.
set -eu
psql "$1" -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE netbox LOGIN PASSWORD '${NETBOX_DB_PASSWORD}';
CREATE DATABASE netbox OWNER netbox;
CREATE ROLE platform LOGIN PASSWORD '${PLATFORM_DB_PASSWORD}';
CREATE DATABASE platform OWNER platform;
SQL
