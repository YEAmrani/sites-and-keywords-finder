#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
.venv/bin/python -m pip install -r requirements.txt
if command -v docker >/dev/null 2>&1; then docker compose up -d searxng; fi
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

