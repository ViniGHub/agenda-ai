# Arquitetura do agenda-ia

Como o código se organiza e o que roda, em ordem, em cada pedido. O que roda fora do código (Cloud Run, Firebase, Neon, segredos) está em [infraestrutura.md](infraestrutura.md).

Os números de linha valem para a versão de 24/11/2026, já com as pastas por funcionalidade. Quando o código mudar, procure pelo nome da função, que é a referência estável.

---

## 1. Visão geral

```
 Navegador: React (web/src)
   App.tsx ─► pages/Workspace.tsx ─► features/chat · features/agenda · features/google
                    │  api/client.ts: fetch /api/{demo|minha}/...
                    ▼
 FastAPI: api/server.py (rotas, sessão, limites, trava global) e api/auth.py (login)
   │
   ├─ chat/       o fluxo do chat (Assistant.ask)
   │    ├─ understanding.py ─► Groq  (1ª chamada: o que a mensagem pede)
   │    ├─ drafting.py ──────► Groq  (anotar: frase → campos do formulário)
   │    ├─ search/hybrid.py ─► storage (busca híbrida: significado + palavra)
   │    └─ answering.py ─────► Groq  (resposta com citações conferidas)
   ├─ events/     o compromisso: Event, leitura de .ics, formulário (EventDraft), agenda de exemplo
   ├─ google/     Google Agenda e Gmail: conexão, sincronização, e-mail das 8h
   └─ ai/         clientes de modelo: llm.py (Groq), embeddings.py (e5-small, local), speech.py (Whisper)

 Dados (storage/): store.py    → protocolo EventStore e SQLite em memória (demo, testes, sem banco)
                   postgres.py → Postgres + pgvector no Neon (agendas privadas)
```

Três ideias sustentam o desenho:

