from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


class AppConfig(BaseModel):
    niche_keywords: list[str] = Field(default_factory=lambda: ["ps5", "xbox", "forex", "courses"])
    threads: int = Field(default=8, ge=1, le=64)
    websites_limit: int = Field(default=25, ge=1, le=500)
    keywords_limit: int = Field(default=100, ge=1, le=5000)
    min_words: int = Field(default=2, ge=1, le=6)
    max_words: int = Field(default=2, ge=1, le=6)
    pages_per_site: int = Field(default=1, ge=1, le=15)
    timeout: float = Field(default=12.0, ge=2, le=120)
    retries: int = Field(default=2, ge=0, le=5)
    ddg_enabled: bool = True
    searxng_enabled: bool = True
    search_engines: list[str] = Field(default_factory=list)
    searxng_url: str = "http://localhost:8888"
    ai_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    proxies_enabled: bool = False
    proxy_list: str = ""
    domain_blocklist: list[str] = Field(default_factory=lambda: [
        "google.", "facebook.com", "youtube.com", "instagram.com", "twitter.com",
        "x.com", "tiktok.com", "pinterest.com", "reddit.com", "amazon.",
        "wikipedia.org", "linkedin.com", "yelp.com", "tripadvisor.com",
    ])

    @field_validator("max_words")
    @classmethod
    def max_not_below_min(cls, value: int, info):
        minimum = info.data.get("min_words", 1)
        return max(value, minimum)

    @field_validator("niche_keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value):
        if isinstance(value, str):
            value = value.replace("\n", ",").split(",")
        return [str(item).strip() for item in (value or []) if str(item).strip()]


def load_config() -> AppConfig:
    if CONFIG_PATH.exists():
        try:
            return AppConfig.model_validate_json(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return AppConfig()


def save_config(config: AppConfig) -> AppConfig:
    CONFIG_PATH.write_text(json.dumps(config.model_dump(), indent=2), encoding="utf-8")
    return config


class FinderRequest(BaseModel):
    config: AppConfig


class ExtractorRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)
    keywords_limit: int = Field(default=100, ge=1, le=5000)
    topic_keywords: list[str] = Field(default_factory=list, max_length=50)
    min_words: int = Field(default=2, ge=1, le=6)
    max_words: int = Field(default=2, ge=1, le=6)
    pages_per_site: int = Field(default=1, ge=1, le=15)
    threads: int = Field(default=8, ge=1, le=64)
    timeout: float = Field(default=12.0, ge=2, le=120)
    proxies_enabled: bool = False
    proxy_list: str = ""


class AIEnhanceRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=50)
    model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
