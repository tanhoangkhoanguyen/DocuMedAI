# LLMGuard — developer task runner.
#
# Phase 1 test harness: `make test` and `make lint` are the two commands CI runs
# (.github/workflows/llmguard-ci.yml).
#
# Run from backend/llmguard/:
#   make test    run the Go test suite
#   make lint    go vet + golangci-lint
#   make run     build and run the proxy locally
#   make bench   run Go benchmarks

GO ?= go
PKGS ?= ./...

# Binary suffix (.exe on Windows) and install location for dev tools, both asked
# of the toolchain rather than assumed, so this Makefile works on Windows,
# Linux and macOS without per-OS branches.
GOEXE := $(shell $(GO) env GOEXE)
GOBIN := $(shell $(GO) env GOPATH)/bin

GOLANGCI_LINT ?= $(GOBIN)/golangci-lint$(GOEXE)
# Pinned so local runs and CI apply an identical rule set — an unpinned linter
# turns a dependency upgrade into a surprise red build on an unrelated PR.
GOLANGCI_LINT_VERSION ?= v2.1.6

# The race detector needs cgo, which is unavailable on a stock Windows install
# (no gcc). Enable it wherever cgo works — notably Linux CI — and degrade to a
# plain run elsewhere rather than failing. Force either way with:
#   make test RACE=-race     /     make test RACE=
ifeq ($(shell $(GO) env CGO_ENABLED),1)
RACE ?= -race
else
RACE ?=
endif

# -count=1 disables Go's test result cache, so `make test` always re-runs.
TESTFLAGS ?= $(RACE) -count=1

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "LLMGuard targets:"
	@echo "  make test    - run tests ($(if $(RACE),race detector on,race detector off: cgo disabled))"
	@echo "  make lint    - go vet + golangci-lint"
	@echo "  make run     - run the proxy locally"
	@echo "  make bench   - run benchmarks"
	@echo "  make build   - compile the binary"
	@echo "  make build-mock / run-mock - mock upstream ($(MOCK_ADDR))"
	@echo "  make tools   - install golangci-lint $(GOLANGCI_LINT_VERSION)"
	@echo "  make tidy    - go mod tidy"

# --- the two targets CI gates on -------------------------------------------

.PHONY: test
test:
	$(GO) test $(TESTFLAGS) $(PKGS)

# `go vet` is run explicitly as well as via golangci-lint's govet linter: vet
# ships with the toolchain, so this half of lint still works before `make tools`
# has ever been run.
.PHONY: lint
lint: vet golangci

.PHONY: vet
vet:
	$(GO) vet $(PKGS)

# Auto-install the pinned linter when it is missing, using make's own $(wildcard)
# rather than a shell test so the check does not depend on sh being present.
ifeq ($(wildcard $(GOLANGCI_LINT)),)
golangci: tools
endif

.PHONY: golangci
golangci:
	$(GOLANGCI_LINT) run $(PKGS)

# --- everyday development ---------------------------------------------------

# Targets "." rather than $(PKGS): the module root holds `main`, while ./...
# also matches internal/gateway, internal/testutil and provider — and `go run`
# against a package set containing non-main packages is ambiguous.
.PHONY: run
run:
	$(GO) run .

# -run '^$$' skips normal tests so only benchmarks execute; -benchmem reports
# allocations, which is the number that matters for a request hot path.
.PHONY: bench
bench:
	$(GO) test -bench=. -benchmem -run '^$$' $(PKGS)

# Compiled binaries go in bin/ rather than the module root. On Linux the mock's
# binary name ("mockupstream") is identical to its SOURCE PACKAGE DIRECTORY, and
# `go build -o mockupstream` against an existing directory writes the binary
# INSIDE it — producing mockupstream/mockupstream, 9MB of build output sitting in
# the source tree. A single ignorable bin/ avoids that, and avoids a .gitignore
# rule that would have to name "mockupstream" and thereby ignore the package.
BIN_DIR ?= bin

.PHONY: build
build:
	$(GO) build -o $(BIN_DIR)/llmguard$(GOEXE) .

# --- mock upstream (see mockupstream/README.md) -----------------------------

MOCK_ADDR ?= :8090

.PHONY: build-mock
build-mock:
	$(GO) build -o $(BIN_DIR)/mockupstream$(GOEXE) ./mockupstream/cmd/mockupstream

.PHONY: run-mock
run-mock:
	$(GO) run ./mockupstream/cmd/mockupstream -addr $(MOCK_ADDR)

.PHONY: docker-mock
docker-mock:
	docker build -f mockupstream/Dockerfile -t la-mockupstream .

.PHONY: tools
tools:
	$(GO) install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_LINT_VERSION)

.PHONY: tidy
tidy:
	$(GO) mod tidy

.PHONY: clean
clean:
	$(GO) clean
	$(GO) clean -testcache
	rm -rf $(BIN_DIR)
