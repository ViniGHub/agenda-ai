"""Linha de comando.

    python -m agenda_ia listar [arquivo.ics] [--md exemplos/agenda_exemplo.md]
    python -m agenda_ia indexar [arquivo.ics]
    python -m agenda_ia buscar "quando é o dentista?" [--de 2026-11-16 --ate 2026-11-23]
    python -m agenda_ia perguntar "o que tenho amanhã?" [--agora 2026-11-16T12:00] [-v]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import store
from .calendar_loader import WEEKDAYS, Event, format_when, load_events
from .config import DEFAULT_DB, SAMPLE_CALENDAR, TZ
from .search import hybrid_search


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # accents in the Windows terminal

    parser = argparse.ArgumentParser(prog="agenda_ia")
    sub = parser.add_subparsers(dest="comando", required=True)

    show = sub.add_parser("listar", help="mostra a agenda dia a dia, como a IA vai recebê-la (sem modelo e sem banco)")
    show.add_argument("ics", nargs="?", default=str(SAMPLE_CALENDAR))
    show.add_argument("--de", type=date.fromisoformat, help="início (padrão: 90 dias atrás)")
    show.add_argument("--ate", type=date.fromisoformat, help="fim, inclusive (padrão: daqui a 180 dias)")
    show.add_argument("--md", help="também grava em Markdown neste arquivo")

    index = sub.add_parser("indexar", help="lê um .ics e grava no banco")
    index.add_argument("ics", nargs="?", default=str(SAMPLE_CALENDAR))
    index.add_argument("--db", default=str(DEFAULT_DB))
    index.add_argument("--de", type=date.fromisoformat, help="início da janela (padrão: 90 dias atrás)")
    index.add_argument("--ate", type=date.fromisoformat, help="fim da janela (padrão: daqui a 180 dias)")

    find = sub.add_parser("buscar", help="busca híbrida, sem LLM")
    find.add_argument("pergunta")
    find.add_argument("--db", default=str(DEFAULT_DB))
    find.add_argument("--de", type=date.fromisoformat)
    find.add_argument("--ate", type=date.fromisoformat, help="dia final, inclusive")
    find.add_argument("-k", type=int, default=5)

    ask = sub.add_parser("perguntar", help="resposta completa: entende a pergunta, busca e responde com o LLM")
    ask.add_argument("pergunta")
    ask.add_argument("--db", default=str(DEFAULT_DB))
    ask.add_argument("--agora", type=datetime.fromisoformat,
                     help="finge que agora é este momento (ex.: 2026-11-16T12:00), útil com a agenda de exemplo")
    ask.add_argument("-v", "--detalhes", action="store_true", help="mostra a consulta entendida, a estratégia e os tokens")

    args = parser.parse_args(argv)

    if args.comando == "listar":
        today = datetime.now(TZ).date()
        start = args.de or today - timedelta(days=90)
        end = (args.ate + timedelta(days=1)) if args.ate else today + timedelta(days=180)
        events = load_events(args.ics, start, end)
        text = agenda_markdown(events, args.ics, start, end - timedelta(days=1))
        if args.md:
            Path(args.md).write_text(text, encoding="utf-8")
            print(f"{len(events)} compromissos gravados em {args.md}")
        else:
            print(text)
        return

    from .embeddings import E5Embedder  # heavy import: torch and the model

    if args.comando == "indexar":
        today = datetime.now(TZ).date()
        start = args.de or today - timedelta(days=90)
        end = args.ate or today + timedelta(days=180)
        events = load_events(args.ics, start, end)
        embedder = E5Embedder()
        vectors = embedder.passages([e.search_text() for e in events])
        conn = store.connect(args.db)
        store.rebuild(conn, events, vectors, embedder.dim)
        print(f"{len(events)} compromissos indexados ({start:%d/%m/%Y} a {end:%d/%m/%Y}) em {args.db}")
        return

    if args.comando == "perguntar":
        from .assistant import Assistant
        from .llm import GroqModel, LLMError

        try:
            model = GroqModel()
        except LLMError as error:
            sys.exit(str(error))
        now = args.agora.replace(tzinfo=TZ) if args.agora and args.agora.tzinfo is None else args.agora
        result = Assistant(store.SqliteEventStore(store.connect(args.db)), E5Embedder(), model).ask(args.pergunta, now)
        print(result.answer.text)
        if result.answer.cited:
            print("\nFontes:")
            for stored in result.answer.cited:
                print(f"  - {stored.event.title}: {format_when(stored.event)}")
        if args.detalhes:
            q = result.query
            print(f"\n[consulta] tipo={q.tipo} de={q.de} ate={q.ate} busca={q.busca!r}"
                  + (f" (reserva: {result.query_note})" if result.query_note else ""))
            print(f"[busca] {result.strategy}: {len(result.retrieved)} compromissos recuperados")
            if result.answer.note:
                print(f"[guarda] {result.answer.note}")
            print(f"[custo] {result.usage.total} tokens em {result.usage.calls} chamadas, {result.seconds:.1f} s")
        return

    conn = store.connect(args.db)
    start = datetime.combine(args.de, time.min, tzinfo=TZ) if args.de else None
    end = datetime.combine(args.ate + timedelta(days=1), time.min, tzinfo=TZ) if args.ate else None
    hits = hybrid_search(store.SqliteEventStore(conn), E5Embedder(), args.pergunta, start, end, k=args.k)
    if not hits:
        print("Nenhum compromisso encontrado.")
    for position, hit in enumerate(hits, start=1):
        event = hit.stored.event
        print(f"{position}. {event.title} | {format_when(event)} | score {hit.score:.4f} | achado por: {', '.join(hit.found_by)}")


def agenda_markdown(events: list[Event], source: str, first: date, last: date) -> str:
    """Day-by-day listing of every occurrence, with the same fields the model will see."""
    lines = [
        f"# Agenda: {Path(source).name}",
        "",
        f"De {first:%d/%m/%Y} a {last:%d/%m/%Y}: {len(events)} compromissos "
        "(recorrências já expandidas, cancelados fora, horários de São Paulo).",
    ]
    current_day = None
    for event in events:
        if event.start.date() != current_day:
            current_day = event.start.date()
            lines += ["", f"## {WEEKDAYS[current_day.weekday()]}, {current_day:%d/%m/%Y}", ""]
        when = "dia inteiro" if event.all_day else f"{event.start:%H:%M}–{event.end:%H:%M}"
        if event.all_day and (event.end - event.start).days > 1:
            when = f"dia inteiro, até {(event.end - timedelta(days=1)):%d/%m}"
        lines.append(f"- **{when}: {event.title}**")
        if event.location:
            lines.append(f"  - Local: {event.location}")
        if event.attendees:
            lines.append(f"  - Participantes: {', '.join(event.attendees)}")
        if event.description:
            lines.append(f"  - Notas: {event.description}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
