# DocuMedAI Deployment Guide

Deploy the **backend** to a GCP VM (Docker) and the **frontend** to Vercel.
The VM runs FastAPI + Mongo + Redis + Qdrant + LLM proxy behind Caddy (HTTPS);
Vercel serves the Next.js app and calls the backend over that HTTPS URL.

```
Browser → Vercel (Next.js) → https://<domain>  →  Caddy → FastAPI :2010 (GCP VM)
```

---

## 1. Create the VM

GCP Console → Compute Engine → Create instance.

- **OS:** Ubuntu 22.04 LTS (do **not** use 24.04/26.04 Minimal — avoids extra issues)
- **Machine:** ≥ 8 vCPU / ≥ 16 GB RAM (backend needs ≥ 8 cores; `mem_limit: 8g`)
- **Access scopes:** *Allow full access to all Cloud APIs* — required for Vertex AI (see §7)
- Reserve a **static external IP** so it survives reboots.

SSH in:

```bash
ssh <your-vm>
```

---

## 2. Update system

```bash
sudo apt update
sudo apt upgrade -y
```

---

## 3. Install Docker (official repo)

```bash
# Dependencies
sudo apt install -y ca-certificates curl git

# Add Docker's GPG key
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repo
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

---

## 4. Enable Docker + add user

```bash
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
exit          # log out, then SSH back in for the group to take effect
```

Verify all three:

```bash
docker --version
docker compose version
docker buildx version
```

---

## 5. Clone the project

```bash
git clone <repo-url>
cd DocuMedAI          # default branch: main
```

---

## 6. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in the **required** values:

```dotenv
UUID_NAMESPACE=<a-uuid>                 # e.g. `uuidgen`
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
GOOGLE_CLOUD_LOCATION=us-central1

HF_TOKEN=<huggingface-READ-token>       # REQUIRED — image build fails if empty (see note)

SUPABASE_JWT_SECRET=<supabase-jwt-secret>
AUTH_JWT_SECRET=<random-long-string>

NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
```

Optional: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `LANGCHAIN_*`, `SEED_FORCE`.

Get the project ID from the VM if unsure:

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/project/project-id
```

> **HF_TOKEN is mandatory.** The backend image pre-caches the embedding model at
> build time using a BuildKit secret sourced from `HF_TOKEN`. If it is unset or
> empty the build **fails by design**. A free HuggingFace **read** token is enough.

> **`gcp-sa.json` not needed on a GCP VM.** The service-account mount and
> `GOOGLE_APPLICATION_CREDENTIALS` are already commented out in
> `docker-compose.yml`. Auth uses the VM's own service account (see §7). Only set
> `GOOGLE_SA_KEY_PATH` if deploying **off** GCP.

---

## 7. Grant Vertex AI access to the VM service account

Chat completions run on Vertex AI. On a GCP VM this works **without a key file**,
but the VM's service account needs both:

**a) IAM role.** GCP Console → IAM & Admin → IAM → find the Compute Engine
default service account → add role **Vertex AI User**.

Find its email:

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
```

**b) OAuth access scope.** The IAM role is not enough if the VM was created with
a restricted scope (symptom: `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT` from
`aiplatform.googleapis.com`).

Fix: Console → Compute Engine → your VM → **Stop** → **Edit** → *Access scopes* →
**Allow full access to all Cloud APIs** → Save → **Start**.

Verify a token is issued:

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token
# expect JSON with "access_token" and "expires_in": 3599
```

---

## 8. Build + start the backend stack

The frontend runs on Vercel (§12), so start only the backend services:

```bash
docker compose up -d --build la-backend la-llm-proxy
```

`la-backend` pulls in `la-qdrant`, `la-mongo`, `la-redis` via `depends_on`.
The **first** run is slow: it builds images, downloads the embedding model, and
seeds the `MedicalTerms` Qdrant collection from the prod dataset.

---

## 9. Verify containers

```bash
docker ps
```

Expect (names as configured):

```
la-backend-service
la-llm-proxy-service
la-qdrant-service
la-mongo-service
la-redis-service
```

---

## 10. Watch startup + confirm seed

First boot runs the seed **before** the API becomes healthy
(`start_period: 180s`), so `/health` may fail for a few minutes. Tail the logs:

```bash
docker compose logs -f la-backend
```

Wait for the seed to finish, then confirm the medical collection exists and is
populated (this is what lets the chatbot answer medical questions):

```bash
curl localhost:6333/collections/MedicalTerms
# expect status "green" and points_count > 0

curl localhost:2010/health
# expect 200 once healthy
```

> If `MedicalTerms` is missing or empty, the dataset likely didn't reach the VM
> or the seed errored — check the **full** logs (a `grep` filter can hide the
> traceback):
> ```bash
> docker compose logs la-backend | grep -A40 "seeding MedicalTerms"
> ```

---

## 11. Expose the backend over HTTPS (DuckDNS + Caddy)

The backend listens on `localhost:2010`, which only the VM can reach. Give it a
public HTTPS name so Vercel can call it.

**a) Free subdomain — DuckDNS.** At <https://www.duckdns.org>, log in, create a
subdomain (e.g. `documedai-backend`), and set **Current IP** to the VM's static
external IP. You now have `documedai-backend.duckdns.org`.

**b) Caddyfile** (auto-provisions Let's Encrypt TLS):

```bash
nano ~/Caddyfile
```

```caddyfile
documedai-backend.duckdns.org {
    reverse_proxy localhost:2010
}
```

**c) Run Caddy:**

```bash
docker run -d \
  --name caddy \
  --restart unless-stopped \
  --network host \
  -v ~/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  caddy:2
```

> Open GCP firewall ports **80 and 443** to the VM, or Let's Encrypt can't issue
> the certificate.

**d) Test:**

```
https://documedai-backend.duckdns.org/health   → 200 OK
```

---

## 12. Deploy the frontend on Vercel

| Setting | Value |
|---|---|
| Repository | DocuMedAI |
| Branch | `main` |
| Root Directory | `frontend` |
| Build / Install | default (Next.js) |

**Environment variables** (Vercel → Project → Settings → Environment Variables):

```dotenv
INTERNAL_API_BASE=https://documedai-backend.duckdns.org
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
```

`INTERNAL_API_BASE` is the public backend URL from §11 — it tells the Next.js
server where to send API calls. The `NEXT_PUBLIC_SUPABASE_*` vars are needed for
auth in the browser.

Final flow:

```
Browser → Vercel → INTERNAL_API_BASE → Caddy (HTTPS) → FastAPI :2010 (GCP VM)
```

---

## Common operations

```bash
docker compose logs -f la-backend        # tail backend logs
docker compose logs -f                    # all services
docker compose down                       # stop
docker compose up -d --build la-backend la-llm-proxy   # rebuild + restart

# After editing .env or docker-compose.yml, recreate:
docker compose down && docker compose up -d --build la-backend la-llm-proxy

# Confirm the container env (should show project, no *_CREDENTIALS):
docker compose exec la-backend printenv | grep GOOGLE
```
