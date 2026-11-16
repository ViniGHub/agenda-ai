# agenda-ia

Pergunte em português sobre a sua agenda ("o que tenho amanhã?", "quando é o dentista?", "o que ficou combinado com a padaria?") e receba a resposta citando os compromissos de onde ela saiu.

> Em construção. Fase atual: indexação e busca híbrida (sem LLM).

## Por que não é "só um RAG"

Agenda é um dado com data. Uma busca só por semelhança de texto erra justamente as perguntas mais comuns: "o que tenho amanhã?" não tem nenhuma palavra em comum com "Dentista". Por isso a consulta tem duas partes:

1. **Filtro de datas:** o intervalo da pergunta ("amanhã", "semana que vem") vira um filtro aplicado *antes* da busca.
2. **Busca híbrida dentro desse intervalo:**
   - **Por significado:** vetores de um modelo de embeddings local. "Consulta odontológica" encontra "Dentista".
   - **Por palavra:** BM25 no índice FTS5 do SQLite. Nomes próprios como "PagFácil" são encontrados mesmo se o modelo não os conhecer.
   - As duas listas são combinadas com *Reciprocal Rank Fusion*.

## Arquitetura

```
.ics (Google Agenda / Outlook)
  └─ indexar: expande recorrências → fuso de São Paulo → remove links de reunião e e-mails
       └─ embeddings locais (multilingual-e5-small, 384 dimensões)
            └─ SQLite em um arquivo: eventos + FTS5 (palavras) + sqlite-vec (vetores)

pergunta
  └─ intervalo de datas → busca vetorial + BM25 no intervalo → RRF → top 5
       └─ (próxima fase) LLM no Groq responde citando os compromissos, e a resposta é validada
```

## Decisões

| Decisão | Motivo |
|---|---|
| Embeddings no próprio computador | Grátis, sem limite de uso, e o texto da agenda não sai da máquina para ser indexado. |
| SQLite com FTS5 e sqlite-vec | Um arquivo só, sem servidor. A filtragem por data acontece dentro da busca vetorial (coluna de metadados). Migra para Postgres + pgvector se crescer. |
| Reconstrução completa do índice | Uma agenda tem centenas de eventos: reconstruir leva segundos e evita bugs de sincronização. |
| Só o nome dos participantes | Minimização de dados: o modelo precisa saber *quem*, não o e-mail. |
| Agenda fictícia na demonstração | A agenda real de uma pessoa não vai para um site público. |

## Como rodar

Requer Python 3.12 ou mais novo.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

python scripts/gerar_agenda_exemplo.py          # cria exemplos/agenda_exemplo.ics (fictícia)
python -m agenda_ia listar                      # a agenda dia a dia, como a IA vai recebê-la
python -m agenda_ia indexar                     # na 1ª vez baixa o modelo (~470 MB)
python -m agenda_ia buscar "quando é o dentista?"
python -m agenda_ia buscar "padaria" --de 2026-11-23 --ate 2026-11-29

pytest
```

A agenda fictícia pode ser conferida em dois formatos: o arquivo bruto [`exemplos/agenda_exemplo.ics`](exemplos/agenda_exemplo.ics) e a versão legível [`exemplos/agenda_exemplo.md`](exemplos/agenda_exemplo.md), que é o gabarito para validar as respostas.

Para usar a sua agenda: exporte o `.ics` do Google Agenda (Configurações → Importar e exportar) ou do Outlook, salve em `agenda_real/` (fora do git) e rode `python -m agenda_ia indexar agenda_real/arquivo.ics`.

## Primeiros resultados (busca, sem LLM)

Oito perguntas sobre a agenda fictícia; em sete o compromisso certo veio em 1º lugar e em todas ele está no top 3. O erro é instrutivo: em "consulta odontológica", a palavra "consulta" puxou a "Consulta com a nutricionista" para o topo pela busca por palavra, e o "Dentista" ficou em 2º, achado só pelo significado. A avaliação automática da próxima fase vai medir isso em dezenas de perguntas.

## Roteiro

- [x] Leitura do `.ics`, embeddings locais e SQLite
- [x] Busca híbrida com filtro de datas
- [ ] Agrupar as ocorrências de um evento recorrente nos resultados (hoje a "Daily" pode ocupar várias posições do top 5)
- [ ] Entender a pergunta com o LLM (intervalo de datas e tipo de pergunta), com saída JSON validada
- [ ] Resposta com citações validadas e "não encontrei" quando não houver base
- [ ] Avaliação automática (recall da busca, acerto das datas, fidelidade)
- [ ] Interface de chat e publicação no Hugging Face Spaces
