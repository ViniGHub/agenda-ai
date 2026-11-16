from datetime import date, datetime

from agenda_ia.calendar_loader import clean_description, format_when, load_events
from agenda_ia.config import TZ


def _events(sample_ics):
    return load_events(sample_ics, date(2026, 11, 1), date(2027, 2, 1))


def test_cancelled_events_are_skipped(sample_ics):
    titles = {e.title for e in _events(sample_ics)}
    assert "Almoço com o Bruno" not in titles
    assert "Dentista" in titles


def test_recurring_events_are_expanded_and_stop_before_vacation(sample_ics):
    events = _events(sample_ics)
    week = [e for e in events if e.title == "Daily do time" and date(2026, 11, 16) <= e.start.date() <= date(2026, 11, 22)]
    assert len(week) == 5
    assert not [e for e in events if e.title == "Daily do time" and e.start.date() >= date(2026, 12, 19)]


def test_times_are_in_sao_paulo(sample_ics):
    dentist = next(e for e in _events(sample_ics) if e.title == "Dentista")
    assert dentist.start == datetime(2026, 11, 17, 8, 0, tzinfo=TZ)
    assert format_when(dentist) == "terça-feira, 17/11/2026, 08:00–09:00"


def test_all_day_events(sample_ics):
    events = {e.title: e for e in _events(sample_ics)}
    assert events["Aniversário da mãe"].all_day
    assert format_when(events["Aniversário da mãe"]) == "sábado, 21/11/2026 (dia inteiro)"
    assert format_when(events["Conferência de front-end em Curitiba"]) == "terça-feira, 01/12/2026 a 02/12/2026 (dia inteiro)"


def test_attendees_keep_names_not_emails(sample_ics):
    daily = next(e for e in _events(sample_ics) if e.title == "Daily do time")
    assert "Carla Mendes" in daily.attendees
    assert "bruno" in daily.attendees  # no display name: only the part before the @
    assert all("@" not in name for e in _events(sample_ics) for name in e.attendees)


def test_meeting_boilerplate_is_removed(sample_ics):
    call = next(e for e in _events(sample_ics) if e.title == "Call com a PagFácil")
    assert "documentação da API" in call.description
    assert "Teams" not in call.description and "http" not in call.description and "Senha" not in call.description


def test_clean_description_keeps_notes_and_drops_links():
    text = "Levar contrato\nVer https://exemplo.com/doc\n-----\nJoin Zoom Meeting\nhttps://zoom.example.com"
    assert clean_description(text) == "Levar contrato Ver"
