# Sites and Keywords Finder

Local dashboard for finding websites and extracting keywords.

## Requirements

- Python 3.11+
- Docker Desktop for SearXNG search

## Launch on Windows

1. Install Python and Docker Desktop.
2. Start Docker Desktop.
3. Double-click `run.bat`.
4. Open <http://127.0.0.1:8000>.

The launcher installs dependencies, starts SearXNG, and starts the app.

## Finder

1. Open **Finder**.
2. Enter your niche keywords.
3. Choose **All** engines or select specific engines.
4. Set the website limit.
5. Click **Start finder**.
6. Export the website list when complete.

## Extractor

1. Open **Extractor**.
2. Paste one website URL per line, or use Finder results.
3. Set keywords per site, pages, word range, and threads.
4. Click **Start extractor**.
5. Export keywords as TXT, CSV, or JSON.

## Settings

Use **Settings** to configure search engines, SearXNG, timeout, retries, and proxies. The included `config.json` is a clean starter configuration.

If SearXNG is unavailable, disable it in Settings and use the other enabled search engine.

## Manual launch

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
docker compose up -d searxng
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run tests with:

```powershell
.venv\Scripts\python -m pytest -q
```
