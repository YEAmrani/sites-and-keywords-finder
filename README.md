# Sites and Keywords Finder

A local-first research workspace for finding websites and extracting ranked, deduplicated keyword phrases.

## What it does

The dashboard has two tools:

- **Finder** searches enabled providers concurrently, including DuckDuckGo and a local SearXNG instance, deduplicates results by domain, validates reachable HTML pages, and exports site URLs.
- **Extractor** fetches site content, removes navigation and boilerplate, extracts noun-phrase candidates, filters generic words, ranks topical phrases, streams progress, and exports unique keywords.

The extractor combines Trafilatura, spaCy, wordfreq, YAKE, optional KeyBERT semantic scoring, and cross-site document-frequency scoring. The requested keyword count is a per-site candidate quota. It is a maximum, not a guarantee: pages may have fewer valid phrases, and the final export removes duplicates.

## Requirements

- Windows 10/11 or Linux/macOS
- Python 3.11 or newer
- Docker Desktop (recommended, required for local SearXNG)
- Internet access for fetching target pages and installing Python/model dependencies

## Quick start on Windows

1. Install Python 3.11+ and Docker Desktop.
2. Start Docker Desktop and wait until it is running.
3. Double-click `run.bat`.
4. Open <http://127.0.0.1:8000/>.

The launcher creates `.venv`, installs `requirements.txt`, prepares the spaCy model and local KeyBERT model, starts SearXNG, and starts the FastAPI app.

## Manual start

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m spacy download en_core_web_sm
.venv\Scripts\python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
docker compose up -d searxng
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

On Linux/macOS, use the equivalent `.venv/bin/python` commands and `run.sh`.

## SearXNG setup

The included `docker-compose.yml` runs the official `searxng/searxng` image locally and maps:

```text
http://127.0.0.1:8888
```

The mounted `searxng/settings.yml` enables JSON search results and disables the local limiter for this single-user local workspace. Do not expose this SearXNG instance directly to the public internet. If Docker is unavailable, disable SearXNG in the Settings panel and use DuckDuckGo only.

Useful Docker commands:

```powershell
docker compose up -d searxng
docker compose ps
docker compose logs -f searxng
docker compose stop searxng
```

## Proxy configuration

Proxy credentials are intentionally local-only and are ignored by Git. Add proxies in the Settings panel or load a private proxy file. Supported formats include:

```text
host:port
host:port:user:password
user:password@host:port
http://user:password@host:port
```

Never commit `config.json`, `free-proxy-list.txt`, `new_proxy.txt`, or any file containing proxy credentials. When proxy mode is enabled, the extractor does not silently fall back to a direct request if the pool is exhausted.

## Finder workflow

1. Enter niche terms.
2. Choose **All** providers, one provider, or leave provider selection automatic.
3. Set the desired website limit, concurrency, timeout, and retries.
4. Start Finder and monitor provider, proxy, and validation telemetry.
5. Export the validated site list or send it to Extractor.

## Extractor workflow

1. Paste one URL per line or use Finder results.
2. Set **Keywords per site**. For example, 100 sites × 55 candidates = 5,500 candidate slots.
3. Set page depth, word range, and threads.
4. Start extraction.
5. Watch the live `processed/total` and `collected/expected` counters.
6. Export TXT, CSV, or JSON.

The live preview is deliberately bounded to protect memory. The final list is globally deduplicated and may be smaller than the candidate quota because weak, duplicate, failed, or content-poor results are removed.

## Data and privacy

The app stores local settings in `config.json` and completed run history in `data/history.db`. Both are ignored by Git. Generated exports in `exports/` are also ignored. Delete `data/history.db` to start with an empty local history.

Private design notes, prompts, proxy lists, personal files, local virtual environments, caches, and generated outputs are excluded by `.gitignore`.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

The test suite covers proxy parsing, phrase filtering, noun-phrase checks, topic signals, candidate limits, deduplication, and ranking behavior.

## Project layout

```text
app/                    FastAPI app, search engine, extractor, proxy, and database logic
static/                 Dashboard HTML, CSS, and JavaScript
tests/                  Automated tests
searxng/                Local SearXNG configuration
docker-compose.yml      SearXNG container definition
requirements.txt        Python dependencies
run.bat / run.sh        Local launchers
```
