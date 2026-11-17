# Infraestrutura do agenda-ia

Tudo o que roda fora do seu computador, para que serve cada peça, como elas se ligam e como montar tudo de novo do zero em outro projeto do Google Cloud.

Nos comandos, `$P` é o ID do projeto e `$REGIAO` é `us-east1`. Defina os dois no Cloud Shell antes de começar:

```bash
P=$(gcloud config get-value project)
REGIAO=us-east1
```

---

## 1. Visão geral

```
                         ┌──────────────────────── Google Cloud (um projeto) ────────────────────────┐
                         │                                                                            │
 Você ── Cloud Shell ────┼──► Cloud Storage ──► Cloud Build ──► Artifact Registry                     │
   (deploy/cloud-run.sh) │    (código enviado)  (roda o         (guarda a imagem)                     │
                         │                       Dockerfile)          │                               │
                         │                                            ▼                               │
 Navegador ──HTTPS───────┼──────────────────────────────────► Cloud Run: serviço agenda-ia           │
                         │                                     └─ revisão ─► instância (container)   │
                         │                                          │   ▲                             │
                         │                         Secret Manager ──┘   │  Cloud Logging (logs)       │
                         │                         (3 segredos)         │                             │
                         │  IAM: contas de serviço agenda-ia-run e agenda-ia-build                    │
                         │  Google Auth Platform: tela de consentimento + ID do cliente OAuth         │
                         └────────────────────────────────────────────────────────────────────────────┘
        fora do Google:  GitHub (código) · Groq (LLM) · Hugging Face Hub (modelo, só no build)
```

São dois caminhos que nunca se misturam:

- **Publicar** (raro, alguns minutos): o código sai do Cloud Shell, vira uma imagem no Cloud Build e uma revisão nova no Cloud Run.
- **Usar** (cada visita, 1 a 2 segundos): o navegador chama o Cloud Run, que liga um container se precisar, e o container chama o Groq.

---

## 2. As peças, uma a uma

### Projeto e faturamento

- **O que é:** o projeto é a "pasta" que agrupa todos os recursos, permissões e cobranças. Ele fica ligado a uma **conta de faturamento**, que pode ter créditos de boas-vindas.
- **Por que importa:** mesmo usando só a cota gratuita, o Cloud Run, o Cloud Build e o Secret Manager exigem faturamento ativo.
- **Proteção:** um **orçamento** (Faturamento → Orçamentos e alertas) avisa por e-mail quando o gasto passa de um valor. O orçamento **avisa, não bloqueia**: ele não desliga nada sozinho. O que limita o gasto deste serviço é a configuração `max-instances 1` (seção Cloud Run).

### APIs ativadas

Um projeto novo começa com quase tudo desligado. O script ativa quatro APIs:

| API | Para quê |
|---|---|
| `run.googleapis.com` | Cloud Run |
| `cloudbuild.googleapis.com` | compilar a imagem |
| `artifactregistry.googleapis.com` | guardar a imagem |
| `secretmanager.googleapis.com` | guardar chaves e a lista de e-mails |

### Cloud Run: serviço, revisão, instância

Executa containers sem você administrar servidor. Tem três níveis:

- **Serviço** (`agenda-ia`): nome, endereço fixo (`https://agenda-ia-<número-do-projeto>.us-east1.run.app`), configuração e regra de tráfego.
- **Revisão** (`agenda-ia-00001`, `00002`…): foto **imutável** de imagem + configuração, criada a cada deploy. O tráfego vai para a mais nova; as antigas ficam para voltar atrás.
- **Instância**: o container rodando. Liga no primeiro pedido, atende vários, desliga depois de ~15 minutos parado. A memória dela é apagada quando desliga.

A configuração, e o porquê de cada valor (está em [`deploy/cloud-run.sh`](../deploy/cloud-run.sh)):

| Opção | Valor | Por quê |
|---|---|---|
| `--memory` / `--cpu` | 2 GiB / 1 | PyTorch + modelo de embeddings ocupam ~1 GB |
| `--cpu-boost` | ligado | mais CPU durante a partida, para o modelo carregar mais rápido |
| `--min-instances` | 0 | desliga quando ninguém usa: custo zero parado (o preço é a partida a frio de 30 a 60 s) |
| `--max-instances` | 1 | teto de gasto: mesmo sob abuso, nunca roda mais de um container |
| `--concurrency` | 10 | pedidos simultâneos por container (a API ainda serializa as perguntas) |
| `--timeout` | 120 s | uma pergunta leva 1 a 15 s; folga para o Groq quando a cota aperta |
| `--allow-unauthenticated` | sim | o site é público; quem controla a área privada é a aplicação (login) |
| `--service-account` | `agenda-ia-run` | identidade do container (seção IAM) |
| `--set-secrets` | 3 segredos | viram variáveis de ambiente quando a instância liga |
| `--update-env-vars` | `GOOGLE_CLIENT_ID` | valor público do login com Google |

