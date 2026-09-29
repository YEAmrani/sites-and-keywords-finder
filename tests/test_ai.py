import asyncio

from app.ai import DEFAULT_MODEL, _extract_json, _normalize_niches, list_models


def test_ai_response_parser_accepts_json_code_fences():
    assert _extract_json('```json\n{"niches":["ps5 games"]}\n```') == {"niches": ["ps5 games"]}


def test_ai_niche_normalization_preserves_seeds_and_deduplicates():
    assert _normalize_niches(["PS5", "Xbox"], ["ps5", " PS5 accessories ", "xbox games"]) == [
        "ps5", "xbox", "ps5 accessories", "xbox games"
    ]


def test_model_catalog_keeps_nemotron_and_free_router_available_without_network(monkeypatch):
    monkeypatch.setattr("app.ai.openrouter_key", lambda: "")
    models = asyncio.run(list_models())
    ids = {model["id"] for model in models}
    assert DEFAULT_MODEL in ids
    assert "openrouter/free" in ids
    assert all(model["free"] for model in models)
