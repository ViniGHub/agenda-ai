from datetime import datetime, timedelta

from agenda_ia.events.calendar_loader import Event
from agenda_ia.config import TZ
from agenda_ia.google.events import event_from_google, google_body, plain_text, split_people


def _item(**fields):
    return {"id": "abc_20261119T180000Z", "iCalUID": "abc@google.com", "etag": '"1"', "status": "confirmed",
            "summary": "Dentista", "start": {"dateTime": "2026-11-19T18:00:00Z"},
            "end": {"dateTime": "2026-11-19T19:00:00Z"}, **fields}


def test_timed_event_comes_in_sao_paulo_time_with_the_ical_uid():
    event = event_from_google(_item(location="  Clínica   Sorriso "))
    assert event.uid == "abc@google.com" and event.title == "Dentista" and not event.all_day
    assert event.start == datetime(2026, 11, 19, 15, 0, tzinfo=TZ) and event.start.tzinfo == TZ
    assert event.end - event.start == timedelta(hours=1) and event.location == "Clínica Sorriso"


def test_all_day_events_keep_googles_exclusive_end():
    event = event_from_google(_item(start={"date": "2026-11-20"}, end={"date": "2026-11-22"}))
    assert event.all_day and event.start == datetime(2026, 11, 20, tzinfo=TZ)
    assert event.end == datetime(2026, 11, 22, tzinfo=TZ)  # two days: 20 and 21


def test_people_come_from_attendees_and_the_description_line_without_the_person_or_rooms():
    event = event_from_google(_item(
        description="levar os exames\n\nCom: Ana; Bruno",
        attendees=[
            {"email": "eu@example.com", "self": True},
            {"email": "carla.souza@example.com"},
            {"email": "sala@resource.calendar.google.com", "resource": True},
            {"email": "ana@example.com", "displayName": "Ana"},
        ],
    ))
    assert event.attendees == ("Ana", "Bruno", "carla.souza")
    assert event.description == "levar os exames"


def test_html_descriptions_become_plain_text():
    assert plain_text("Levar <b>RG</b><br>e o pedido &amp; exames") == "Levar RG\ne o pedido & exames"
    event = event_from_google(_item(description="<p>Sala 3</p><p>Meet: https://meet.google.com/x</p>"))
    assert event.description == "Sala 3 Meet:"  # links are dropped like in the .ics import


def test_cancelled_occurrences_and_items_without_start_are_skipped():
    assert event_from_google(_item(status="cancelled")) is None
    assert event_from_google(_item(start={})) is None


def test_untitled_events_get_a_title():
    assert event_from_google(_item(summary="   ")).title == "(sem título)"


def test_body_round_trip_keeps_every_field():
    event = Event("x", "Almoço", datetime(2026, 11, 20, 12, tzinfo=TZ), datetime(2026, 11, 20, 13, 30, tzinfo=TZ),
                  False, "Padaria", ("Ana", "Bruno"), "reservar mesa")
    body = google_body(event)
    assert body["start"] == {"dateTime": "2026-11-20T12:00:00-03:00", "timeZone": "America/Sao_Paulo"}
    assert "attendees" not in body  # nobody gets an invitation e-mail
    back = event_from_google({**body, "id": "g1", "iCalUID": "x", "status": "confirmed"})
    assert back == event


def test_all_day_body():
    event = Event("y", "Feriado", datetime(2026, 11, 20, tzinfo=TZ), datetime(2026, 11, 21, tzinfo=TZ), True, "", (), "")
    body = google_body(event)
    assert body["start"] == {"date": "2026-11-20"} and body["end"] == {"date": "2026-11-21"}
    assert body["description"] == ""


def test_split_people_only_reads_a_last_line():
    assert split_people("Com: Ana\nlevar bolo") == ("Com: Ana\nlevar bolo", [])
    assert split_people("Com: Ana ;  ; Bruno ") == ("", ["Ana", "Bruno"])
