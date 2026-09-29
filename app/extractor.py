from __future__ import annotations

import asyncio
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import httpx
import yake
from selectolax.parser import HTMLParser

try:
    from wordfreq import zipf_frequency
except ImportError:  # pragma: no cover - optional dependency fallback
    zipf_frequency = None

try:
    import spacy
except ImportError:  # pragma: no cover - optional dependency fallback
    spacy = None

try:
    import trafilatura
except ImportError:  # pragma: no cover - optional dependency fallback
    trafilatura = None

try:
    from keybert import KeyBERT
except ImportError:  # pragma: no cover - optional dependency fallback
    KeyBERT = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency fallback
    SentenceTransformer = None


STOPWORDS = {
    "a", "an", "as", "at", "be", "by", "do", "does", "doing", "for", "from", "in", "into", "is", "it", "its",
    "of", "on", "or", "so", "the", "to", "up", "we", "and", "for", "with", "this", "that", "from", "your", "you", "are", "was", "will", "have", "has",
    "about", "into", "more", "than", "what", "when", "where", "how", "our", "their", "they", "them", "can",
    "les", "des", "une", "pour", "avec", "dans", "sur", "est", "sont", "aux", "plus", "les", "qui", "que",
    "من", "في", "على", "عن", "هذا", "هذه", "مع", "الى", "إلى", "هو", "هي", "ما", "كل", "تم", "أن", "إن",
    "cookie", "cookies", "privacy", "terms", "login", "sign", "menu", "home", "click", "copyright",
}
JUNK = re.compile(r"[^\w\s'’\-&]", re.UNICODE)
WORD = re.compile(r"[^\W_]+(?:['’&-][^\W_]+)*", re.UNICODE)
NOISE_TERMS = {
    "async", "await", "body", "class", "const", "css", "document", "div", "element", "false", "function",
    "getelementbyid", "href", "html", "http", "https", "img", "javascript", "json", "null", "onclick",
    "queryselector", "return", "script", "src", "span", "style", "svg", "true", "undefined", "url", "var",
    "window", "www", "xml",
}
GENERIC_TERMS = {
    "article", "activity", "content", "extractor", "fetching", "finder", "free", "game", "games", "home",
    "keyword", "keywords", "latest", "network", "news", "online", "output", "page", "proxy", "results",
    "run", "search", "settings", "site", "sites", "validated", "website", "workspace",
}
WEAK_SINGLE_TERMS = {
    "all", "also", "back", "best", "buy", "choose", "click", "complete", "day", "easy", "every", "first",
    "full", "get", "good", "great", "help", "here", "just", "like", "live", "make", "many", "may", "most",
    "much", "need", "new", "now", "one", "only", "offer", "offers", "other", "out", "play", "popular", "read",
    "right", "see", "some", "support", "take", "team", "there", "these", "top", "use", "way", "why", "world",
}
COMMON_WORD_ZIPF_LIMIT = 4.0


@dataclass(slots=True)
class KeywordResult:
    keyword: str
    score: float
    sites_count: int
    source_url: str = ""


def clean_keyword(value: str, min_words: int, max_words: int, preserve_terms: set[str] | None = None) -> str | None:
    value = JUNK.sub(" ", value.lower()).replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip(" -")
    words = value.split()
    if not min_words <= len(words) <= max_words:
        return None
    if len(value) < 3 or len(value) > 64 or all(word.isdigit() for word in words):
        return None
    if any(word in NOISE_TERMS for word in words):
        return None
    if len(words) > 1 and any(word in STOPWORDS for word in words):
        return None
    preserved = len(words) == 1 and words[0] in (preserve_terms or set())
    if len(words) == 1 and (words[0] in GENERIC_TERMS or words[0] in WEAK_SINGLE_TERMS) and not preserved:
        return None
    # A common single word is rarely a useful SEO target by itself. Phrases
    # are allowed because a common head noun can still form a valuable query
    # with a branded or topical modifier (for example, "ps5 games").
    if len(words) == 1 and zipf_frequency is not None and not any(char.isdigit() for char in words[0]):
        if zipf_frequency(words[0], "en") > COMMON_WORD_ZIPF_LIMIT and not preserved:
            return None
    if len(words) > 1 and all(word in GENERIC_TERMS for word in words):
        return None
    if any(len(word) > 28 for word in words):
        return None
    if all(word in STOPWORDS for word in words) or any(word in STOPWORDS for word in words if len(words) == 1):
        return None
    if sum(ch.isalpha() for ch in value) < 3:
        return None
    return value


