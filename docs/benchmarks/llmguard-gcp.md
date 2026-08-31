# LLMGuard benchmark — two-VM GCP setup

Standing up the run that produces publishable numbers. Local runs found the bugs;
this one measures the gateway instead of the laptop.

**Why two machines.** The local ladder was clean to 160 QPS and then collapsed
into nginx 499s — the k6 container ran out of room on the same host as the stack.
That ceiling is the driver's, not the gateway's. Separate machines, same zone and
VPC, driven over **internal IP**: sub-millisecond stable RTT across a real NIC, so
a latency rise means saturation rather than internet jitter.

**Sizing rule.** The client must not saturate before the server. Give it more CPU
than the stack and always run the gate in §6.

Budget ~40 min, most of it VM creation and the Docker build. Two `e2-standard-4`
for ~2h is roughly $1. **Delete the VMs when done** (§9).

## 1. Two VMs

Same **zone** and **VPC**.

| | stack | driver |
|---|---|---|
| name | `llmguard-stack` | `llmguard-driver` |
| machine | `e2-standard-4` (4 vCPU, 16 GB) | `e2-standard-8` (8 vCPU, 32 GB) |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| disk | 30 GB | 20 GB |
| network tag | `llmguard-stack` | `llmguard-driver` |

The stack is Go plus Redis, nginx and a mock — no models, so RAM is not the
constraint the MCP runbook had. The driver gets more CPU because k6 spawns a VU
per in-flight request and that is what fell over locally.

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/zone us-central1-a

gcloud compute instances create llmguard-stack \
  --machine-type=e2-standard-4 --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud --boot-disk-size=30GB --tags=llmguard-stack

gcloud compute instances create llmguard-driver \
  --machine-type=e2-standard-8 --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud --boot-disk-size=20GB --tags=llmguard-driver
```

Tags must actually be attached — an untagged VM matches no firewall rule and
looks exactly like a network failure:

```bash
gcloud compute instances list \
  --format="table(name,networkInterfaces[0].networkIP,zone,tags.items)"
```

Note the stack's **internal** IP (e.g. `10.128.0.10`); every driver command uses
it. `ITEMS` must be non-empty for both rows.

## 2. Firewall: driver → stack only

Port 8082 is nginx in front of the replicas; 8081 is the single-replica gateway.
Both answer without auth, so scope the rule to the driver's tag — never
`0.0.0.0/0`.

```bash
gcloud compute firewall-rules create allow-llmguard-from-driver \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:8081,tcp:8082 \
  --source-tags=llmguard-driver --target-tags=llmguard-stack
```

```bash
gcloud compute firewall-rules list --filter="name~llmguard" \
  --format="table(name,sourceTags.list(),targetTags.list())"
```

## 3. Docker on the stack VM

```bash
gcloud compute ssh llmguard-stack
```

```bash
sudo apt update && sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in so the group applies, then:

```bash
docker --version && docker compose version
nproc    # record this -- it goes in the results header
```

## 4. Bring the stack up

```bash
git clone https://github.com/tanhoangkhoanguyen/DocuMedAI.git && cd DocuMedAI
```

The multi-replica profile needs almost nothing: the mock upstream replaces the
provider, so there are no cloud credentials and no spend.

```bash
cat > .env <<'EOF'
UUID_NAMESPACE="b1343753-a4f3-4be8-94c6-9780ee37ab61"
AUTH_JWT_SECRET=not-used-by-llmguard-but-compose-reads-this-file
MOCK_API_KEY=dummy

# Scenario A needs the upstream to behave like a model, and the limiter out of
# the way -- see docs/benchmarks/llmguard-procedure.md §1.
MOCK_LATENCY=2000ms
RATE_LIMIT_RPM=1000000
EOF

docker compose --profile multi-replica up -d --build
```

Wait for healthy, then verify from the stack VM itself:

