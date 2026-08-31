# DocuMedAI Deployment Guide

Backend → GCP VM (Docker). Frontend → Vercel.

```
Browser → Vercel (Next.js) → https://<domain> → Caddy → FastAPI :2010 (GCP VM)
```

## 1. Create the VM

GCP Console → Compute Engine → Create instance.

- **OS:** Ubuntu 22.04 LTS (not 24.04/26.04 Minimal)
- **Machine:** ≥ 8 vCPU / 16 GB RAM
- **Access scopes:** *Allow full access to all Cloud APIs* (needed for Vertex AI, §7)
- **Static external IP:** *Network interfaces → External IPv4 → Reserve* (survives reboots)

## 2. Install Docker

```bash
sudo apt update && sudo apt upgrade -y
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

Enable + grant your user access, then re-login:

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

## 3. Clone + configure

```bash
git clone <repo-url> && cd DocuMedAI   # branch: main
cp .env.example .env
nano .env
```

Fill in the **required** values:

```dotenv
UUID_NAMESPACE=<a-uuid>                 # `uuidgen`
GOOGLE_CLOUD_PROJECT=<gcp-project-id>
GOOGLE_CLOUD_LOCATION=us-central1
...
```

- **HF_TOKEN** pre-caches the embedding model at build time — a free HF *read* token.
- **No `gcp-sa.json` on a GCP VM** — it's already commented out in `docker-compose.yml`; auth uses the VM's service account (§7).

## 4. Grant Vertex AI access (§7 — the usual 403 source)

Grab the values first (on the VM):

```bash
PROJECT_ID=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)
SA_EMAIL=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email)
echo "$PROJECT_ID | $SA_EMAIL"
```

**a) Enable the API** (as your user account, not the compute SA):

```bash
gcloud auth login
gcloud services enable aiplatform.googleapis.com
```

**b) Grant the role** — must be `roles/aiplatform.user`:

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user"

# verify the role is attached
gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:$SA_EMAIL" \
  --format="table(bindings.role)"
```

> ⚠️ Use `roles/aiplatform.user`, **not** `roles/ml.admin` (legacy — lacks
> `aiplatform.endpoints.predict` → 403).

**c) Set the OAuth scope.** If you see `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT`, the VM
scope is too narrow. Console → VM → **Stop → Edit → Access scopes → Allow full
access → Save → Start** (do it from Console; you're on the VM being stopped).

Verify a token issues:

```bash
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token
# expect "access_token" + "expires_in": 3599
```

> Already running when you fix this? Role applies instantly; a scope change needs
> the Stop→Start. Then `docker compose restart la-documedai` and re-test chat.

## 5. Build + start the backend

```bash
docker compose up -d --build la-documedai la-llmguard
```

Pulls in Qdrant/Mongo/Redis automatically. **First run is slow** — builds images, downloads the model, seeds the `MedicalTerms` collection.

## 6. Verify

```bash
docker ps                                   # expect la-documedai/llmguard/qdrant/mongo/redis
docker compose logs -f la-documedai           # watch the seed finish (may take minutes)
curl localhost:6333/collections/MedicalTerms  # status "green", points_count > 0
curl localhost:2010/health                    # 200 once healthy
```

> `MedicalTerms` empty? Seed likely errored — check full logs (no `grep`, it hides
> the traceback): `docker compose logs la-documedai`.

## 7. Expose over HTTPS (DuckDNS + Caddy)

The backend is `localhost`-only. Give it a public HTTPS name for Vercel.

1. **DuckDNS** — at <https://www.duckdns.org>, create a subdomain (e.g.
   `documedai-backend`) and set its IP to the VM's static IP.
2. **Open GCP firewall TCP 80 + 443** (Let's Encrypt needs them). Close **2010** —
   only Caddy uses it internally.
3. **Caddyfile:**

   ```bash
   nano ~/Caddyfile
   ```
   ```caddyfile
   documedai-backend.duckdns.org {
       reverse_proxy localhost:2010
   }
   ```
4. **Run Caddy** (auto TLS):

   ```bash
   docker run -d --name caddy --restart unless-stopped --network host \
     -v ~/Caddyfile:/etc/caddy/Caddyfile -v caddy_data:/data caddy:2
   ```
5. **Test:** `https://documedai-backend.duckdns.org/health` → 200 OK

---

## 8. Deploy the frontend (Vercel)

| Setting | Value |
|---|---|
| Branch | `main` |
| Root Directory | `frontend` |
| Build | default (Next.js) |

Environment variables:

```dotenv
INTERNAL_API_BASE=https://documedai-backend.duckdns.org   # backend URL from §7
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
```

---

## Common operations

```bash
docker compose logs -f la-documedai                       # tail logs
docker compose restart la-documedai                       # reload app only
docker compose down && docker compose up -d --build la-documedai la-llmguard   # after .env/compose edits
docker compose exec la-documedai printenv | grep GOOGLE   # confirm project, no *_CREDENTIALS
```
