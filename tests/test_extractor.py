from selectolax.parser import HTMLParser

from app.extractor import KeywordAccumulator, clean_keyword, aggregate_keywords, page_text, _has_topic_signal, _rank_page_phrases


def test_keyword_cleaning_respects_word_range():
    assert clean_keyword("Best PS5 Games", 1, 2) is None
    assert clean_keyword("PS5 Games", 1, 2) == "ps5 games"
    assert clean_keyword("privacy", 1, 2) is None


def test_aggregate_prefers_cross_site_frequency():
    result = aggregate_keywords([{"ps5 games": 0.4, "xbox": 0.1}, {"ps5 games": 0.5}], 2, 1, 2)
    assert result[0].keyword == "ps5 games"
    assert result[0].sites_count == 2


def test_page_text_removes_code_and_layout_noise():
    parser = HTMLParser("""
    <html><head><title>PS5 Games and Reviews</title><style>.text-black{color:black}</style></head>
    <body><nav>Home Login</nav><main><h1>PS5 Games</h1><p>Compare new PS5 games and console reviews.</p></main>
    <script>document.querySelector('div').className = 'return function';</script></body></html>
    """)
    text = page_text(parser)
    assert "ps5 games" in text.lower()
    assert "queryselector" not in text.lower()
    assert "text-black" not in text.lower()


def test_keyword_cleaning_rejects_code_phrases():
    assert clean_keyword("return function", 1, 2) is None
    assert clean_keyword("document getelementbyid", 1, 2) is None
    assert clean_keyword("is a", 1, 2) is None
    assert clean_keyword("best", 1, 1) is None


def test_aggregate_keeps_valid_site_specific_terms_without_padding():
    result = aggregate_keywords([
        {"ps5 games": 0.2, "rare phrase": 0.1},
        {"ps5 games": 0.2},
        {"xbox games": 0.2},
    ], 10, 1, 2)
    assert result[0].keyword == "ps5 games"
    assert {item.keyword for item in result} == {"ps5 games", "rare phrase", "xbox games"}


def test_per_site_export_keeps_the_requested_quota_and_source():
    accumulator = KeywordAccumulator()
    accumulator.add({"ps5 games": 0.2, "console reviews": 0.3, "gaming forums": 0.5}, "https://one.example")
    accumulator.add({"xbox games": 0.2, "console reviews": 0.4, "gaming forums": 0.6}, "https://two.example")
    rows = accumulator.per_site_ranked(1, 1, 2)
    assert len(rows) == 2
    assert {row.keyword for row in rows} == {"ps5 games", "xbox games"}
    assert {row.source_url for row in rows} == {"https://one.example", "https://two.example"}


def test_per_site_export_removes_duplicate_terms_across_sources():
    accumulator = KeywordAccumulator()
    accumulator.add({"ps5 games": 0.2, "console reviews": 0.3, "gaming forums": 0.5}, "https://one.example")
    accumulator.add({"ps5 games": 0.4, "console reviews": 0.2, "gaming forums": 0.6}, "https://two.example")
    rows = accumulator.per_site_ranked(2, 2, 2)
    assert [row.keyword for row in rows].count("ps5 games") == 1
    assert len({row.keyword for row in rows}) == len(rows)
    assert len(rows) == 2


def test_topic_signal_rejects_unrelated_pages_but_accepts_inflections():
    assert not _has_topic_signal("online casino poker slots", "Casino Poker", ["PS5", "gaming"])
    assert _has_topic_signal("new games and gaming accessories", "Gaming accessories", ["PS5", "gaming"])


def test_ranker_applies_a_real_per_site_candidate_cap():
    text = "PS5 games console reviews. " * 20
    result = _rank_page_phrases(text, "PS5 games console reviews", 2, 2, ["PS5"], candidate_limit=1)
    assert len(result) <= 1