```bash
docker compose ps
time curl -s -o /dev/null -X POST http://localhost:8082/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"provider":"mock","model":"gemini-2.5-flash","messages":[{"role":"user","content":"x"}]}'
```

`real` must be ~2s. If it is milliseconds, `MOCK_LATENCY` did not reach the
container — check with `docker inspect la-mockupstream-service` before going on,
because every number after this depends on it.

**Restart nginx whenever replicas are recreated.** It resolves the upstream name
once at startup, so replicas that came back on new IPs are invisible to it and
traffic piles onto whichever old IP still answers:

```bash
docker compose --profile multi-replica up -d --force-recreate la-nginx
```

## 5. Prepare the driver VM

```bash
gcloud compute ssh llmguard-driver
```

```bash
sudo apt update && sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out, back in, then clone for the scripts and set the target once:

```bash
git clone https://github.com/tanhoangkhoanguyen/DocuMedAI.git
cd DocuMedAI/backend/llmguard
export STACK=10.128.0.10        # the stack's INTERNAL ip from step 1
```

k6 runs from its own container and needs host networking here, since the stack is
across the network rather than on a compose bridge:

```bash
docker run --rm -i -e BASE_URL=http://$STACK:8082 -e PROVIDER=mock \
  -e QPS=10 -e DURATION=10s \
  grafana/k6:latest run - < bench/load.js
```

`iterations≈100 dropped=0` and `served_duration` around 2000ms means the path
works end to end.

## 6. The gate: prove the driver is not the bottleneck

This is the step that separates a real ceiling from a client-side one, and it is
the step people skip.

Run the ladder's top rung **twice** — once as configured, once with the driver
given half the load. If the "failure" moves with the driver rather than staying
at a fixed QPS, the driver is what is failing.

```bash
# on the driver, while a high rung is running:
top -bn1 | head -5      # k6 CPU should stay well under 100% x nproc
```

Read a failed rung by its shape, not by the fact it failed:

- **429s** — a result. The gateway shed, which is what it is for.
- **499s / connection resets** — a broken measurement. nginx logs 499 when the
  client hung up. Check on the stack VM:

```bash
docker logs la-nginx-service 2>&1 | tail -20
```

## 7. Run the scenarios

Everything from
[llmguard-procedure.md](llmguard-procedure.md) applies, with two changes:
`BASE_URL` is `http://$STACK:8082` instead of the compose DNS name, and the
sidecar scrapers run **on the stack VM** (they need the compose network).

Order is a data dependency:

| | what | where |
|---|---|---|
| A | capacity ladder — the number this trip is for | driver |
| B | overhead, direct vs gateway | driver |
| D | breaker, 30% then 80% | driver + stack |
| Streaming | `curl` ttfb vs total | stack |
| E | tracing on vs off | both |
| F | nginx calibration | both |

The `in_flight` collector must run on the stack VM, because `/metrics` through
nginx round-robins across per-replica registries:

```bash
# on the stack VM, alongside a driver run
export MSYS_NO_PATHCONV=1   # not needed on Linux; harmless
docker run --rm --network documedai_documedai-net \
  -v "$PWD/backend/llmguard/bench/watch-metrics.sh:/watch.sh:ro" \
  -v "$PWD/out:/out" alpine:3 sh /watch.sh
```

## 8. Record the conditions

Results are only readable with the environment attached. Capture, on both VMs:

```bash
nproc; free -g | head -2; uname -r
docker --version
git rev-parse --short HEAD
gcloud compute instances describe $(hostname) \
  --format="value(machineType.basename(),zone.basename())"
```

Put those in the header of
[llmguard-results.md](llmguard-results.md), replacing the
provisional note. A number without its machine is not reproducible.

## 9. Delete the VMs

Billing continues while they are stopped-but-present.

```bash
gcloud compute instances delete llmguard-stack llmguard-driver --quiet
gcloud compute firewall-rules delete allow-llmguard-from-driver --quiet
gcloud compute instances list        # confirm both are gone
```
