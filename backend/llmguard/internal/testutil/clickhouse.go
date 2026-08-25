package testutil

// ClickHouse fixtures for tests that genuinely need a server — the usage
// writer's insert path is columnar block encoding done by the driver, so a fake
// can prove the buffer logic but never that a row round-trips through the real
// wire format with its types intact.

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
)

// DefaultClickHouseAddr is the fallback ClickHouse endpoint for tests: the
// NATIVE protocol port (9000), not the HTTP one (8123).
const DefaultClickHouseAddr = "127.0.0.1:9000"

// TestClickHouseDatabase is where test tables live.
//
// A separate database from the gateway's own `llmguard`, for the reason
// DefaultRedisURL points at DB 15: a test run must not be able to clobber real
// usage rows, and a TRUNCATE against the wrong database is a silent, total loss.
const TestClickHouseDatabase = "llmguard_test"

// ClickHouseAddr returns the endpoint tests should use.
func ClickHouseAddr() string {
	if v := os.Getenv("TEST_CLICKHOUSE_ADDR"); v != "" {
		return v
	}
	return DefaultClickHouseAddr
}

// RequireClickHouse returns a connection to the test database, creating it if
// needed.
//
// If ClickHouse is unreachable the test is SKIPPED, not failed — the same
// posture as RequireRedis and for the same reason: `make test` has to stay green
// on a laptop with nothing running. CI is where the server is guaranteed, which
// is why llmguard-ci.yml supplies one; without that service container these tests
// would silently never run anywhere.
//
// The caller is responsible for creating its table (the DDL lives in the gateway
// package, which testutil must not import — that would be an import cycle with
// the gateway's own tests). Cleanup drops every table this database holds, so a
// failed test cannot leak rows into the next one.
func RequireClickHouse(t *testing.T) driver.Conn {
	t.Helper()

	addr := ClickHouseAddr()

	// Connect to `default` first: the test database may not exist yet, and the
	// driver resolves Auth.Database at connection time.
	admin, err := clickhouse.Open(&clickhouse.Options{
		Addr:        []string{addr},
		Auth:        clickhouse.Auth{Database: "default", Username: "default"},
		DialTimeout: 2 * time.Second,
	})
	if err != nil {
		t.Skipf("testutil: clickhouse options rejected for %s (%v); skipping", addr, err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	if pingErr := admin.Ping(ctx); pingErr != nil {
		_ = admin.Close()
		t.Skipf("testutil: clickhouse unavailable at %s (%v); skipping", addr, pingErr)
	}
	if execErr := admin.Exec(ctx, "CREATE DATABASE IF NOT EXISTS "+TestClickHouseDatabase); execErr != nil {
		_ = admin.Close()
		t.Fatalf("testutil: create test database: %v", execErr)
	}
	_ = admin.Close()

	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr:        []string{addr},
		Auth:        clickhouse.Auth{Database: TestClickHouseDatabase, Username: "default"},
		DialTimeout: 2 * time.Second,
	})
	if err != nil {
		t.Fatalf("testutil: open test database: %v", err)
	}

	t.Cleanup(func() {
		cleanCtx, cleanCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cleanCancel()
		dropAllTables(cleanCtx, conn)
		_ = conn.Close()
	})
	return conn
}

// dropAllTables empties the test database.
//
// Errors are deliberately ignored: this runs in t.Cleanup, where the test's own
// verdict is already decided, and failing here would replace a real failure
// message with a teardown one.
func dropAllTables(ctx context.Context, conn driver.Conn) {
	rows, err := conn.Query(ctx,
		"SELECT name FROM system.tables WHERE database = ?", TestClickHouseDatabase)
	if err != nil {
		return
	}
	var names []string
	for rows.Next() {
		var name string
		if scanErr := rows.Scan(&name); scanErr == nil {
			names = append(names, name)
		}
	}
	_ = rows.Close()

	for _, name := range names {
		_ = conn.Exec(ctx, "DROP TABLE IF EXISTS "+TestClickHouseDatabase+"."+name)
	}
}
