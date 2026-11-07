# CodeCare

```
frontend/   marketing site and the /workspace app
backend/    FastAPI app, Alembic, tests, sample fixture
sandbox/    verification image
deploy/     Compose files and Caddy
docs/       product and operations docs
scripts/    backup and live checks
```

Secrets belong in `backend/.env`, which is gitignored. Start from `.env.example`.

## Marketing site only

From `frontend/` in PowerShell, run:

```powershell
py -m http.server 8765
```

Then open http://127.0.0.1:8765/ in a browser.

Dark theme is the default for a first visit. The header toggle switches between dark and light and remembers the choice in browser local storage.

## Full platform

See [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/API.md](docs/API.md), and [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md).

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
uvicorn codecare.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/ for the site and http://127.0.0.1:8000/workspace/login.html to sign in. The production site is https://codecare.si.

GitHub, Dodo Payments, hosted AI, and sandbox verification stay disabled until the matching environment variables are set. The product does not report those integrations as successful without credentials.

Public URLs in the sitemap, robots file, and page metadata use https://codecare.si. Legal pages still mark the operating company, dates, and jurisdiction as drafts for counsel.
