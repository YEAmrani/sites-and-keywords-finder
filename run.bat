@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required. Install it from python.org and try again.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  python -m venv .venv
)

echo Installing application dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo Preparing spaCy noun-phrase model...
.venv\Scripts\python.exe -c "import spacy; spacy.load('en_core_web_sm')" >nul 2>nul
if errorlevel 1 .venv\Scripts\python.exe -m spacy download en_core_web_sm

echo Preparing KeyBERT semantic model...
.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" >nul 2>nul

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop was not found. The app will still start with DuckDuckGo only.
  echo Install Docker Desktop, then run this file again to enable local SearXNG.
) else (
  echo Starting local SearXNG...
  docker compose up -d searxng
)

echo Starting Site Finder at http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
