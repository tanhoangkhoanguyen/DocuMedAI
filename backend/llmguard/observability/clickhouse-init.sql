-- Run once on an empty data volume. The collector creates its own tables
-- (create_schema), but not the database: its user has no CREATE DATABASE grant.
CREATE DATABASE IF NOT EXISTS otel;