O container escuta na porta que o Cloud Run informa na variável `$PORT` (8080). O [`Dockerfile`](../Dockerfile) já faz isso.

### Cloud Build e Cloud Storage

- O `gcloud run deploy --source .` **compacta a pasta** (menos o que está no [`.gcloudignore`](../.gcloudignore)) e envia para um bucket criado sozinho, `run-sources-<projeto>-us-east1`.
- O **Cloud Build** pega esse pacote e executa o Dockerfile:
  1. etapa Node: `npm ci` e `npm run build` (checa os tipos e gera o React compilado);
  2. etapa Python: PyTorch só de CPU, o pacote `agenda_ia`, o download do modelo de embeddings para dentro da imagem e a cópia do React compilado.
- Roda com a conta **`agenda-ia-build`**. Sem ela, usaria a conta padrão do Compute Engine, que pode não ter permissão e é também a identidade das VMs do projeto.
- Leva de 5 a 10 minutos porque reinstala tudo a cada vez (não há cache entre builds).

### Artifact Registry

- Guarda as imagens prontas no repositório `cloud-run-source-deploy` (criado no primeiro deploy). Cada imagem tem ~1,5 GB.
- É o único item que pode custar algo: a cota gratuita é de 0,5 GB, e cada deploy adiciona uma imagem. Ver "Limpeza das imagens antigas" na seção 5.

### Secret Manager

Três segredos criados pelo script e um opcional:

| Segredo | Variável no container | Conteúdo | Quem cria |
|---|---|---|---|
| `groq-api-key` | `GROQ_API_KEY` | chave da API do Groq | você digita (não aparece na tela) |
| `agenda-ia-sessao` | `AGENDA_IA_SESSAO_SEGREDO` | chave que assina os cookies de login | o script gera aleatoriamente |
| `agenda-ia-permitidos` | `AGENDA_IA_PERMITIDOS` | e-mails com acesso à área privada, separados por vírgula | você digita |
| `agenda-ia-banco` (opcional) | `DATABASE_URL` | endereço de conexão do Postgres (Neon) | você cria (seção Neon); o script só o liga se existir |

- Cada segredo tem **versões**. O Cloud Run usa a `latest`, lida **quando a instância liga**.
- Para trocar um valor: grave uma versão nova (o segredo guarda o valor inteiro, então a lista de e-mails vai completa):

  ```bash
  printf '%s' "a@gmail.com,b@gmail.com" | gcloud secrets versions add agenda-ia-permitidos --data-file=-
  ```

- Para valer na hora, crie uma revisão nova sem refazer o build:

  ```bash
  gcloud run services update agenda-ia --region $REGIAO --update-labels atualizado=$(date +%s)
  ```

- Trocar `agenda-ia-sessao` desconecta todo mundo (os cookies antigos deixam de ser válidos).

### IAM: quem pode o quê

| Identidade | Papel | Pode | Não pode |
|---|---|---|---|
| você (dono) | Proprietário | tudo; dispara o deploy pelo Cloud Shell | |
| `agenda-ia-build` | Cloud Run Builder (no projeto) | ler o código enviado, gravar a imagem, escrever o log do build | mexer em serviços, segredos, VMs |
| `agenda-ia-run` | Secret Manager Secret Accessor (só nos segredos do app) | ler esses segredos | qualquer outra coisa no projeto |
| `allUsers` | Cloud Run Invoker (no serviço) | chamar o site | |

É o princípio do **menor privilégio**: se alguém invadisse o container, só conseguiria ler as variáveis que o container já tem.

### Neon (Postgres com pgvector, para a agenda privada)

O container do Cloud Run perde a memória quando desliga, então a agenda privada precisa de um banco fora dele. O Neon é um Postgres "serverless" com plano gratuito (1 GB por projeto, desliga sozinho depois de 5 minutos parado e liga na próxima consulta) e com a extensão **pgvector**.

- **Tabela:** uma só, `eventos`, criada pela própria API na primeira conexão ([`pgstore.py`](../src/agenda_ia/pgstore.py)). Cada linha tem o dono (ID da conta Google), os campos do compromisso, o vetor de 384 números (`vector(384)`) e uma coluna `tsvector` gerada para a busca por palavra.
- **Índices:** `(dono, inicio)` para listar por data, GIN no `tsvector` e HNSW no vetor (distância de cosseno).
- **Isolamento:** toda consulta tem `where dono = ...`, inclusive a exclusão.
- **Sem o banco:** se o segredo `agenda-ia-banco` não existir, a agenda privada fica em memória e a tela avisa que é temporária.

