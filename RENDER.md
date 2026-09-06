# OpenManus on Render — quick deploy guide

Fork lives at: https://github.com/8mile1965-collab/OpenManus

## What this fork adds on top of upstream

| File                  | Purpose                                                                    |
|-----------------------|----------------------------------------------------------------------------|
| `render.yaml`         | Render Blueprint — declarative infra, instant provisioning                 |
| `render_main.py`      | FastAPI wrapper exposing `POST /run` (single-prompt agent endpoint)         |
| `render_envsubst.py`  | Reads `OPENMANU_*` env vars and materialises them into `config/config.toml` |
| `config/config.toml`  | Production-ready template (CometAPI, Russian locale, headless browser)       |
| `Dockerfile`          | Render-tuned (healthcheck, uvicorn CMD, envsubst pipeline)                  |

The upstream CLI (`python main.py --prompt "..."`) still works locally.

---

## One-time deploy (≈ 5 minutes)

### 1. Connect the fork to Render

1. Open https://render.com → Sign up (GitHub OAuth, no card).
2. Dashboard → **New** → **Blueprint**.
3. Pick the repo **8mile1965-collab/OpenManus**.
4. Render auto-detects `render.yaml`. Click **Apply**.
5. When prompted for `OPENMANU_LLM_API_KEY`, paste `sk-WrZEs…` from vault (CometAPI).
   Other env vars are pre-filled by the blueprint — leave defaults.
6. Wait ~5–8 min for first build (Docker layer + `uv pip install`).

Free tier note: the service sleeps after 15 min idle. First request after a
sleep takes ~30–60 s to wake up — that's expected.

### 2. Smoke test

```bash
curl -fsS https://openmanus.onrender.com/healthz
# → {"status":"ok"}

curl -fsS -X POST https://openmanus.onrender.com/run \
     -H "Content-Type: application/json" \
     -d '{"prompt":"Скажи привет, кто ты?"}'
```

If the cold-start warning appears, retry once after ~1 min.

### 3. Hook up Telegram

When the Render URL is stable, ask me and I'll wire `@Vell_um_bot` to POST
prompts from your chat straight to `/run` — full OpenManus-on-tap.

---

## Local run (no Render)

```bash
cp config/config.example.toml config/config.toml
# edit api_key
uv pip install -r requirements.txt
python -m playwright install chromium        # only if browser tools needed
python render_main.py                        # serves on :10000
```

---

## Cost

Render free tier = $0/mo. Service sleeps after 15 min idle → first request
wakes it.  Heavy workloads (crawl4ai + playwright browsers) may exceed
512 MB RAM — upgrade to **Starter $7/mo** (1 GB) if you see OOM kills in
the Render logs.
