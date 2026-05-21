# Deploying DocuMedAI frontend (Next.js)

## Vercel (frontend only)

1. Create a Vercel project and set **Root Directory** to `frontend`.
2. Add these **environment variables** in the Vercel project:

| Name | Where it runs | Notes |
|------|----------------|-------|
| `NEXT_PUBLIC_SUPABASE_URL` | Build + Runtime | From Supabase → Settings → API |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Build + Runtime | Supabase anon (public) key |
| `INTERNAL_API_BASE` | Runtime only | Public HTTPS URL of your **FastAPI** server (e.g. `https://api.example.com`). Do **not** use `localhost` in production. |

3. In **Supabase** → Authentication → URL configuration, add your Vercel domain to **Redirect URLs** (e.g. `https://your-app.vercel.app/auth/callback`).

4. On the machine that hosts **FastAPI**, set `SUPABASE_JWT_SECRET` (Supabase → Settings → API → JWT Secret) for magic-link users, and **`LOCAL_AUTH_JWT_SECRET`** (any long random string) for username/password users. Both are used to verify or mint JWTs for `/auth/*` and chat APIs.

## What does not run on Vercel

- **FastAPI**, **mongo**, and **Redis** are not hosted by Vercel. For production you typically run the API on a container host or VM and use **mongo Atlas** + **Upstash Redis** (or similar) if you are not keeping Docker on a server.

## Local Docker Compose

- Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the project root `.env` (Compose substitutes them into `la-frontend`).
- `INTERNAL_API_BASE` is set inside Compose to `http://la-backend:2010` for the Next container.
- Start the FastAPI process inside `la-backend` (e.g. `uvicorn services.chatbot.app:app --host 0.0.0.0 --port 2010`) so the frontend can reach it.
