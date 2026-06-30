package main

import (
	"crypto/sha256"
	"encoding/hex"

	"golang.org/x/sync/singleflight"
)

// Deduper prevents duplicate concurrent requests from making multiple
// upstream calls. Identical requests share the same response.
type Deduper struct {
	group singleflight.Group
}

func newDeduper() *Deduper { return &Deduper{} }

// key hashes the request body so identical bodies map to the same flight.
// Body already includes model + messages + params, so it's a complete identity.
func dedupKey(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

// Execute fn once per concurrent key.
// Later callers wait for and reuse the first result.
// shared=true means the result came from another caller.
func (d *Deduper) Do(
		key string, 
		fn func() (*upstreamResult, error)
	) (
		res *upstreamResult, 
		shared bool, err error
	) {
	v, e, sharedFlight := d.group.Do(key, func() (interface{}, error) {
		return fn()
	})
	if e != nil {
		return nil, sharedFlight, e
	}
	return v.(*upstreamResult), sharedFlight, nil
}

// --- Extension point: cross-replica dedup ---
// Use Redis to extend deduplication across multiple proxy replicas.