**Como ligar:**

1. Crie a conta em neon.tech e um projeto. Escolha a região **AWS us-east-1** (a mais perto do Cloud Run em us-east1).
2. Em **Connect**, copie o endereço de conexão (`postgresql://...neon.tech/neondb?sslmode=require`). Prefira o endereço com **pooling** (`-pooler` no nome do host).
3. No Cloud Shell, guarde-o como segredo, sem que ele apareça na tela nem no histórico:

   ```bash
   read -rs URL && printf '%s' "$URL" | gcloud secrets create agenda-ia-banco --data-file=- && unset URL
   ```

4. Rode o deploy de novo (`bash deploy/cloud-run.sh`). O script detecta o segredo, dá à conta `agenda-ia-run` permissão de leitura e entrega a variável `DATABASE_URL` ao container.

Para testar o SQL sem o Neon, [`scripts/testar-postgres.sh`](../scripts/testar-postgres.sh) sobe um Postgres com pgvector descartável no Docker e roda os testes de armazenamento.

### Google Auth Platform (login com Google)

Duas peças, em APIs e serviços / Google Auth Platform:

- **Tela de consentimento** (Branding / Público-alvo / Acesso a dados): o nome do app que aparece na janela de login, e o público "Externo". O login só pede `openid`, `email` e `profile`, escopos que não exigem verificação do Google.
  - Se o app estiver em modo **Teste**, só os "usuários de teste" listados ali conseguem passar pela janela do Google.
  - Em **Produção**, qualquer conta Google passa pela janela, e quem decide o acesso é a lista do segredo `agenda-ia-permitidos`.
- **ID do cliente OAuth** do tipo *Aplicativo da Web*:
  - **Origens JavaScript autorizadas:** os endereços de onde o botão pode ser usado: os dois endereços do serviço no Cloud Run, `http://localhost:8000` e `http://localhost`.
  - **URIs de redirecionamento:** vazio (o login é por janela, não por redirecionamento).
  - O **ID** é público (vai para o navegador). A **chave secreta do cliente** não é usada por este app.

A verificação do login acontece no servidor ([`auth.py`](../src/agenda_ia/auth.py)): assinatura do token com as chaves públicas do Google, público igual ao ID do cliente, emissor, validade, e-mail verificado e presença na lista.

### Cloud Logging

Tudo o que o container escreve (e os logs do build) vai para o Cloud Logging. Para ler os últimos registros do serviço:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="agenda-ia"' --limit 30 --format 'value(timestamp, textPayload)'
```

Ou pelo console: Cloud Run → agenda-ia → Registros.

### Fora do Google

| Serviço | Para quê | Quando |
|---|---|---|
| GitHub | código-fonte; o Cloud Shell faz `git pull` antes de cada deploy | deploy |
| Hugging Face Hub | download do modelo `intfloat/multilingual-e5-small` | só no build (em produção o container não acessa) |
| Groq | o LLM `openai/gpt-oss-120b`, plano gratuito (8.000 tokens/minuto) | cada pergunta |
| Neon | Postgres com pgvector para guardar a agenda privada (opcional) | cada operação na área privada |

---

## 3. Os dois fluxos

**Publicar** (`bash deploy/cloud-run.sh` no Cloud Shell):

1. ativa as APIs;
2. cria os segredos que faltam;
3. cria as contas de serviço e dá as permissões;
4. `gcloud run deploy --source .`: envia o código ao Cloud Storage → Cloud Build gera a imagem → Artifact Registry guarda → Cloud Run cria a revisão, liga uma instância de teste, e só então manda 100% do tráfego para ela.

**Usar** (uma pergunta no site):

1. o navegador abre o endereço do serviço; o Cloud Run termina o HTTPS e, se não houver instância ligada, liga uma (partida a frio);
2. a instância lê os segredos, carrega o modelo de embeddings (já dentro da imagem) e monta a agenda fictícia na memória;
3. o FastAPI entrega o React (`/`) e responde à API (`/api/...`) na mesma origem;
4. na pergunta, a API chama o Groq duas vezes (entender a pergunta e responder) e confere as citações;
5. sem visitas por ~15 minutos, a instância desliga.

---

## 4. Como replicar do zero

**Pré-requisitos:** uma conta Google Cloud com faturamento ativo (os créditos de boas-vindas servem), uma chave gratuita do Groq (console.groq.com/keys) e uma cópia do repositório no seu GitHub.

1. **Projeto.** Crie um projeto no console, ligue-o à conta de faturamento e crie um orçamento com alerta por e-mail (Faturamento → Orçamentos e alertas).
2. **Cloud Shell.** Abra o Cloud Shell (ícone `>_` no topo do console) e prepare:

   ```bash
   gcloud config set project ID_DO_SEU_PROJETO
   git clone https://github.com/SEU_USUARIO/agenda-ai.git && cd agenda-ai
   ```

3. **Descubra o endereço que o serviço vai ter**, para cadastrar no login antes do primeiro deploy:

   ```bash
   echo "https://agenda-ia-$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)').us-east1.run.app"
   ```

4. **Login com Google** (opcional; sem ele, o site funciona só no modo teste):
   - Google Auth Platform → Branding: nome do app e e-mail de suporte. Público-alvo: Externo.
   - Credenciais → Criar credenciais → ID do cliente OAuth → Aplicativo da Web. Em origens autorizadas, o endereço do passo 3, `http://localhost:8000` e `http://localhost`. Copie o ID do cliente.
