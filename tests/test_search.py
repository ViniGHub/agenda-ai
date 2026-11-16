from datetime import date, datetime, time

from agenda_ia.config import TZ
from agenda_ia.search import fts_query, hybrid_search, reciprocal_rank_fusion


def _day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=TZ)


def test_fts_query_drops_stopwords_and_stems():
    assert fts_query("Quando é a reunião com a padaria?") == '"reun"* OR "pada"*'


def test_fts_query_is_safe_with_quotes_and_operators():
    query = fts_query('dentista" OR drop NEAR( *')
    assert query == '"denti"* OR "drop"* OR "near"*'  # operators become plain quoted terms


def test_rrf_rewards_items_found_by_both():
    fused = reciprocal_rank_fusion({"significado": [1, 2, 3], "palavra": [3, 1]})
    assert [item for item, _, _ in fused] == [1, 3, 2]
    assert fused[0][2] == ("significado", "palavra")


def test_hybrid_search_puts_events_found_by_both_on_top(sample_db, fake_embedder):
    hits = hybrid_search(sample_db, fake_embedder, "dentista")
    assert hits[0].stored.event.title == "Dentista"
    assert set(hits[0].found_by) == {"significado", "palavra"}


def test_keyword_search_finds_proper_names(sample_db):
    ids = sample_db.keyword_search("o que vou falar com a PagFácil?", 5, None, None)
    assert sample_db.get_events(ids[:1])[ids[0]].event.title == "Call com a PagFácil"


def test_date_window_filters_both_searches(sample_db, fake_embedder):
    hits = hybrid_search(sample_db, fake_embedder, "padaria", _day(date(2026, 11, 26)), _day(date(2026, 11, 27)))
    titles = [h.stored.event.title for h in hits]
    assert "Acompanhamento com a Padaria Pão de Mel" in titles
    assert "Kickoff com a Padaria Pão de Mel" not in titles
    assert all(date(2026, 11, 26) == h.stored.event.start.date() for h in hits)


def test_events_between_lists_in_time_order(sample_db):
    tuesday = sample_db.events_between(int(_day(date(2026, 11, 17)).timestamp()), int(_day(date(2026, 11, 18)).timestamp()))
    assert [s.event.title for s in tuesday] == ["Dentista", "Daily do time", "Kickoff com a Padaria Pão de Mel", "Aula de inglês"]
