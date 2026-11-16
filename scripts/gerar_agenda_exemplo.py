"""Gera a agenda fictícia da Ana, usada na demonstração pública e na avaliação.

    python scripts/gerar_agenda_exemplo.py                  # semana atual
    python scripts/gerar_agenda_exemplo.py --base 2026-11-16 --saida exemplos/agenda_exemplo.ics

Tudo é inventado. Os e-mails usam example.com, um domínio reservado para exemplos.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vCalAddress, vText

TZ = ZoneInfo("America/Sao_Paulo")
ROOT = Path(__file__).resolve().parents[1]

TEAMS_BOILERPLATE = """
________________________________________________________________________________
Reunião do Microsoft Teams
Ingressar no computador, aplicativo móvel ou dispositivo de sala
https://teams.example.com/l/meetup-join/19%3ameeting_exemplo
ID da Reunião: 123 456 789 0
Senha: exemplo
"""

TIME = [("Carla Mendes", "carla.mendes"), ("Bruno Alves", None), ("Júlia Rocha", "julia.rocha")]


def build(base: date) -> Calendar:
    monday = base - timedelta(days=base.weekday())
    vacation_start = monday + timedelta(days=33)
    recurring_until = datetime.combine(vacation_start - timedelta(days=1), time(23, 59), tzinfo=TZ)

    cal = Calendar()
    cal.add("prodid", "-//agenda-ia//agenda de exemplo//PT-BR")
    cal.add("version", "2.0")
    counter = iter(range(1, 1000))

    def at(offset: int, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(monday + timedelta(days=offset), time(hour, minute), tzinfo=TZ)

    def day(offset: int) -> date:
        return monday + timedelta(days=offset)

    def add(summary, start, end, *, location="", attendees=(), description="", rrule=None, status=None):
        event = Event()
        event.add("uid", f"exemplo-{next(counter):03d}@agenda-ia.example.com")
        event.add("dtstamp", datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")))
        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)
        if location:
            event.add("location", location)
        for name, user in attendees:
            address = vCalAddress(f"mailto:{(user or name.split()[0].lower())}@example.com")
            if user:  # one attendee without a display name, like real invitations sometimes have
                address.params["cn"] = vText(name)
            event.add("attendee", address, encode=0)
        if description:
            event.add("description", description)
        if rrule:
            event.add("rrule", rrule)
        if status:
            event.add("status", status)
        cal.add_component(event)

    # Recorrentes (começam duas semanas antes e param antes das férias)
    add("Daily do time", at(-14, 9, 30), at(-14, 9, 45), location="Google Meet", attendees=TIME,
        description="Status rápido: o que fiz ontem, o que faço hoje e bloqueios.",
        rrule={"freq": "weekly", "byday": ["MO", "TU", "WE", "TH", "FR"], "until": recurring_until})
    add("Academia", at(-14, 7), at(-14, 8), location="Academia do bairro",
        description="Treino de força. Levar garrafa de água.",
        rrule={"freq": "weekly", "byday": ["MO", "WE", "FR"], "until": recurring_until})
    add("Aula de inglês", at(-13, 19), at(-13, 20), location="Online", attendees=[("Laura Smith", "laura.smith")],
        description="Conversação. Preparar uma apresentação curta sobre o projeto atual.",
        rrule={"freq": "weekly", "byday": ["TU", "TH"], "until": recurring_until})
    add("1:1 com a Carla", at(-11, 16), at(-11, 16, 30), attendees=[TIME[0]],
        description="Conversa de acompanhamento com a gestora: prioridades, carreira e bloqueios.",
        rrule={"freq": "weekly", "interval": 2, "until": recurring_until})
    add("Planning da sprint", at(0, 10), at(0, 11, 30), location="Sala Ipê", attendees=TIME,
        description="Definir o escopo da sprint. Trazer as estimativas das tarefas do app de pedidos.",
        rrule={"freq": "weekly", "interval": 2, "until": recurring_until})
    add("Retro da sprint", at(11, 16), at(11, 17), location="Sala Ipê", attendees=TIME,
        description="O que funcionou, o que não funcionou e o que vamos mudar.",
        rrule={"freq": "weekly", "interval": 2, "until": recurring_until})

    # Passado recente
    add("Feedback semestral com a Carla", at(-10, 15), at(-10, 16), attendees=[TIME[0]],
        description="Pontos fortes: comunicação com o cliente e qualidade do código. A melhorar: estimativas de "
                    "prazo e pedir ajuda mais cedo. Meta para o próximo semestre: liderar a entrega do app de pedidos.")
    add("Happy hour do time", at(-3, 18, 30), at(-3, 21), location="Bar do Zé, Vila Madalena", attendees=TIME,
        description="Comemoração da entrega da versão 2.0.")

    # Esta semana
    add("Dentista", at(1, 8), at(1, 9), location="Clínica Sorriso, Rua das Flores, 120",
        attendees=[("Dr. Marcos Lima", "marcos.lima")],
        description="Limpeza e avaliação do siso. Chegar 10 minutos antes e levar a carteirinha do convênio.")
    add("Kickoff com a Padaria Pão de Mel", at(1, 14), at(1, 15, 30), location="Google Meet",
        attendees=[("Rita Oliveira", "rita.oliveira"), TIME[0]],
        description="Início do app de pedidos da padaria. Levar o protótipo do Figma. Dúvidas em aberto: "
                    "integração com a maquininha de cartão e horário de corte para encomendas.")
    add("Almoço com o Bruno", at(2, 12), at(2, 13), location="Restaurante Sabor Caseiro", attendees=[TIME[1]],
        description="Cancelado: ele vai estar de folga.", status="CANCELLED")
    add("Call com a PagFácil", at(2, 11), at(2, 11, 45), attendees=[("Pedro Santos", "pedro.santos")],
        description="Fornecedor de pagamentos: negociar a taxa do Pix e o prazo de repasse. "
                    "Pedir a documentação da API." + TEAMS_BOILERPLATE)
    add("Entrega do relatório trimestral", day(3), day(4),
        description="Prazo final para enviar o relatório de indicadores do trimestre para a diretoria.")
    add("Workshop: RAG na prática", at(4, 14), at(4, 17), location="Auditório, 3º andar",
        description="Workshop interno sobre busca semântica e embeddings. Levar o notebook carregado.")
    add("Aniversário da mãe", day(5), day(6),
        description="Almoço em família às 13h. Comprar o presente: livro de receitas e flores.")
    add("Veterinário da Mia", at(5, 10), at(5, 10, 30), location="Clínica Pet Amigo",
        description="Vacina anual da gata Mia. Levar a carteira de vacinação.")

    # Próximas semanas
    add("Revisão do carro", at(8, 8), at(8, 12), location="Oficina do Paulo",
        description="Revisão dos 40 mil km: troca de óleo e das pastilhas de freio.")
    add("Check-up anual", at(9, 7, 30), at(9, 8, 30), location="Laboratório Vida",
        description="Exame de sangue. Jejum de 12 horas.")
    add("Acompanhamento com a Padaria Pão de Mel", at(10, 14), at(10, 15), location="Google Meet",
        attendees=[("Rita Oliveira", "rita.oliveira")],
        description="Apresentar a primeira versão do cardápio no app e trazer a resposta sobre a maquininha.")
    add("Conferência de front-end em Curitiba", day(15), day(17), location="Curitiba",
        description="Voo às 6h do primeiro dia; hotel perto do centro de eventos. "
                    "Palestra sobre acessibilidade às 15h do segundo dia.")
    add("Consulta com a nutricionista", at(22, 18), at(22, 19), location="Online",
        description="Retorno: levar o diário alimentar das últimas duas semanas.")
    add("Férias", day(33), day(49), description="Férias de fim de ano. Viagem para a praia com a família.")

    if hasattr(cal, "add_missing_timezones"):
        cal.add_missing_timezones()
    return cal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=date.fromisoformat, default=datetime.now(TZ).date(),
                        help="um dia da semana que será a 'semana atual' da agenda")
    parser.add_argument("--saida", type=Path, default=ROOT / "exemplos" / "agenda_exemplo.ics")
    args = parser.parse_args()
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_bytes(build(args.base).to_ical())
    print(f"Agenda de exemplo gerada em {args.saida} (semana de {args.base - timedelta(days=args.base.weekday()):%d/%m/%Y})")


if __name__ == "__main__":
    main()