5. **Publique:**

   ```bash
   GOOGLE_CLIENT_ID=SEU_ID.apps.googleusercontent.com bash deploy/cloud-run.sh
   ```

   O script pede a chave do Groq e os e-mails com acesso, e termina mostrando o endereço. Sem login, rode só `bash deploy/cloud-run.sh` (ele ainda pede os e-mails; pode deixar vazio).
6. **Confira:**

   ```bash
   curl -s https://ENDERECO/api/config     # {"login":true,...} se o login estiver ligado
   curl -s https://ENDERECO/api/demo/status
   ```

7. **Depois do deploy, o Cloud Run mostra também um segundo endereço** (formato `https://agenda-ia-xxxxxxxx-ue.a.run.app`). Se for usá-lo, adicione-o também às origens autorizadas do ID do cliente.

---

## 5. Operação do dia a dia

**Atualizar o site** depois de um push no GitHub:

```bash
cd ~/agenda-ai && git pull && bash deploy/cloud-run.sh
```

**Voltar para uma revisão anterior** (sem build):

```bash
gcloud run revisions list --service agenda-ia --region $REGIAO
gcloud run services update-traffic agenda-ia --region $REGIAO --to-revisions agenda-ia-00002-xxx=100
```

**Limpeza das imagens antigas** (para o Artifact Registry não passar da cota gratuita). Salve como `limpeza.json`:

```json
[
  { "name": "manter-as-2-mais-novas", "action": { "type": "Keep" }, "mostRecentVersions": { "keepCount": 2 } },
  { "name": "apagar-as-antigas", "action": { "type": "Delete" }, "condition": { "olderThan": "7d" } }
]
```

```bash
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --location $REGIAO --policy limpeza.json            # simula
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --location $REGIAO --policy limpeza.json --no-dry-run
```

**Desmontar tudo** (para parar qualquer cobrança):

```bash
gcloud run services delete agenda-ia --region $REGIAO
gcloud artifacts repositories delete cloud-run-source-deploy --location $REGIAO
gcloud secrets delete groq-api-key; gcloud secrets delete agenda-ia-sessao; gcloud secrets delete agenda-ia-permitidos
gcloud iam service-accounts delete agenda-ia-run@$P.iam.gserviceaccount.com
gcloud iam service-accounts delete agenda-ia-build@$P.iam.gserviceaccount.com
```

E apague o ID do cliente OAuth em Credenciais, o segredo `agenda-ia-banco` (se existir) e o projeto no Neon.

---

## 6. Custos

| Peça | Cota gratuita (mensal) | Uso típico desta demonstração |
|---|---|---|
| Cloud Run | 2 milhões de requisições, 180 mil vCPU-segundos, 360 mil GiB-segundos | muito abaixo |
| Cloud Build | cota gratuita de minutos de build | poucos deploys por mês |
| Artifact Registry | 0,5 GB de armazenamento | ~1,5 GB por imagem: centavos por mês sem a limpeza |
| Secret Manager | 6 versões ativas e 10 mil acessos | 3 segredos |
| Cloud Logging | 50 GiB de logs | muito abaixo |
| Groq | plano gratuito (8.000 tokens/minuto) | ~2 mil tokens por pergunta |
| Neon | 1 GB e 100 horas de processamento por projeto; passando do limite, bloqueia gravações em vez de cobrar | alguns MB por agenda |

As cotas mudam com o tempo; confira a página de preços de cada serviço antes de replicar.

---

## 7. Segurança, em resumo

- Nenhuma chave no código nem no Git: tudo no Secret Manager (produção) ou no `.env` ignorado pelo Git (local).
- Contas de serviço próprias, cada uma só com o que precisa.
- Teto de 1 instância e limite de perguntas por visitante contra abuso da cota gratuita do Groq.
- Login conferido no servidor, cookie `HttpOnly` + `Secure` + `SameSite=Lax`, cabeçalho próprio contra CSRF, lista de permitidos fora do repositório.
- O modelo de embeddings vai dentro da imagem: em produção, o container não depende de baixar nada.