@lru_cache(maxsize=1)
def _spacy_model():
    if spacy is None:
        return None
    try:
        return spacy.load("en_core_web_sm", disable=["ner"])
    except (OSError, ImportError):
        return None


@lru_cache(maxsize=4096)
def _is_noun_phrase(value: str) -> bool:
    """Accept one-word nouns/proper nouns or two-word SEO noun phrases."""
    nlp = _spacy_model()
    if nlp is None:
        return True
    doc = nlp(value)
    tokens = [token for token in doc if not token.is_space and not token.is_punct]
    if len(tokens) == 1:
        return tokens[0].pos_ in {"NOUN", "PROPN", "NUM"}
    if len(tokens) != 2:
        return False
    first, second = tokens
    return first.pos_ in {"ADJ", "NOUN", "PROPN", "NUM"} and second.pos_ in {"NOUN", "PROPN", "NUM"}


@lru_cache(maxsize=1)
def _keybert_model():
    if KeyBERT is None or SentenceTransformer is None:
        return None
    try:
        # Never make a live extraction wait on a model download. The model is
        # used when already cached; the deterministic ranking remains active
        # otherwise.
        encoder = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        return KeyBERT(model=encoder)
    except Exception:
        return None


def _keybert_scores(text: str, candidates: list[str], topic_keywords: list[str] | None) -> dict[str, float]:
    """Use semantic similarity when the optional model is installed/cached."""
    model = _keybert_model()
    if model is None or not candidates:
        return {}
    try:
        keywords = model.extract_keywords(
            text[:40_000],
            candidates=candidates,
            keyphrase_ngram_range=(1, max(len(item.split()) for item in candidates)),
            stop_words="english",
            use_mmr=True,
            diversity=0.35,
            top_n=len(candidates),
            seed_keywords=topic_keywords or None,
        )
        return {keyword.lower(): float(score) for keyword, score in keywords}
    except Exception:
        return {}


async def fetch_page(url: str, timeout: float, proxy: str | None = None) -> tuple[str, str, HTMLParser] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=proxy, headers={"User-Agent": "SiteFinder/1.0"}) as client:
            response = await client.get(url)
        if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "text/html"):
            return None
        return str(response.url), response.text, HTMLParser(response.text)
    except httpx.HTTPError:
        return None


def page_text(parser: HTMLParser) -> str:
    visible, _ = _page_signals(parser)
    return visible


def _page_signals(parser: HTMLParser, raw_html: str = "") -> tuple[str, str]:
    """Return visible page text and high-intent title/heading text.

    Removing executable and layout nodes is important: YAKE otherwise treats
    JavaScript identifiers, CSS classes, and navigation labels as keywords.
    """
    for node in parser.css("script, style, noscript, template, svg, canvas, iframe, nav, footer, header, aside, form, [role='navigation'], [role='menu']"):
        node.decompose()

    def node_text(node) -> str:
        return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip() if node else ""

    title = node_text(parser.css_first("title"))
    description = " ".join(
        node.attributes.get("content", "").strip()
        for node in parser.css('meta[name="description"], meta[property="og:description"]')
        if node.attributes.get("content", "").strip()
    )
    headings = " ".join(node_text(node) for node in parser.css("h1, h2, h3") if node_text(node))
    content_node = parser.css_first("main")
    if content_node is None:
        content_node = parser.css_first("article")
    if content_node is None:
        content_node = parser.css_first("body")
    content = node_text(content_node)
    if trafilatura is not None and raw_html:
        try:
            extracted = trafilatura.extract(
                raw_html,
                include_comments=False,
                include_tables=False,
                include_links=False,
                favor_precision=True,
            )
            if extracted and len(extracted) >= 120:
                content = re.sub(r"\s+", " ", extracted).strip()
        except Exception:
            pass
    # Keep boundaries between title/description/headings/content so an n-gram
    # cannot silently join the last word of one block to the first of another.
    visible = " . ".join(part for part in (title, description, headings, content) if part)
    prominent = " . ".join(part for part in (title, description, headings) if part)
    return visible[:80_000], prominent[:12_000]


