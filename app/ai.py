from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
FREE_ROUTER_MODEL = "openrouter/free"

SYSTEM_PROMPT = """You are an expert SEO research assistant helping a site-discovery tool.
Your job is to expand a user's niche seeds into precise, commercially meaningful search phrases.

Rules:
- Preserve every original seed exactly or in a natural normalized form.
- Add only phrases that are tightly related to the supplied seeds; never drift into unrelated topics.
- Prefer specific entities, products, platforms, audiences, use cases, problems, comparisons,
  communities, and intent-bearing modifiers over generic filler.
- Avoid generic words and boilerplate such as best, good, new, online, website, home, login,
  free, buy, get, today, news, guide, tips, game, games, and forum unless the phrase is still
  a meaningful niche query when combined with a specific seed.
- Do not invent brands, products, features, or facts that are not implied by the seeds.
- Use lowercase, 2 to 5 words per phrase, no punctuation, no duplicates, and no explanations.
- Return ONLY valid JSON in exactly this shape: {"niches": ["phrase one", "phrase two"]}.
- Return at most 30 phrases, including the original seeds.
"""


def _read_local_env() -> dict[str, str]:
    """Read the small local env file without adding a dotenv runtime dependency."""
    path = ROOT / ".env.local"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            values[key.strip()] = value
    except OSError:
        return {}
    return values


def openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip() or _read_local_env().get("OPENROUTER_API_KEY", "").strip()


def _is_free_model(model: dict) -> bool:
    model_id = str(model.get("id", ""))
    pricing = model.get("pricing") or {}
    try:
        return model_id == FREE_ROUTER_MODEL or model_id.endswith(":free") or (
            float(pricing.get("prompt", 1) or 1) == 0 and float(pricing.get("completion", 1) or 1) == 0
        )
    except (TypeError, ValueError):
        return model_id == FREE_ROUTER_MODEL or model_id.endswith(":free")


def _model_item(model: dict) -> dict:
    return {
        "id": str(model.get("id", "")),
        "name": str(model.get("name") or model.get("id") or "Unknown model"),
        "free": _is_free_model(model),
        "context_length": model.get("context_length"),
    }


async def list_models(search: str = "") -> list[dict]:
    """Return a compact, UI-safe model catalog. The API key is never returned."""
    query = search.strip().casefold()
    key = openrouter_key()
    if not key:
        return [
            {"id": DEFAULT_MODEL, "name": "NVIDIA: Nemotron 3 Ultra 550B A55B (free)", "free": True},
            {"id": FREE_ROUTER_MODEL, "name": "Free Models Router", "free": True, "context_length": 200000},
        ]

    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(f"{OPENROUTER_BASE}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError("OpenRouter model catalog request failed") from exc
    if response.status_code >= 400:
        raise RuntimeError("OpenRouter model catalog request failed")
    try:
        raw_models = response.json().get("data", [])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenRouter model catalog returned invalid data") from exc
    models = [_model_item(model) for model in raw_models if model.get("id")]
    models.insert(0, {"id": FREE_ROUTER_MODEL, "name": "Free Models Router", "free": True, "context_length": 200000})
    if not any(item["id"] == DEFAULT_MODEL for item in models):
        models.insert(0, {"id": DEFAULT_MODEL, "name": "NVIDIA: Nemotron 3 Ultra 550B A55B (free)", "free": True})
    if query:
        models = [item for item in models if query in item["id"].casefold() or query in item["name"].casefold()]
    # Free options are deliberately first so they are visible without searching.
    models.sort(key=lambda item: (not item["free"], item["name"].casefold()))
    unique: dict[str, dict] = {}
    for item in models:
        unique.setdefault(item["id"], item)
    return list(unique.values())[:250]


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI returned no JSON") from None
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("niches"), list):
        raise ValueError("AI returned an invalid niche list")
    return value


def _normalize_niches(originals: list[str], candidates: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*originals, *candidates]:
        phrase = re.sub(r"\s+", " ", str(raw)).strip().lower()
        phrase = re.sub(r"[^\w\s-]", "", phrase, flags=re.UNICODE)
        if not phrase or phrase in seen or len(phrase.split()) > 5:
            continue
        seen.add(phrase)
        result.append(phrase)
    return result[:30]


async def enhance_niches(keywords: list[str], model: str = DEFAULT_MODEL) -> dict:
    originals = _normalize_niches([], keywords)
    if not originals:
        raise ValueError("Enter at least one niche keyword first")
    key = openrouter_key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    selected_model = model.strip() or DEFAULT_MODEL
    user_prompt = "Expand these niche seeds for accurate website discovery:\n" + "\n".join(f"- {item}" for item in originals)
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1000,
        # Keep thinking tokens from consuming the small free-model completion budget.
        "reasoning": {"effort": "minimal", "exclude": True},
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "Sites and Keywords Finder",
    }
    used_model = selected_model
    parsed: dict | None = None
    candidate_models = [selected_model]
    if selected_model == FREE_ROUTER_MODEL:
        try:
            free_models = await list_models("free")
            candidate_models.extend(item["id"] for item in free_models if item["id"] != DEFAULT_MODEL)
        except (RuntimeError, httpx.HTTPError):
            pass
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        for candidate in candidate_models[:4]:
            used_model = candidate
            payload["model"] = used_model
            try:
                response = await client.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers, json=payload)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                if response.status_code in {408, 425, 429, 500, 502, 503, 504} and selected_model == FREE_ROUTER_MODEL:
                    continue
                raise RuntimeError("OpenRouter niche enhancement request failed")
            try:
                body = response.json()
                content = body["choices"][0]["message"].get("content")
                if content:
                    parsed = _extract_json(content)
                    break
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                continue
    if not parsed:
        raise RuntimeError("OpenRouter returned an unusable niche response")
    return {"model": used_model, "niches": _normalize_niches(originals, parsed["niches"])}
