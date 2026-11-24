from datetime import date, datetime, timedelta
from email import message_from_bytes, policy

from agenda_ia.events.calendar_loader import Event
from agenda_ia.config import TZ
from agenda_ia.google.daily_email import compose, message, todays_events
from agenda_ia.storage.store import SqliteEventStore

DAY = date(2026, 11, 18)  # a Wednesday


def _event(title, start, hours=1, all_day=False, **fields):
    end = start + (timedelta(days=hours) if all_day else timedelta(hours=hours))
    return Event(f"uid-{title}", title, start, end, all_day, fields.get("location", ""), fields.get("people", ()),
                 fields.get("notes", ""))


def test_only_todays_events_come_including_ones_still_going(fake_embedder):
    at = lambda d, h=0: datetime(2026, 11, d, h, tzinfo=TZ)  # noqa: E731
    events = [
        _event("Viagem", at(16), hours=4, all_day=True),  # 16 to 19: still going today
        _event("Ontem", at(17, 10)),
        _event("Dentista", at(18, 15)),
        _event("Feriado", at(18), all_day=True),
        _event("Amanhã", at(19, 9)),
    ]
    store = SqliteEventStore.in_memory(events, fake_embedder.passages([e.search_text() for e in events]), fake_embedder.dim)
    assert [e.title for e in todays_events(store, DAY)] == ["Viagem", "Feriado", "Dentista"]  # all-day first


def test_the_mail_lists_each_appointment_with_its_details():
    events = [
        _event("Feriado", datetime(2026, 11, 18, tzinfo=TZ), all_day=True),
        _event("Dentista", datetime(2026, 11, 18, 15, tzinfo=TZ), location="Clínica Sorriso", people=("Ana",),
               notes="levar exames"),
    ]
    subject, text, page = compose(events, DAY, "https://agenda-ia.web.app")
    assert subject == "Agenda de hoje (quarta, 18/11): 2 compromissos"
    assert "dia todo     Feriado" in text and "15:00–16:00  Dentista" in text
    assert "Clínica Sorriso · com Ana" in text and "levar exames" in text
    assert "<strong>Dentista</strong>" in page and "https://agenda-ia.web.app" in page


def test_a_free_day_still_gets_its_mail():
    subject, text, _ = compose([], DAY, "https://x")
    assert subject.endswith(": dia livre") and "Nenhum compromisso" in text


def test_titles_cannot_inject_html():
    _, _, page = compose([_event("<script>x</script>", datetime(2026, 11, 18, 9, tzinfo=TZ))], DAY, "https://x")
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_the_message_goes_from_the_person_to_themself():
    raw = message("eu@example.com", "Agenda de hoje (quarta, 18/11): dia livre", "texto", "<p>texto</p>")
    parsed = message_from_bytes(raw, policy=policy.default)
    assert parsed["To"] == parsed["From"] == "eu@example.com"
    assert parsed["Subject"] == "Agenda de hoje (quarta, 18/11): dia livre"  # accents survive the header encoding
    assert {part.get_content_type() for part in parsed.iter_parts()} == {"text/plain", "text/html"}
