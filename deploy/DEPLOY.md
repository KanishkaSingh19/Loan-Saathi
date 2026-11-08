# Free deployment

| Piece | Free host | Why there |
|---|---|---|
| Webhook API (FastAPI + LightGBM) | **Hugging Face Docker Space** | Free 2 vCPU / 16 GB container, stays warm while in use, permanent URL |
| Storage (pre-screens, calls) | **Neon Postgres** (free plan) | The Space's disk is wiped on every restart, so SQLite would lose data |
| Dashboard (React + Vite) | **Vercel** (Hobby) | Static site, permanent URL, rebuilds on every GitHub push |

The API keeps working without a database URL, but it then writes to SQLite inside the container
and loses everything on restart.

Limits to know about:
- A free Space **sleeps after 48 hours without traffic**, and the first request then takes about a
  minute while it wakes. Open `/health` before a demo or a test call.
- Neon's free database pauses when idle and wakes on the first query (a second or less).
- Vercel Hobby is for non-commercial use, which fits a challenge entry.

## 1. Database: Neon
1. Sign up at neon.tech and create a project (pick the Singapore or Mumbai region if offered,
   to be close to the Space and to callers).
2. Copy the connection string (`postgresql://…?sslmode=require`). It is a secret: don't commit it
   or paste it in chats. The tables are created automatically on the first request.

## 2. API: Hugging Face Space
1. On huggingface.co, go to **New Space**, choose **Docker** → **Blank**, and name it, e.g.
   `loan-saathi-api`. Public is fine: the code is already public and every `/v1` route needs the
   webhook key.
2. Upload these files to the Space (the **Files** tab, or `git clone` the Space and copy them):
   - `Dockerfile`, `.dockerignore`, `requirements.txt`
   - `src/` (all of it)
   - `models/voice_model.txt` and `models/voice_model_meta.json`
   - `deploy/huggingface/README.md`, placed at the Space's root as `README.md` (its header tells
     Hugging Face to use Docker on port 7860)
3. Under **Settings → Variables and secrets**, add as **secrets**:
   - `WEBHOOK_KEY`: a long random value (it can be the one in your local `.env`)
   - `DATABASE_URL`: the Neon connection string
   - `GNANI_STT_API_KEY`, `GNANI_TTS_API_KEY` (or `GNANI_API_KEY`): needed for the web call's voice (`/v1/stt`, `/v1/tts`)
   - `GEMINI_API_KEY`: for the web call's understanding and replies (free from aistudio.google.com)
   - `ALLOWED_ORIGINS`: the Vercel URL from step 3, e.g. `https://loan-saathi.vercel.app`
     (add it once you know it; comma-separate several)
4. Wait for the build, then open `https://<username>-loan-saathi-api.hf.space/health`. It should
   return `{"ok": true}`.
5. In Agent Builder, set the action URL to `https://<username>-loan-saathi-api.hf.space/v1/prescreen`
   (and `/v1/post-call` for the post-call trigger). This URL doesn't change, unlike the tunnel's.

## 3. Dashboard: Vercel
1. The code must be on GitHub first (push the latest commits).
2. On vercel.com, choose **Add New → Project** and import `KanishkaSingh19/Loan-Saathi`.
3. Set **Root Directory** to `dashboard`. The framework is detected as Vite (`dashboard/vercel.json`).
4. Add the environment variable `VITE_API_BASE` = `https://<username>-loan-saathi-api.hf.space`
   (no trailing slash). It is public, not a secret: it gets baked into the page.
5. Deploy, then copy the site URL into the Space's `ALLOWED_ORIGINS` secret. The Space restarts,
   and after that the dashboard can load data. Open it and enter the webhook key, which stays in
   that browser tab only.

## Checking it
```bash
curl https://<username>-loan-saathi-api.hf.space/health
curl -X POST https://<username>-loan-saathi-api.hf.space/v1/prescreen -H "Content-Type: application/json" -d "{}"
```
The second command must return 401 (no key). With `-H "X-Webhook-Key: <key>"` it returns a decision
asking for the missing fields, and the call shows up on the dashboard.
