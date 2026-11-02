# Deployment (Render, Docker)

ResolveRight runs as one Docker container: FastAPI (`/api/v1/*`, `/health`, `/docs`) with the Gradio UI at `/ui`.

## What is in the repository
| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12 slim, `uv sync --frozen --no-dev --no-group eval`, non-root user, health check, one uvicorn worker |
| `.dockerignore` | keeps `.env`, `.git`, tests, docs and local data stores out of the image |
| `render.yaml` | Render Blueprint: Docker web service, health check `/health`, env vars (secrets are `sync: false`) |
| `.github/workflows/uptime.yml` | pings `/health` every 10 minutes (also keeps a free instance awake) |
| `scripts/ping.py` | same check from the command line |

## Secrets
- `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` are typed into the **Render dashboard** only. They are not in the image, the repository, the logs or any screenshot.
- `.env` is git-ignored and docker-ignored. The detect-secrets pre-commit hook scans every commit; `uv run pre-commit run detect-secrets --all-files` scans the working tree (the history scan is in `docs/evidence/history-scan.txt`).
- Rotate a key at once if it is ever pasted somewhere it should not be.

## Cost and abuse protection
- Per-customer limit: `RATE_LIMIT_PER_MINUTE` (default 10). Over the limit the API answers `429` with `Retry-After`; the UI shows a polite message.
- Global budget: `DAILY_REQUEST_BUDGET` (render.yaml sets 300). When used up, every request gets `429` until the next UTC day.
- Message length: `MAX_MESSAGE_CHARS` (1500). Empty and garbage input never reach the LLM.
- Set a monthly spend limit in the OpenAI dashboard as the last line of defence.

## Steps for the first deployment (you do these; they need your accounts)
1. Create an empty GitHub repository, then in the project folder:
   `git remote add origin https://github.com/<you>/<repo>.git` and `git push -u origin main`.
2. Render dashboard > **New > Blueprint** > pick the repository. Render reads `render.yaml`.
3. When asked, enter the three secret values (OpenAI key, Langfuse public and secret key).
4. Wait for the build (the first one takes several minutes). Open the service URL.
5. Check: `https://<service>.onrender.com/health` shows `"status":"ok"` and `"vector_store":"ok"`; `/ui` loads; `/ui-staff` and `/ui-dev` load (the staff page has no login, so do not advertise its address); `/docs` loads.
6. Send one message in the UI, then open Langfuse > Traces and find the trace.
7. GitHub > Settings > Secrets and variables > Actions > **Variables** > add `RESOLVERIGHT_URL` = the service URL (no trailing slash). The uptime workflow then runs every 10 minutes.

## Langfuse region
`LANGFUSE_HOST` must match the region where the Langfuse project was created (`render.yaml` sets the US host, `https://us.cloud.langfuse.com`). Keys from one region return `401 Invalid credentials` on the others. Check with `uv run python -c "import app.config; from langfuse import Langfuse; print(Langfuse().auth_check())"`.

## Behaviour to know
- **First start on an empty disk:** the app embeds the 10 policies (a few cents, a few seconds). On every later start it re-embeds only the policy files that changed (D54). If that fails, `/health` shows `degraded` and the agent escalates policy questions instead of guessing.
- **Free plan:** the instance sleeps after inactivity and its disk is wiped on every deploy and restart. That is fine here because policies are re-embedded on start, but pending review cases and sessions (in memory) are lost.
- **One worker only:** local Qdrant allows a single process per folder. Do not add `--workers`.
- **Cold start:** the first request after a sleep can take about a minute. The app warms up the retriever at start so the 30 s request deadline is not spent on imports.

## Local error: "qdrant_db is already accessed by another instance of Qdrant client"
Local Qdrant allows one program per `qdrant_db/` folder. The message means a second copy of the app (or `pytest`) opened the folder while another one still held it, most often a server left running on another port.

How to avoid it:
- Run only one server for this project at a time. The UI and the API come from the same process, so there is no reason for two.
- Stop the server (Ctrl+C in its window) before you run `uv run pytest` or start the app on another port.
- Do not start the app in two terminals at once, and do not use `--workers`.
- To see who holds a port: `Get-NetTCPConnection -State Listen -LocalPort 8000,8012`. To stop a leftover server: `Stop-Process -Id <process id> -Force`. When started with `uv run`, the server is several processes (uv, uvicorn, python), so stop the whole group or the port stays busy.
- If the message still shows after the other copy is stopped, restart the server you want to keep.
- Real concurrent access needs a Qdrant server instead of the local folder, which is more than this capstone needs.