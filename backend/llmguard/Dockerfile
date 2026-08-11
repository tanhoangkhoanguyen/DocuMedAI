# --- build stage ---
# Pin Go 1.23 to match go.mod. CGO disabled → fully static binary that runs on
# the distroless static runner.
FROM golang:1.23-bookworm AS builder
WORKDIR /src

# Copy sources first, then resolve deps. We run `go mod tidy` in-build because
# go.sum is generated here (no local Go toolchain on the dev machine).
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /out/llmguard .

# --- runtime stage ---
# distroless/static: tiny, no shell, no package manager → small attack surface.
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=builder /out/llmguard /llmguard
# The model allowlist is REQUIRED — the process exits without it — so a default
# copy ships in the image and the service starts out of the box. Compose mounts
# the repo's file over this one, so an operator edits config.yaml and restarts
# rather than rebuilding. LLMGUARD_CONFIG overrides the path.
COPY --from=builder /src/config.yaml /config.yaml
EXPOSE 8081
USER nonroot:nonroot
ENTRYPOINT ["/llmguard"]
