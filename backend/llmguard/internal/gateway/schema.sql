-- LLMGuard usage log — the durable, per-request record behind cost analytics.
--
-- ONE statement with an UNQUALIFIED table name, deliberately. Three different
-- paths execute these exact bytes, and only this shape works for all three:
--
--   1. docker-compose mounts this file into /docker-entrypoint-initdb.d/.
--   2. usagelog.go embeds it with go:embed and runs it at writer startup.
--   3. usagelog_test.go runs it against an isolated llmguard_test database.
--
-- Hence no CREATE DATABASE (the native protocol takes one statement per Exec, so
-- a second would need a SQL splitter — a bug waiting to happen) and no database
-- prefix (a hardcoded one would put the test's table in the production database
-- and destroy its isolation).
--
-- That is also why the table is named llmguard_usage in `default` rather than
-- usage in a dedicated llmguard database. The image's entrypoint builds its
-- client WITHOUT --database, so every init script runs against `default` no
-- matter what CLICKHOUSE_DB is set to — a dedicated database would need a
-- wrapper shell script mounted beside this file, and the prefix buys the same
-- namespacing for nothing.
--
-- Startup execution is not redundant with the init script: the image runs
-- /docker-entrypoint-initdb.d only when the data directory is EMPTY, so a
-- developer whose volume predates this file would otherwise never get the table.
-- IF NOT EXISTS makes running it on every start free.
--
-- APPEND-ONLY. Nothing updates or deletes a row; expiry is the TTL below.
CREATE TABLE IF NOT EXISTS llmguard_usage
(
    -- Millisecond precision: latency_ms is in milliseconds, so second
    -- granularity would collapse the ordering of a burst into one bucket.
    ts                DateTime64(3, 'UTC'),

    -- The OpenTelemetry trace id when the request was traced, else a UUID.
    -- The trace id is what joins a row to its span tree in Jaeger: this table
    -- says what a request cost, the trace says why it was slow.
    request_id        String,

    -- LowCardinality: a handful of distinct values each, which buys large
    -- compression and fast GROUP BY — the only query shape this table serves.
    model             LowCardinality(String),
    provider          LowCardinality(String),

    prompt_tokens     UInt32,
    completion_tokens UInt32,

    -- Decimal, NOT Float64. This is money and the point of the table is
    -- SUM() per key and per model; float accumulates error across millions of
    -- rows. 8 places because a per-request cost is a small fraction of a cent
    -- (a 1k-token Flash call is ~$0.000075).
    cost_usd          Decimal(18, 8),

    latency_ms        UInt32,

    -- 0 means the request NEVER REACHED UPSTREAM — shed by admission control,
    -- refused by the rate limiter, or failed fast on an open breaker. 0 is not
    -- a real HTTP status, so this is unambiguous without paying for a Nullable
    -- (an extra byte per row plus a null mask).
    upstream_status   UInt16,

    -- Attempts after the first. RetryMax defaults to 4, so UInt8 is ample.
    retries           UInt8,

    -- True when this request's flight had more than one caller. Note that
    -- singleflight reports this to the flight LEADER too, so it means
    -- "coalesced", not "reused someone else's response".
    dedup_hit         Bool,

    -- Last 6 characters of the caller's API key — a stable, non-secret bucket
    -- label. "anon" when the request carried no usable key.
    api_key_hint      LowCardinality(String)
)
ENGINE = MergeTree
-- Monthly parts so the TTL below drops whole parts instead of rewriting them.
PARTITION BY toYYYYMM(ts)
-- Leading with the raw DateTime64 would put nearly every row in its own granule
-- mark and make the sparse primary index useless for anything but a time filter.
-- Bucketing to the hour first, then the two columns the cost queries group by,
-- serves both "the last 24h" and "spend per model" from one index.
ORDER BY (toStartOfHour(ts), provider, model, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