- **O modelo nunca grava nada.** Ele classifica, extrai campos e escreve respostas. O que vai para o banco passa pelo `EventDraft` (as mesmas validações do formulário), e a decisão de salvar é tomada em código.
- **Datas são calculadas em código.** Os prompts recebem a lista de datas exatas em volta de hoje (`date_reference`, [chat/understanding.py:52](../src/agenda_ia/chat/understanding.py#L52)), porque modelos erram aritmética de datas.
- **Toda resposta do modelo é desconfiada.** O JSON é validado com pydantic, as citações fora da lista são descartadas e os campos extraídos de uma frase precisam usar palavras da própria frase.

---

## 2. Mapa dos arquivos

### Backend (`src/agenda_ia/`)

Uma pasta por funcionalidade. Entre pastas, os imports são relativos (`from ..storage.store import StoredEvent`).

| Pasta | Para quê |
|---|---|
| `api/` | A camada HTTP: rotas, contratos, sessão e login |
| `chat/` | O fluxo da conversa: entender, anotar, responder (e, em construção, alterar) |
| `search/` | A busca híbrida e os termos da busca por palavra |
| `storage/` | Onde os compromissos ficam: o protocolo e as duas implementações |
| `events/` | O compromisso em si: modelo, leitura de .ics, validação do formulário e agenda de exemplo |
| `google/` | Tudo que fala com o Google: conexão, sincronização, conversão de eventos e e-mail das 8h |
| `ai/` | Os clientes de modelo: LLM, embeddings e transcrição |

| Arquivo | Papel | Chama |
|---|---|---|
| [api/server.py](../src/agenda_ia/api/server.py) | App FastAPI: inicialização, contratos (`*Out`, `*In`), rotas, limites, sessão, trava global. O `api/__init__.py` reexporta `app`, então o comando continua `uvicorn agenda_ia.api:app` | quase tudo |
| [api/auth.py](../src/agenda_ia/api/auth.py) | Login com Google (popup e redirecionamento), cookie de sessão assinado, lista de e-mails permitidos | Google OAuth |
| [chat/assistant.py](../src/agenda_ia/chat/assistant.py) | `Assistant.ask`: entender → anotar, ou entender → buscar → responder | understanding, drafting, search, answering |
| [chat/understanding.py](../src/agenda_ia/chat/understanding.py) | Mensagem → `Query` (tipo, de, ate, busca) | ai/llm |
| [chat/drafting.py](../src/agenda_ia/chat/drafting.py) | Frase → `Suggestion` (campos do formulário); `explicit_request` decide se é ordem | ai/llm |
| [chat/answering.py](../src/agenda_ia/chat/answering.py) | `compose`: prompt da resposta e guardas das citações | ai/llm |
| [chat/changing.py](../src/agenda_ia/chat/changing.py) | Em construção (passo 1a): alterar e apagar pelo chat | drafting, search, storage |
| [search/hybrid.py](../src/agenda_ia/search/hybrid.py) | `hybrid_search`: vetor + palavra, fundidos por RRF | storage, ai/embeddings |
| [search/text.py](../src/agenda_ia/search/text.py) | `query_terms`: termos e radicais para a busca por palavra | – |
| [storage/store.py](../src/agenda_ia/storage/store.py) | Protocolo `EventStore`, `StoredEvent`, origens e `SqliteEventStore` (FTS5 + sqlite-vec) | search/text |
| [storage/postgres.py](../src/agenda_ia/storage/postgres.py) | `PostgresEventStore` (pgvector + tsvector, sempre filtrado por dono) e `PostgresConnections` | psycopg |
| [events/calendar_loader.py](../src/agenda_ia/events/calendar_loader.py) | `Event`, leitura de .ics com recorrências, `format_when` | icalendar |
| [events/editing.py](../src/agenda_ia/events/editing.py) | `EventDraft` (validação do formulário) e `import_events` | calendar_loader |
| [events/sample.py](../src/agenda_ia/events/sample.py) | Agenda fictícia da demo, montada em volta da semana atual | icalendar |
| [google/link.py](../src/agenda_ia/google/link.py) | Conexão OAuth, token cifrado, cliente REST e `GoogleService` | Google REST |
| [google/sync.py](../src/agenda_ia/google/sync.py) | `sync`, `push_new` e `push_update` | google/events, storage |
| [google/events.py](../src/agenda_ia/google/events.py) | Converte item do Google ↔ `Event` | – |
| [google/daily_email.py](../src/agenda_ia/google/daily_email.py) | Monta o e-mail das 8h (sem LLM) | storage |
| [ai/llm.py](../src/agenda_ia/ai/llm.py) | `GroqModel.complete_json` (único ponto de chamada ao LLM), `Usage`, `LLMError` | Groq |
| [ai/embeddings.py](../src/agenda_ia/ai/embeddings.py) | `E5Embedder`: texto → vetor normalizado | sentence-transformers |
| [ai/speech.py](../src/agenda_ia/ai/speech.py) | `GroqTranscriber`: áudio → texto, filtrando frases inventadas pelo Whisper | Groq |
| [config.py](../src/agenda_ia/config.py) | Constantes: fuso, modelos, caminhos | – |
| [\_\_main\_\_.py](../src/agenda_ia/__main__.py) | CLI local (`listar`, `indexar`, `buscar`, `perguntar`); não participa do servidor | – |

### Front-end (`web/src/`)

| Pasta | Para quê |
|---|---|
| `api/` | A conversa com o back: uma função por rota e os tipos do contrato |
| `pages/` | As duas telas: a capa e a agenda aberta |
| `features/` | Uma pasta por área da tela: `chat`, `agenda`, `google` e `startup` (o servidor acordando) |
| `components/` | O que é geral: `AppNotices` (avisos do PWA) e `ui/` (componentes do shadcn, que o `components.json` espera aqui) |
| `lib/` | Utilitários sem tela: `utils`, `format` (datas), `media` e `google` (script do login) |

| Arquivo | Papel |
|---|---|
| [main.tsx](../web/src/main.tsx) | Monta `<App/>` e `<AppNotices/>`; liga o tema escuro |
| [App.tsx](../web/src/App.tsx) | Escolhe a tela pelo `#` da URL: `inicio` (Landing), `demo` ou `minha` (Workspace) |
| [api/client.ts](../web/src/api/client.ts) | Uma função por rota; `request` trata erros e manda o cabeçalho anti-CSRF |
| [api/types.ts](../web/src/api/types.ts) | Espelho dos contratos do `api/server.py` (`Answer` ↔ `AnswerOut`, `NoteAction` ↔ `ActionOut`...) |
| [pages/Landing.tsx](../web/src/pages/Landing.tsx) | Capa, login com Google e botão da agenda fictícia |
| [pages/Workspace.tsx](../web/src/pages/Workspace.tsx) | O controlador: estado da conversa, da agenda e do formulário, e todos os handlers |
| [features/chat/Chat.tsx](../web/src/features/chat/Chat.tsx) | Lista de mensagens, campo de texto, microfone |
| [features/chat/NoteCard.tsx](../web/src/features/chat/NoteCard.tsx) | Post-it de "anotar": estados `aberto`, `anotado`, `desfeito`, `cancelado`, `pergunta` |
| [features/chat/DetailsPanel.tsx](../web/src/features/chat/DetailsPanel.tsx) | "Como foi respondido": entendimento, busca e verificação |
| [features/chat/recorder.ts](../web/src/features/chat/recorder.ts) | Gravação de áudio (até 60 s) |
| [features/agenda/AgendaCalendar.tsx](../web/src/features/agenda/AgendaCalendar.tsx) | Calendário do mês com post-its |
| [features/agenda/AgendaPanel.tsx](../web/src/features/agenda/AgendaPanel.tsx) | Agenda em lista, dia a dia |
| [features/agenda/EventRow.tsx](../web/src/features/agenda/EventRow.tsx) | Um compromisso; editar e apagar só quando `origem !== 'base'` |
| [features/agenda/EventForm.tsx](../web/src/features/agenda/EventForm.tsx) | Formulário de criar e editar, com a caixa "diga em uma frase" |
| [features/google/GoogleBar.tsx](../web/src/features/google/GoogleBar.tsx) | Conectar o Google, sincronizar, e-mail das 8h |
| [features/startup/WarmupChecklist.tsx](../web/src/features/startup/WarmupChecklist.tsx) | Lista de etapas enquanto o servidor acorda |
| [features/startup/warmup.ts](../web/src/features/startup/warmup.ts) | `useWarmup`: consulta `/api/pronto` até ficar pronto |

Imports entre pastas usam o atalho `@/` (`@/api/client`, `@/features/chat/Chat`); arquivos da mesma pasta usam `./`.

---

## 3. Inicialização

```
uvicorn agenda_ia.api:app                    (Dockerfile:39; local: --port 8000)
└─ app = create_app()                        api/server.py:1078 → 481
   ├─ cria os RateLimiters e lê AuthSettings
   ├─ lifespan: thread "carga-inicial" → Startup.run(load_state)        api/server.py:427
   └─ monta web/dist em "/"                  api/server.py:1074

load_state(progress)                         api/server.py:328
   etapa "modelo":  E5Embedder (um só, compartilhado)
                    GroqModel  (sem GROQ_API_KEY → UnavailableModel: responde só com a busca)
                    GroqTranscriber (sem chave → UnavailableTranscriber: sem microfone)
   etapa "exemplo": sample.build(hoje) → .ics temporário → SqliteEventStore em memória → demo
   etapa "banco":   com DATABASE_URL → PostgresDatabase (Neon); agenda privada = db.for_owner(dono)
                    sem DATABASE_URL → agenda privada em SQLite na memória (some ao reiniciar)
   → AppState(demo, llm_model, new_private, transcriber, google)
```

O servidor responde desde o primeiro segundo: rotas que precisam do estado chamam `startup.get()`, que espera até 50 s, e `/api/pronto` devolve a etapa atual. No navegador, `useWarmup` ([features/startup/warmup.ts](../web/src/features/startup/warmup.ts)) consulta essa rota em loop, e `WarmupChecklist` mostra as etapas.

A janela de datas (`hoje - 60` a `hoje + 120`) é calculada uma vez por instância ([api/server.py:337](../src/agenda_ia/api/server.py#L337)).

---

## 4. Os dois espaços: `demo` e `minha`

Cada rota existe duas vezes, `/api/demo/...` e `/api/minha/...`, e as duas chamam a mesma função interna (`answer_in`, `add_to`, `update_in`...). Muda só o `Workspace` que é passado.

| | demo | minha |
|---|---|---|
| Quem acessa | qualquer visitante | só e-mails em `AGENDA_IA_PERMITIDOS` |
| De onde vem o workspace | `demo_workspace` → `AppState.demo_for` ([api/server.py:307](../src/agenda_ia/api/server.py#L307)) | `private_workspace` → `AppState.private_for` ([api/server.py:301](../src/agenda_ia/api/server.py#L301)) |
| Armazenamento | SQLite em memória; o visitante ganha uma cópia (`store.clone()`) na primeira escrita, que dura 2 h | Postgres, filtrado por `dono = owner_of(user)` |
| Origem dos compromissos | `base` (exemplo, não pode mudar) e `cadastro` | `google`, `cadastro`, `importado` |
| Google | não | escritas vão primeiro ao Google quando a agenda está conectada |

Um `Workspace` ([api/server.py:276](../src/agenda_ia/api/server.py#L276)) guarda um `Assistant`, que guarda o `store`, o `embedder` e o `model`. É por isso que `ws.store` e `ws.assistant.model` aparecem em todas as rotas.

---

## 5. Rotas

`[S]` exige o cabeçalho `X-Requested-With: agenda-ia` (anti-CSRF), `[W]` passa pelo limite de escrita e `[U]` exige login. As rotas de `minha` são todas `[U]`.

| Método e caminho | Função (api/server.py) | O que chama |
|---|---|---|
| GET `/api/pronto` | `ready` 817 | `Startup.wait_change` |
| GET `/api/config`, `/api/me` | `get_config` 826, `get_me` 866 | sessão |
| POST `/api/login` [S] | `login` 871 | `auth.login` → token do Google → lista de permitidos → cookie |
| GET `/api/login/google` e o retorno | `google_start` 832, `google_return` 844 | OAuth por redirecionamento (login ou conexão da agenda) |
| GET `/api/{espaço}/status`, `/agenda` | `status_of` 671, `agenda_of` 678 | `store.count`, `store.events_between` |
| POST `/api/{espaço}/perguntar` [S] | `answer_in` 682 | `Assistant.ask` → `note_from_chat` → `add_to` |
| POST `/api/{espaço}/interpretar` [S] | `interpret_in` 727 | `draft_from_text` (a caixa "diga em uma frase") |
| POST `/api/{espaço}/transcrever` [S] | `transcribe_in` 744 | `GroqTranscriber.transcribe` (sem a trava global) |
| POST `/api/{espaço}/compromissos` [S][W] | `add_to` 769 | `push_new` ou `store.add` |
| PUT `/api/{espaço}/compromissos/{id}` [S][W] | `update_in` 785 | `push_update` ou `store.update`; recusa `base` |
| DELETE `/api/{espaço}/compromissos/{id}` [S][W] | `delete_from` 803 | Google `delete_event` e `store.delete`; recusa `base` |
| POST `/api/minha/importar` [S][W] | `own_import` 978 | `import_events` → `store.add(IMPORTED)` (não vai ao Google) |
| GET/PUT/DELETE `/api/minha/google` | `google_status` 995, `google_settings` 1015, `google_disconnect` 1044 | `GoogleService` |
| GET `/api/minha/google/conectar` | `google_connect` 999 | OAuth com os escopos da agenda e do Gmail |
| POST `/api/minha/google/sincronizar` [S] | `google_sync` 1022 | `run_sync` → `sync` |
| POST `/api/minha/google/resumo` [S] | `google_summary_now` 1033 | `send_summary` |
| POST `/api/tarefas/resumo-diario` | `daily_summary_task` 1055 | `check_scheduler` (token OIDC) → `send_summary` para cada conta |

---

## 6. Fluxos de execução

### 6.1 Uma pergunta pelo chat

```
features/chat/Chat.tsx  submit ─► Workspace.send (pages/Workspace.tsx:230) ─► ask (:260) ─► askQuestion (api/client.ts:71)
  POST /api/minha/perguntar {pergunta, modo}
  own_ask (api/server.py:950) ─► answer_in (:682)
    limiter.allow(ip)                                   20 por 10 min
    with s.lock:
      Assistant.ask (chat/assistant.py:50)
        understand (chat/understanding.py:101) ─────────────► Groq #1 → Query {tipo, de, ate, busca}
            falhou? → Query(tipo="buscar", busca=a mensagem) e o motivo vai em "reserva"
        tipo "buscar"                 → hybrid_search(k=5, uma ocorrência por série)
        "listar"/"contar" com busca   → hybrid_search(k=20, todas as ocorrências), em ordem de data
        "listar"/"contar" sem busca   → store.events_between(limit=40)
        compose (chat/answering.py:64) ─────────────────────► Groq #2 → {resposta, citacoes, encontrou}
            nada recuperado → responde sem chamar o modelo
            citações fora da lista → descartadas; "encontrou" sem citação → "não confirmado"
    ◄─ AnswerOut {resposta, encontrou, fontes, detalhes, acao=None}
  Workspace acrescenta a mensagem ─► Chat mostra o texto e as fontes; DetailsPanel mostra "detalhes"
```

### 6.2 Anotar pelo chat

```
Assistant.ask
  understand → tipo "anotar"            (no modo "anotar" esse passo é pulado)
  tem "?" e nenhum verbo de ordem → vira "buscar"                     chat/assistant.py:68
  draft_from_text (chat/drafting.py:99) ─────────────────► Groq #2 → Suggestion (campos do formulário)
      sem título → NoEventError → responde como pergunta
answer_in ─► note_from_chat (api/server.py:707)
  falta dia ou horário              → acao "completar"  (abre o formulário)
  EventDraft inválido               → acao "completar"
  modo auto + verbo de ordem ("marca", "anota"...) + limite de escrita → add_to → acao "anotado"
  senão                             → acao "confirmar"
Tela: features/chat/Chat.tsx:169 ─► NoteCard
  Anotar    → addDraft → POST /compromissos          → estado "anotado"
  desfazer  → deleteEvent → DELETE /compromissos/id  → estado "desfeito"
  editar    → EventForm                               → POST ou PUT
```

A decisão de salvar direto é feita em código (`explicit_request`, [chat/drafting.py:45](../src/agenda_ia/chat/drafting.py#L45)), nunca pelo modelo.

### 6.3 Criar, editar e apagar pelo formulário

```
EventForm.submit ─► addEvent / updateEvent (api/client.ts:75-76)
  POST/PUT /api/minha/compromissos ─► own_add / own_update ─► add_to (api/server.py:769) / update_in (:785)
    EventDraft já validado pelo FastAPI ─► draft.to_event()
    with s.lock:
      criar, com o Google conectado            ─► push_new (google/sync.py:80)
      editar um evento ligado ao Google        ─► push_update (google/sync.py:91)
            nos dois: Google primeiro, depois o banco, depois o vínculo (google_id e etag)
      senão ─► embedder.passages + store.add / store.update
EventRow "apagar → sim" ─► Workspace.remove (pages/Workspace.tsx:391) ─► DELETE ─► delete_from (api/server.py:803)
```

### 6.4 Mensagem de voz

```
Chat (microfone) ─► useRecorder (features/chat/recorder.ts) ─► Workspace.sendAudio (pages/Workspace.tsx:242)
  ─► transcribeAudio (api/client.ts:106) ─► POST /transcrever ─► transcribe_in (api/server.py:744)
       tipo, tamanho e limite de áudio ─► GroqTranscriber (ai/speech.py:81) ─► Groq Whisper
  ◄─ {texto} ─► ask(texto, "auto") ─► segue como 6.1 ou 6.2
```

O áudio só existe dentro da requisição: vai ao Groq e nunca é gravado.

### 6.5 Sincronizar com o Google Agenda

```
GoogleBar "sincronizar" (ou ao abrir a agenda, se a última foi há mais de 5 min) ─► syncGoogle (api/client.ts:99)
  ─► google_sync (api/server.py:1022) ─► run_sync (:574) ─► sync (google/sync.py:37)
       1. lista os itens do Google na janela e os vínculos locais (google_id → etag)
       2. id novo → novo; etag diferente → alterado; cancelado → ignorado
       3. gera vetores só dos novos e alterados
       4. novos → store.add(GOOGLE) + vínculo; alterados → store.update + novo etag
       5. vínculo local que sumiu do Google → store.delete
```

### 6.6 E-mail das 8h

```
Cloud Scheduler (0 8 * * *, America/Sao_Paulo) ─► POST /api/tarefas/resumo-diario + token OIDC
  daily_summary_task (api/server.py:1055) ─► check_scheduler (:654)
  para cada conta com resumo ligado e que ainda não recebeu hoje:
    send_summary (api/server.py:582) ─► run_sync ─► todays_events ─► daily_email.compose ─► Gmail send_mail
```

### 6.7 Login

```
Popup:      Landing ─► Google Identity Services ─► login(credential) ─► POST /api/login
            ─► auth.login: confere o token, o e-mail verificado e a lista de permitidos ─► cookie "__session"
Redirect:   /api/login/google ─► Google ─► /api/login/google/retorno ─► cookie ─► /#minha
Cada pedido: require_user ─► auth.user_in confere a validade e a lista de novo
```

---

## 7. Busca híbrida

`hybrid_search` ([search/hybrid.py:37](../src/agenda_ia/search/hybrid.py#L37)) roda duas buscas no mesmo período e junta as duas listas:

| | Por significado | Por palavra |
|---|---|---|
| Entrada | `embedder.query(texto)` (prefixo `query:`) | `query_terms(texto)`: sem palavras vazias, cortadas no radical |
| SQLite | `sqlite-vec` (`vec0`), KNN com filtro de data | FTS5 com `bm25`, sem acentos |
| Postgres | `pgvector`, `<=>` (cosseno), índice HNSW | `tsvector 'simple'` com `ts_rank_cd` |
| Em `Hit.found_by` | `"significado"` | `"palavra"` |

A fusão é a Reciprocal Rank Fusion: cada lista vota `1/(60 + posição)`, e quem aparece nas duas sobe. Com `collapse_series=True`, um compromisso recorrente conta uma vez, na próxima ocorrência.

---

## 8. Chamadas ao modelo por pedido

O plano gratuito do Groq tem cerca de 8 mil tokens por minuto, então cada chamada conta. Todas passam por `GroqModel.complete_json` ([ai/llm.py:64](../src/agenda_ia/ai/llm.py#L64)): JSON obrigatório, `temperature=0`, `reasoning_effort="low"` e até 4 novas tentativas do próprio SDK.

| Pedido | Chamadas |
|---|---|
| Pergunta (buscar, listar, contar) | 2: entender e responder (1 se nada foi encontrado) |
| Anotar pelo chat | 2: entender e ler o compromisso (1 no modo "anotar") |
| "Diga em uma frase" no formulário | 1 |
| Áudio | 1 do Whisper + as do texto transcrito |
| Formulário, sincronização, e-mail das 8h | 0 |

O `DetailsPanel` mostra o custo de cada resposta (`detalhes.tokens` e `detalhes.chamadas`), calculado pela diferença do `Usage` antes e depois ([chat/assistant.py:53](../src/agenda_ia/chat/assistant.py#L53)).

---

## 9. Guardas

- **Limites por janela de 10 min** (`RateLimiter`, [api/server.py:453](../src/agenda_ia/api/server.py#L453)): perguntas e frases 20, escritas 30, áudios 15, login 10, sincronizações 10 e resumos 5 por conta.
- **Trava global** `s.lock`: um pedido por vez no modelo e no banco, porque as conexões SQLite são compartilhadas e a cota de tokens é por minuto.
- **Anti-CSRF**: todo POST, PUT e DELETE, menos a tarefa das 8h, exige `X-Requested-With: agenda-ia` (`same_site_only`), que só o próprio `api/client.ts` manda.
- **Agenda original protegida**: `update` e `delete` recusam `origin == "base"`, no SQL e na rota.
- **Isolamento**: toda consulta do Postgres leva `dono = %s`.
- **Tarefa das 8h**: só aceita o token OIDC da conta de serviço do Cloud Scheduler.

---

## 10. Onde mexer para cada tipo de melhoria

| Melhoria | Arquivos, nesta ordem |
|---|---|
| Novo tipo de mensagem no chat (ex.: alterar, apagar) | `chat/understanding.py` (tipo e regra no prompt) → `chat/assistant.py` (ramo em `ask` e campo em `Result`) → `api/server.py` (`answer_in` e um contrato como `ActionOut`) → `web/src/api/types.ts` → `web/src/features/chat/Chat.tsx` e um cartão como o `NoteCard` |
| Mudar como a resposta é escrita | `answering.build_system_prompt` e `tests/test_answering.py` |
| Novo campo no compromisso | `calendar_loader.Event` → esquemas em `storage/store.py` e `storage/postgres.py` (migração no Neon) → `google/events.py` → `editing.EventDraft` → `EventOut`/`DraftOut` → `web/src/api/types.ts` → `web/src/features/agenda/EventForm.tsx` |
| Nova rota | `api/server.py`: uma função `x_in(s, ws, ...)` e as duas rotas finas `demo_x` e `own_x` → `web/src/api/client.ts` → `web/src/api/types.ts` |
| Mudar a busca | `search/hybrid.py` (fusão), `storage/store.py` e `storage/postgres.py` (cada busca), `search/text.py` (termos) |
| Novo limite | um `RateLimiter` em `create_app` |

**Testes.** Ficam em `tests/` e não usam rede. O [conftest.py](../tests/conftest.py) tem os dublês:
- `FakeModel`: respostas prontas, em ordem;
- `FakeEmbedder`: vetor por radicais, sem baixar o modelo;
- `FakeGoogle`;
- `FakeTranscriber`;
- `sample_db`: a agenda de exemplo da semana de 16/11/2026.

Para testar um fluxo novo, enfileire no `FakeModel` o JSON que o Groq devolveria.

**Rodar localmente** (do README):
```powershell
.\.venv\Scripts\python -m pytest
```
```powershell
.\.venv\Scripts\python -m uvicorn agenda_ia.api:app --port 8000
```
```powershell
cd web; npm run dev
```
O Vite abre em `http://localhost:5173` e encaminha `/api` para a porta 8000.

---

## 11. Pontos de atenção encontrados no mapeamento

Nenhum destes é um defeito confirmado em uso. São candidatos a melhoria.

1. **A trava global fica presa durante chamadas ao Google.** `add_to`, `update_in`, `delete_from` e `run_sync` chamam a API do Google dentro de `with s.lock`, então uma sincronização lenta faz todas as perguntas esperarem.
2. **O limite por IP usa o primeiro valor de `X-Forwarded-For`** ([api/server.py:473](../src/agenda_ia/api/server.py#L473)). Atrás do Firebase Hosting e do front-end do Google, esse primeiro valor pode vir do próprio cliente. Vale conferir se dá para burlar o limite trocando o cabeçalho.
3. **Acentos no Postgres.** O `tsvector 'simple'` não remove acentos, e o FTS5 do SQLite remove. Na agenda privada, "ferias" não casa com "Férias" pela palavra, só pelo significado.
4. **Importar .ics não envia nada ao Google.** Os eventos importados ficam só no banco.
5. **A janela de datas é fixa por instância.** Com `min-instances 0` a instância reinicia com frequência, mas uma que fique viva por dias não anda a janela.
6. **Dicionários que só crescem.** `AppState.private` e as chaves dos `RateLimiter` nunca são limpos. Com poucos usuários e uma instância, não pesa.
7. **README desatualizado.** Ele fala em 71 testes; hoje são mais de 200.
