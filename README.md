# agenda-ia

Pergunte em português sobre a sua agenda ("o que tenho amanhã?", "quando é o dentista?", "o que ficou combinado com a padaria?") e receba a resposta citando os compromissos de onde ela saiu.

> Em construção. Pronto: indexação, busca híbrida, resposta com o LLM e interface web (React + FastAPI). Falta: avaliação automática.

A interface mostra, ao lado de cada resposta, como ela foi montada: como a pergunta foi entendida, o que a busca trouxe, o que foi citado e quanto custou. A aba "Agenda completa" é o gabarito para conferir.

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
  1. entender (LLM, JSON validado com Pydantic): tipo (listar | buscar | contar), intervalo de datas, termos
  2. recuperar:
       listar/contar sem termos → todos os compromissos do intervalo, em ordem
       buscar                   → busca vetorial + BM25 no intervalo → RRF → top 5, recorrentes agrupados
       listar/contar com termos → busca híbrida ampla (top 20), sem agrupar, em ordem
  3. responder (LLM): só com os compromissos numerados, citando os números → validação em código

web/ (React + Vite + TypeScript)  ──HTTP/JSON──>  src/agenda_ia/api.py (FastAPI)  ──>  Assistant.ask()
     POST /api/perguntar · GET /api/agenda · GET /api/status
```

O front e o back só se conhecem pelo contrato JSON: os modelos Pydantic em [`api.py`](src/agenda_ia/api.py) e os tipos espelhados em [`web/src/types.ts`](web/src/types.ts). Em produção, o FastAPI serve o React já compilado, então é uma origem só (sem CORS) e a chave do Groq nunca chega ao navegador.

O LLM é o `openai/gpt-oss-120b` no plano gratuito do Groq (8.000 tokens por minuto). Cada pergunta faz duas chamadas e gasta uns 2 a 4 mil tokens.

## Guardas

O que o prompt pede, o código confere:

- **Datas:** modelos de linguagem erram aritmética de datas, então o prompt já traz as datas calculadas ("ter 17/11/2026 = 2026-11-17 (amanhã)", "Semana que vem: 2026-11-23 a 2026-11-29"). Se o JSON vier inválido ou com intervalo invertido, a consulta cai para uma busca simples, sem filtro.
- **Citações:** a resposta precisa citar os números dos compromissos recuperados. Citações inexistentes são descartadas; uma resposta que diz ter encontrado algo sem citar nada válido é trocada por "Não consegui confirmar isso na agenda."
- **Nada recuperado:** a resposta é escrita sem chamar o modelo (economiza tokens e não dá margem para inventar).
- **Falha do modelo:** o usuário ainda recebe os compromissos mais próximos da pergunta.
- **Injeção de prompt:** os compromissos vão delimitados em `<compromissos>` e o prompt manda tratá-los como dado. (Isso reduz o risco, não elimina; por isso as citações são verificadas fora do modelo.)
- **Recorrentes:** numa pergunta sobre um compromisso específico, uma série ("Daily do time") conta uma vez só, na próxima ocorrência. Em listagens e contagens, cada ocorrência conta.

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

# Resposta completa com o LLM: copie .env.example para .env e ponha a sua GROQ_API_KEY
# --agora finge a data atual, para a agenda de exemplo (que é da semana de 16/11/2026)
python -m agenda_ia perguntar "o que tenho amanhã?" --agora 2026-11-16T12:00 -v

pytest                                          # 38 testes, sem rede: o LLM e os embeddings são simulados
```

Interface web (requer Node 20 ou mais novo):

```bash
pip install -e ".[api]"
cd web && npm install && npm run build && cd ..
uvicorn agenda_ia.api:app --port 8000           # abre em http://localhost:8000

# desenvolvimento do front, com recarga automática: deixe o uvicorn rodando e, em outro terminal,
cd web && npm run dev                           # http://localhost:5173 (encaminha /api para a porta 8000)
```

Sem a variável `AGENDA_ICS`, a API gera a agenda fictícia em volta da semana atual. Com `AGENDA_ICS=agenda_real/minha.ics`, usa a sua (rode só localmente: a aba "Agenda completa" mostra tudo a quem acessar).

## Publicação (Google Cloud Run)

