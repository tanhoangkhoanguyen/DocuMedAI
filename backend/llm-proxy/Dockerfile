# --- build stage ---
# Pin Go 1.23 to match go.mod. CGO disabled → fully static binary that runs on
# the distroless static runner.
FROM golang:1.23-bookworm AS builder
WORKDIR /src

# Copy sources first, then resolve deps. We run `go mod tidy` in-build because
# go.sum is generated here (no local Go toolchain on the dev machine).
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /out/llm-proxy .

# --- runtime stage ---
# distroless/static: tiny, no shell, no package manager → small attack surface.
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=builder /out/llm-proxy /llm-proxy
EXPOSE 8081
USER nonroot:nonroot
ENTRYPOINT ["/llm-proxy"]