def _phrase_counts(text: str, min_words: int, max_words: int, preserve_terms: set[str] | None = None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for segment in re.split(r"[.!?;:,|\n\r]+", text):
        tokens = [token.lower() for token in WORD.findall(segment)]
        for size in range(min_words, max_words + 1):
            for index in range(len(tokens) - size + 1):
                phrase = clean_keyword(" ".join(tokens[index:index + size]), min_words, max_words, preserve_terms)
                if phrase:
                    counts[phrase] += 1
    return counts


def _token_forms(token: str) -> set[str]:
    forms = {token.lower()}
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        forms.update({stem, f"{stem}e"})
    if len(token) > 4 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
    elif len(token) > 4 and token.endswith("s"):
        forms.add(token[:-1])
    return forms


def _topic_forms(values: list[str] | None) -> set[str]:
    """Create small, deterministic variants for matching seed topics to page text."""
    return {form for value in values or [] for token in WORD.findall(value.lower()) for form in _token_forms(token)}


def _has_topic_signal(text: str, prominent: str, topic_keywords: list[str] | None) -> bool:
    topic_forms = _topic_forms(topic_keywords)
    if not topic_forms:
        return True
    prominent_tokens = [token.lower() for token in WORD.findall(prominent)]
    body_tokens = [token.lower() for token in WORD.findall(text)]
    prominent_hits = sum(bool(_token_forms(token) & topic_forms) for token in prominent_tokens)
    body_hits = sum(bool(_token_forms(token) & topic_forms) for token in body_tokens)
    # A title/heading match is strong evidence. Body-only matches need two
    # occurrences so a cookie label or an incidental mention cannot qualify a page.
    return prominent_hits >= 1 or body_hits >= 2


def _rank_page_phrases(text: str, prominent: str, min_words: int, max_words: int,
                       topic_keywords: list[str] | None = None,
                       candidate_limit: int | None = None) -> dict[str, float]:
    exact_topic_tokens = {token.lower() for topic in (topic_keywords or []) for token in WORD.findall(topic)}
    counts = _phrase_counts(text, min_words, max_words, exact_topic_tokens)
    prominent_counts = _phrase_counts(prominent, min_words, max_words, exact_topic_tokens)
    if not counts:
        return {}
    if candidate_limit is not None and len(counts) > candidate_limit * 8:
        # Keep a bounded quality pool before POS and semantic scoring. This
        # prevents large pages from creating thousands of temporary candidates
        # when the user requested only a small per-site quota.
        pool_size = max(candidate_limit * 8, candidate_limit + 50)
        pool = sorted(
            counts,
            key=lambda keyword: (-prominent_counts.get(keyword, 0), -counts[keyword], keyword),
        )[:pool_size]
        counts = Counter({keyword: counts[keyword] for keyword in pool})

    # YAKE provides linguistic salience; frequency and headings provide the
    # stronger SEO-like signals missing from a raw YAKE-only ranking.
    weighted_text = f"{prominent} {prominent} {prominent} {text}"
    extractor = yake.KeywordExtractor(lan="en", n=max_words, dedupLim=0.85, top=500)
    yake_scores: dict[str, float] = {}
    for phrase, score in extractor.extract_keywords(weighted_text):
        clean = clean_keyword(phrase, min_words, max_words)
        if clean:
            yake_scores[clean] = min(yake_scores.get(clean, 999.0), float(score))

    candidates: dict[str, float] = {}
    topic_tokens = _topic_forms(topic_keywords)
    for keyword, frequency in counts.items():
        words = keyword.split()
        heading_frequency = prominent_counts.get(keyword, 0)
        # One-off body phrases are usually noise. Keep one-off phrases only
        # when they occur in a title, description, or heading.
        if frequency < 2 and heading_frequency == 0:
            continue
        if len(words) == 1 and frequency < 3 and heading_frequency == 0:
            continue
        if len(words) > 1 and not any(
            word not in STOPWORDS and word not in GENERIC_TERMS and word not in WEAK_SINGLE_TERMS
            for word in words
        ):
            continue
        if not _is_noun_phrase(keyword):
            continue
        yake_cost = yake_scores.get(keyword, 0.65)
        frequency_signal = math.log1p(frequency)
        prominence_signal = 1.0 + (heading_frequency * 1.5)
        topic_matches = sum(bool(_token_forms(word) & topic_tokens) for word in words)
        exact_topic_matches = sum(word in exact_topic_tokens for word in words)
        topic_signal = 1.0 + min(6.0, exact_topic_matches * 3.0 + max(0, topic_matches - exact_topic_matches) * 0.75)
        weak_terms = sum(word in GENERIC_TERMS or word in WEAK_SINGLE_TERMS for word in words)
        specificity_penalty = 1.0 + weak_terms * 0.9
        # Cap YAKE's raw score influence. Extremely small YAKE values are
        # often caused by repeated boilerplate, not by useful search intent.
        yake_factor = 0.65 + min(0.65, yake_cost)
        candidates[keyword] = (yake_factor * specificity_penalty) / (
            frequency_signal * prominence_signal * topic_signal
        )
    semantic_window = min(150, max(25, (candidate_limit or 100) * 2))
    semantic_candidates = sorted(candidates, key=candidates.get)[:semantic_window]
    semantic_scores = _keybert_scores(text, semantic_candidates, topic_keywords)
    for keyword, similarity in semantic_scores.items():
        if keyword in candidates:
            candidates[keyword] /= 1.0 + min(1.0, max(0.0, similarity)) * 1.5
    if candidate_limit is None:
        return candidates
    # The per-site quota is a real memory boundary, not just a display limit.
    # Everything below this cut is discarded before it reaches the accumulator.
    return dict(sorted(candidates.items(), key=lambda item: (item[1], item[0]))[:candidate_limit])


async def extract_from_site(url: str, pages: int, timeout: float, min_words: int, max_words: int,
                            proxy: str | None = None, topic_keywords: list[str] | None = None,
                            candidate_limit: int | None = None,
                            return_fetch_status: bool = False) -> dict[str, float] | tuple[dict[str, float], bool]:
    def outcome(result: dict[str, float], fetched: bool):
        return (result, fetched) if return_fetch_status else result

    first = await fetch_page(url, timeout, proxy)
    if not first:
        return outcome({}, False)
    final_url, raw_html, parser = first
    first_text, first_prominent = _page_signals(parser, raw_html)
    corpus = [first_text]
    prominent_corpus = [first_prominent]
    if pages > 1:
        base_host = urlparse(final_url).netloc
        links = []
        for anchor in parser.css("a[href]"):
            target = urljoin(final_url, anchor.attributes.get("href", ""))
            if urlparse(target).netloc == base_host and target not in links:
                links.append(target)
        for target in links[: pages - 1]:
            extra = await fetch_page(target, timeout, proxy)
            if extra:
                extra_text, extra_prominent = _page_signals(extra[2], extra[1])
                corpus.append(extra_text)
                prominent_corpus.append(extra_prominent)
    text = " ".join(corpus)
    if not text:
        return outcome({}, True)
    if not _has_topic_signal(text, " ".join(prominent_corpus), topic_keywords):
        return outcome({}, True)
    # Keep CPU-heavy semantic ranking off the event loop so proxy rotation and
    # live progress events remain responsive while pages are being scored.
    result = await asyncio.to_thread(
        _rank_page_phrases,
        text,
        " ".join(prominent_corpus),
        min_words,
        max_words,
        topic_keywords,
        candidate_limit,
    )
    return outcome(result, True)


class KeywordAccumulator:
    """Keep only keyword scores and site counts so extraction can stream without retaining page data."""

    def __init__(self):
        self.sites = defaultdict(int)
        self.scores = defaultdict(list)
        self.total_sites = 0
        self.per_site: list[tuple[str, dict[str, float]]] = []

    def add(self, result: dict[str, float], source_url: str = ""):
        self.total_sites += 1
        self.per_site.append((source_url, dict(result)))
        for keyword, score in result.items():
            self.sites[keyword] += 1
            self.scores[keyword].append(score)

    def _idf(self, keyword: str) -> float:
        """Document frequency across sites; lower cost means a stronger result."""
        documents = max(1, len(self.per_site))
        return math.log((1 + documents) / (1 + self.sites.get(keyword, 0))) + 1.0

    def ranked(self, limit: int, min_words: int, max_words: int,
               topic_keywords: list[str] | None = None) -> list[KeywordResult]:
        preserve_terms = {token.lower() for topic in (topic_keywords or []) for token in WORD.findall(topic)}
        ranked = []
        for keyword, values in self.scores.items():
            if clean_keyword(keyword, min_words, max_words, preserve_terms):
                # Coverage and IDF rank distinctiveness; coverage is not a hard
                # filter because the user still needs the requested per-site
                # quota and unique phrases can be valid on one source.
                combined = (sum(values) / len(values)) / (1 + self.sites[keyword] * 0.8)
                combined /= self._idf(keyword)
                ranked.append(KeywordResult(keyword, round(combined, 6), self.sites[keyword]))
        ranked.sort(key=lambda item: (-item.sites_count, item.score, item.keyword))
        return ranked[:limit]

    def per_site_ranked(self, limit: int, min_words: int, max_words: int,
                        topic_keywords: list[str] | None = None) -> list[KeywordResult]:
        """Return unique top-N-per-site candidates, preserving one source identity."""
        preserve_terms = {token.lower() for topic in (topic_keywords or []) for token in WORD.findall(topic)}
        best_by_keyword: dict[str, tuple[float, KeywordResult]] = {}
        for source_url, result in self.per_site:
            rows = [
                (keyword, score) for keyword, score in result.items()
                if clean_keyword(keyword, min_words, max_words, preserve_terms)
            ]
            # Coverage is a useful trend-like signal: a phrase appearing on
            # several independent pages is more valuable than an equally good
            # phrase found once. It never creates filler rows or duplicates.
            rows.sort(
                key=lambda item: (
                    item[1] / self._idf(item[0]),
                    item[0],
                )
            )
            for keyword, score in rows[:limit]:
                rank_cost = score / self._idf(keyword)
                candidate = KeywordResult(keyword, round(score, 6), self.sites.get(keyword, 1), source_url)
                previous = best_by_keyword.get(keyword)
                if previous is None or rank_cost < previous[0]:
                    best_by_keyword[keyword] = (rank_cost, candidate)
        return [
            item
            for _, item in sorted(
                best_by_keyword.values(),
                key=lambda pair: (-pair[1].sites_count, pair[0], pair[1].keyword),
            )
        ]


def aggregate_keywords(site_results: list[dict[str, float]], limit: int, min_words: int, max_words: int) -> list[KeywordResult]:
    accumulator = KeywordAccumulator()
    for result in site_results:
        accumulator.add(result)
    return accumulator.ranked(limit, min_words, max_words)