O [`Dockerfile`](Dockerfile) compila o React e roda a API num container só; o modelo de embeddings já vai dentro da imagem, então o container não acessa o Hugging Face em produção. O script [`deploy/cloud-run.sh`](deploy/cloud-run.sh), rodado no Cloud Shell, faz tudo:

1. ativa as APIs necessárias;
2. guarda a chave do Groq no Secret Manager (você digita a chave; ela não aparece na tela);
3. cria uma conta de serviço que só pode ler esse segredo;
4. compila a imagem no Cloud Build e publica o serviço.

Configuração pensada para custo zero: escala até zero quando ninguém usa, no máximo 1 instância (teto de gasto mesmo sob abuso) e 20 perguntas por visitante a cada 10 minutos (para não esgotar a cota gratuita do Groq). O preço disso é a partida a frio: depois de um tempo parado, o primeiro acesso leva de 30 a 60 segundos.

A agenda fictícia pode ser conferida em dois formatos: o arquivo bruto [`exemplos/agenda_exemplo.ics`](exemplos/agenda_exemplo.ics) e a versão legível [`exemplos/agenda_exemplo.md`](exemplos/agenda_exemplo.md), que é o gabarito para validar as respostas.

Para usar a sua agenda: exporte o `.ics` do Google Agenda (Configurações → Importar e exportar) ou do Outlook, salve em `agenda_real/` (fora do git) e rode `python -m agenda_ia indexar agenda_real/arquivo.ics`.

## Primeiros resultados

Rodada manual na agenda fictícia, fingindo que agora é segunda, 16/11/2026, ao meio-dia. A avaliação automática (próxima fase) vai medir isso em dezenas de perguntas.

**Só a busca, sem LLM (8 perguntas):** em 7 o compromisso certo veio em 1º lugar e em todas ele estava no top 3. O erro foi instrutivo: em "consulta odontológica", a palavra "consulta" puxou a "Consulta com a nutricionista" para o topo pela busca por palavra, e o "Dentista" ficou em 2º, achado só pelo significado.

**Com o LLM (10 perguntas):** as 10 respostas certas, com as fontes certas.

| Pergunta | Entendida como | Resposta |
|---|---|---|
| o que tenho amanhã? | listar 17/11 | os 4 compromissos de terça, em ordem |
| quando é minha consulta odontológica? | buscar "consulta odontológica" a partir de hoje | terça, 17/11, às 8h (Dentista) |
| quantas aulas de inglês tenho esta semana? | contar "aula", 16/11 a 22/11 | 2 |
| o que ficou de ponto a melhorar no meu feedback? | buscar | estimativas de prazo e pedir ajuda mais cedo |
| tenho algo com a padaria semana que vem? | buscar "padaria", 23/11 a 29/11 | quinta, 26/11, às 14h |
| tenho show do Coldplay marcado? | buscar | não encontrei isso na agenda |
| como está meu fim de semana? | listar 21/11 a 22/11 | aniversário da mãe e veterinário |

O caso da "consulta odontológica" mostra a divisão de trabalho: a busca só precisa trazer o certo entre os 5 primeiros, e o modelo escolhe.

A rodada também pegou um defeito de texto: o modelo dizia "férias começam sábado, 19/12, às 0h". O prompt agora manda não dar horário para compromisso de dia inteiro.

**Custo:** cerca de 2 mil tokens por pergunta (quase tudo entrada: o calendário de datas e os compromissos). Dentro do limite, a resposta sai em 1 a 2 segundos. Acima de umas 4 perguntas por minuto, o plano gratuito (8.000 tokens/minuto) devolve o erro 429, o cliente espera e tenta de novo, e a resposta passa a levar de 8 a 15 segundos.

## Roteiro

- [x] Leitura do `.ics`, embeddings locais e SQLite
- [x] Busca híbrida com filtro de datas
- [x] Agrupar as ocorrências de um evento recorrente nos resultados
- [x] Entender a pergunta com o LLM (intervalo de datas e tipo de pergunta), com saída JSON validada
- [x] Resposta com citações validadas e "não encontrei" quando não houver base
- [ ] Avaliação automática (recall da busca, acerto das datas, fidelidade)
- [x] Interface web em React + API em FastAPI, com limite de perguntas por visitante
- [ ] Publicação no Google Cloud Run
