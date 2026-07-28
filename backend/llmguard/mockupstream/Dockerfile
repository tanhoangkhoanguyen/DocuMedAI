# Mock upstream LLM provider.
#
# BUILD CONTEXT IS THE PARENT DIRECTORY, not this one — the package is part of
# the llm-proxy module and needs go.mod/go.sum at the module root:
#
#   docker build -f mockupstream/Dockerfile -t la-mockupstream backend/llm-proxy
#
# --- build stage ---
# Pinned to 1.23 to match the go directive in go.mod. CGO off → a fully static
# binary that runs on the distroless static runner.
FROM golang:1.23-bookworm AS builder
WORKDIR /src

# Dependencies first, as their own layer: sources change on nearly every commit
# while go.mod/go.sum rarely do, so the module download stays cached.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" \
    -o /out/mockupstream ./mockupstream/cmd/mockupstream

# --- runtime stage ---
# distroless/static: no shell, no package manager → small attack surface. The
# healthcheck is the binary probing itself, since there is no curl to call.
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=builder /out/mockupstream /mockupstream

EXPOSE 8090
USER nonroot:nonroot

HEALTHCHECK --interval=10s --timeout=5s --start-period=3s --retries=3 \
    CMD ["/mockupstream", "-healthcheck", "-addr", ":8090"]

ENTRYPOINT ["/mockupstream"]
CMD ["-addr", ":8090"]
