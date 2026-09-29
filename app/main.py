from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, ExtractorRequest, FinderRequest, load_config, save_config
from .db import HistoryDB
from .engine import RunManager
from .proxy import parse_proxy_list


ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Site Finder + Keyword Extractor")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
db = HistoryDB(ROOT / "data" / "history.db")
manager = RunManager(db)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/config")
async def get_config():
    return load_config().model_dump()


@app.put("/api/config")
async def put_config(config: AppConfig):
    return save_config(config).model_dump()


@app.get("/api/history")
async def history():
    return db.recent()


@app.get("/api/proxies/workspace")
async def workspace_proxy_list(file: str = Query("free-proxy-list.txt")):
    allowed_files = {"free-proxy-list.txt", "new_proxy.txt"}
    if file not in allowed_files:
        raise HTTPException(status_code=400, detail="Unsupported proxy file")
    path = ROOT / file
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{file} not found")
    content = path.read_text(encoding="utf-8", errors="ignore")
    parsed, skipped = parse_proxy_list(content)
    return {"content": content, "parsed": len(parsed), "skipped": skipped}


@app.post("/api/finder/start")
async def finder_start(request: FinderRequest):
    save_config(request.config)
    state = await manager.start_finder(request.config)
    return {"run_id": state.run_id}


@app.post("/api/extractor/start")
async def extractor_start(request: ExtractorRequest):
    state = await manager.start_extractor(request)
    return {"run_id": state.run_id}


@app.post("/api/runs/{run_id}/{action}")
async def control_run(run_id: str, action: str):
    if not await manager.control(run_id, action):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    state = manager.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    async def events():
        async for item in manager.stream(state):
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/export")
async def export(payload: dict):
    kind = payload.get("kind", "txt")
    if payload.get("type") == "keywords":
        rows = payload.get("items", [])
        if kind == "json":
            content, media = json.dumps(rows, ensure_ascii=False, indent=2), "application/json"
        elif kind == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["keyword", "score", "sites_count", "source_url"])
            writer.writeheader(); writer.writerows(rows)
            content, media = output.getvalue(), "text/csv"
        else:
            content, media = "\n".join(row["keyword"] for row in rows), "text/plain"
    else:
        rows = payload.get("items", [])
        if kind == "json":
            content, media = json.dumps(rows, ensure_ascii=False, indent=2), "application/json"
        elif kind == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["url", "title"])
            writer.writeheader(); writer.writerows(rows)
            content, media = output.getvalue(), "text/csv"
        else:
            content, media = "\n".join(row.get("url", "") for row in rows), "text/plain"
    return JSONResponse({"content": content, "media_type": media, "filename": f"export.{kind}"})
