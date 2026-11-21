# Upside LeadHunter

Plataforma interna da Upside (agência de marketing, software e IA) com três funções:

1. **Hunter** — encontra empresas para prospectar (nicho + bairro/cidade) usando o motor do MazyOS Scraper, calcula sinais e uma nota de 0 a 100 e organiza a lista.
2. **Ads** — mostra, **somente leitura**, as campanhas Meta de cada cliente da Upside.
3. **Reports** — gera o relatório mensal de cada cliente em PDF.

Especificação completa: https://claude.ai/artifact/J8ZeT1tiMPbEyX6ukbq5KN

---

## Como trabalhar neste projeto

- Entregue código completo e funcionando. Nada de `TODO`, `pass`, placeholders ou "implemente aqui".
- Não peça confirmação a cada passo. Pergunte só antes de algo irreversível (apagar dados, reescrever migração já aplicada, forçar push) ou quando duas leituras da tarefa levam a resultados bem diferentes.
- Antes de terminar uma tarefa, rode `make check` e só diga que está pronto se passar.
- Ao concluir, resuma em poucas linhas o que mudou e o próximo passo natural. Não repita o código no resumo.
- Código, nomes de variáveis, commits e comentários em **inglês**. Textos de interface (telas, mensagens, PDF) em **português do Brasil**.
- Fuso padrão `America/Sao_Paulo`, moeda `BRL`, formato de data `dd/mm/aaaa` na interface.

---

## Escopo do MVP

Entra:

- Login da equipe (admin / equipe) e cadastro de clientes.
- Hunter: busca automática via scraper, sinais, checagem de site, nota, lista com filtros, status, CSV, "copiar telefones", "virar cliente", limite diário de buscas.
- Ads: vínculo de conta de anúncios ao cliente, sincronização de métricas diárias por campanha, painel por cliente.
- Reports: relatório mensal com snapshot e PDF gerado sob demanda.

**Não implementar agora** (há lugar previsto na arquitetura, mas fica fora do MVP): pausar/editar campanhas, alertas, Lead Ads/webhooks, pipeline kanban, IA na nota ou no relatório, envio automático de relatório, portal do cliente, base de CNPJ, deploy em nuvem.

---

## Stack

| Camada | Escolha |
| --- | --- |
| Linguagem | Python 3.12 |
| Web | FastAPI + Jinja2 + HTMX + Chart.js (sem frontend separado) |
| Banco | PostgreSQL 16, SQLAlchemy 2 (estilo 2.0, tipado), Alembic |
| Config | pydantic-settings lendo `.env` |
| HTTP externo | httpx (async) |
| Checagem de site | httpx + selectolax |
| Jobs | Tabela `job` no Postgres + processo `worker` + APScheduler (só enfileira) |
| PDF | Jinja2 → WeasyPrint; gráficos PNG com matplotlib |
| Segurança | Sessão por cookie assinado (itsdangerous), senha com argon2-cffi, token Meta criptografado com Fernet (`cryptography`) |
| Qualidade | ruff (lint + format), pyright, import-linter, pytest, testcontainers, respx |
| Execução | Docker Compose local: `db`, `web`, `worker`, `scraper` |
| Dependências | `uv` com `pyproject.toml` |

---

## Comandos

```bash
make up          # docker compose up -d --build
make down        # docker compose down
make logs        # docker compose logs -f web worker
make migrate     # alembic upgrade head (dentro do container web)
make revision m="add hunter tables"   # alembic revision --autogenerate
make test        # pytest
make lint        # ruff check + ruff format --check
make typecheck   # pyright
make arch        # lint-imports (import-linter)
make check       # lint + typecheck + arch + test
make seed        # cria usuário admin a partir de ADMIN_EMAIL / ADMIN_PASSWORD
```

URLs locais: app em http://localhost:8000, scraper em http://localhost:8080 (só para debug).

Se a porta 8080 estiver ocupada pelo MazyOS desktop, feche-o ou mude a porta publicada do serviço `scraper` no compose. Dentro da rede Docker o app sempre usa `http://scraper:8080`.

---

## Arquitetura (regras que não se quebram)

Monolito modular. Um código, dois processos (`web` e `worker`), quatro módulos com fronteiras rígidas.

```
app/
├─ main.py            # cria o FastAPI e inclui o router de cada módulo
├─ worker.py          # loop da fila + APScheduler
├─ shared/            # config, db, auth, crypto, events, jobs (fila e runner)
├─ clients/
├─ hunter/
├─ ads/
├─ reports/
└─ templates/         # Jinja2; subpasta por módulo
```

Forma de cada módulo:

```
<modulo>/
├─ __init__.py     # API pública do módulo (ex.: HunterApi). Única coisa importável por outros módulos.
├─ domain.py       # entidades e regras puras. Sem FastAPI, sem SQLAlchemy, sem httpx.
├─ ports.py        # interfaces typing.Protocol para serviços externos
├─ service.py      # casos de uso; recebe repositório e portas por injeção
├─ repository.py   # SQLAlchemy; tabelas com prefixo do módulo
├─ adapters/       # implementações das portas (HTTP para fora)
├─ jobs.py         # handlers da fila, registrados por nome
└─ web.py          # APIRouter + telas
```

Regras:

1. **Um módulo só importa outro via `from app.<modulo> import <Api>`.** Nunca `app.ads.repository`, `app.hunter.domain` etc. O `import-linter` impõe isso (contrato `independence` entre `clients`, `hunter`, `ads`, `reports`; todos podem importar `app.shared`; `app.shared` não importa módulos).
2. **Comunicação entre módulos**: chamada à API pública ou evento em `app.shared.events` (barramento síncrono simples, in-process). Eventos do MVP: `ProspectConverted(prospect_id, client_id)` e `ReportGenerated(report_id)`.
3. **Serviço externo só atrás de `Protocol`** em `ports.py`. O domínio e o service nunca veem JSON da Meta ou do scraper; o adaptador converte para tipos do domínio.
4. **Trabalho demorado vira job.** Rota HTTP nunca chama scraper, Meta ou checagem de sites diretamente; ela enfileira e responde.
5. **Tabelas pertencem a um módulo** (prefixo `hunter_`, `ads_`, `report_`, `client`, e `job`/`app_user` em shared). Outro módulo não faz JOIN com elas: pede os dados pela API do dono.
6. **Configuração só por variável de ambiente.** Nada de URL, token ou limite fixo no código.

---

## Fila de jobs (`app/shared/jobs`)

Tabela `job`: `id`, `type`, `payload jsonb`, `status` (`queued` | `running` | `done` | `failed`), `attempts`, `max_attempts` (padrão 5), `run_after timestamptz`, `last_error`, `created_at`, `updated_at`.

- `enqueue(type: str, payload: dict, run_after: datetime | None = None) -> UUID`.
- Handlers registrados com decorator: `@job_handler("hunter.poll_scrape")`, assinatura `async def handler(payload: dict, ctx: JobContext) -> None`.
- O worker pega um job por vez com `SELECT … WHERE status='queued' AND run_after <= now() ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1`.
- Exceção no handler → `attempts += 1`, `run_after = now() + 2^attempts minutos`, volta para `queued`; ao atingir `max_attempts`, vira `failed` com `last_error`.
- Um handler pode pedir para rodar de novo mais tarde sem contar como falha: `raise RetryLater(seconds=15)` (usado no polling do scraper).
- **Todo handler é idempotente** (upserts, checagem de estado antes de agir).
- APScheduler no worker só **enfileira**: `ads.enqueue_hourly_sync` a cada hora (minuto 5) e `ads.enqueue_resync_7d` às 03:00 (America/Sao_Paulo).
- Painel admin simples em `/admin/jobs`: lista os últimos jobs, filtro por status, botão "reenfileirar" para `failed`.

---

## Hunter

### Integração com o scraper (MazyOS / google-maps-scraper)

O scraper roda como serviço `scraper` (build de `vendor/mazyos-scraper`, git submodule de https://github.com/vagnermazzeo/mazyos-scraper), iniciado com `-web -addr :8080 -data-folder /data`. **Não modifique o código do scraper.** Toda a integração fica no adaptador `app/hunter/adapters/mazyos_scraper.py`, que implementa a porta:

```python
class CompanySearchProvider(Protocol):
    async def start(self, request: SearchRequest) -> str: ...          # devolve external_job_id
    async def status(self, external_job_id: str) -> SearchStatus: ...  # PENDING | RUNNING | DONE | FAILED
    async def results(self, external_job_id: str) -> list[FoundCompany]: ...
```

Contrato HTTP do scraper (base `SCRAPER_URL`):

- `POST /api/v1/jobs` — corpo JSON:
  ```json
  {
    "name": "Dentista · Batel",
    "keywords": ["dentista em Batel, Curitiba"],
    "lang": "pt",
    "depth": 5,
    "email": false,
    "max_time": 1200
  }
  ```
  `max_time` em **segundos**. `depth` e `max_time` não podem ser 0; `lang` tem 2 letras. Resposta `{"id": "<uuid>"}`. Erro de validação → HTTP 422 `{"code": 422, "message": "..."}`.
- `GET /api/v1/jobs/{id}` — devolve o job com chaves em **PascalCase** (struct Go sem tags): `{"ID", "Name", "Date", "Status", "Data"}`. `Status` ∈ `pending`, `working`, `ok`, `failed`.
- `GET /api/v1/jobs/{id}/places` — lista de empresas:
  `title, address, latitude, longitude, link, category, phone, website, review_rating, review_count, emails, borough, city` (`emails` é string; pode ter vários separados por vírgula ou vir vazia).
- `DELETE /api/v1/jobs/{id}` — remove o job no scraper (chamar depois de importar os resultados).

Mapeamento quantidade → parâmetros (o mesmo que a interface da MazyOS usa):

| Quantidade | `depth` | `max_time` (s) |
| --- | --- | --- |
| 20 | 2 | 600 |
| 60 | 5 | 1200 |
| 120 | 10 | 1800 |
| 200+ | 18 | 3600 |

Com "buscar e-mails" ligado, dobre o `max_time`. Consultas: uma por nicho, no formato `"{nicho} em {bairro}, {cidade}"` (sem bairro: `"{nicho} em {cidade}"`).

### Fluxo

1. `POST /hunter/searches` valida o formulário, checa o limite diário (`HUNTER_DAILY_LIMIT`, padrão 20, contando buscas criadas hoje no fuso de São Paulo), cria `hunter_search` com status `queued` e enfileira `hunter.start_scrape`.
2. `hunter.start_scrape`: chama `provider.start`, grava `scraper_job_id`, status `running`, enfileira `hunter.poll_scrape`. Só uma busca `running` por vez; se já houver outra, `RetryLater(30)`.
3. `hunter.poll_scrape`: `pending`/`working` → `RetryLater(15)`. `failed` → busca `failed` com mensagem amigável ("O Google não devolveu resultados ou bloqueou temporariamente. Tente mais tarde."). `ok` → `results`, upsert dos prospects, `DELETE` no scraper, status `done`, `total_found`, enfileira `hunter.check_sites` com os IDs que têm site próprio.
4. Se a busca passar de `max_time + 5 min` sem terminar, marque `failed` por timeout.
5. A tela da busca faz polling por HTMX (`hx-trigger="every 5s"`) enquanto o status for `queued`/`running`.

### Sinais (calculados na gravação; replicam as regras da MazyOS)

- `whatsapp`: só dígitos do telefone; se começar com `55` e tiver 13 dígitos, remova o `55`; se sobrar 11 dígitos e o terceiro for `9`, é celular → guarde `55` + dígitos. Senão `null`.
- Tipo de site: vazio → `none`; URL contendo `instagram.com`, `facebook.com`, `linktr.ee`, `wa.me`, `whatsapp.com` ou `tiktok.com` → `social`; senão `site`. Daí saem `no_website` e `social_only`.
- `emails`: lista normalizada (minúsculas, sem duplicados).
- `hunter.check_sites` (para tipo `site`): GET da página inicial (timeout 10 s, até 20 em paralelo, user-agent comum), procura `connect.facebook.net/` + `fbevents.js` → `has_meta_pixel`; links de `instagram.com` e `wa.me`/`api.whatsapp.com`. Falha de rede não é erro do job: marca `site_unreachable`.

### Nota (`ScoringRules` em `hunter/domain.py`, função pura)

| Sinal | Pontos |
| --- | --- |
| 30+ avaliações | +20 |
| Nota média ≥ 4,3 | +10 |
| Sem site | +25 |
| Só rede social (no lugar de "sem site") | +20 |
| Site próprio sem pixel Meta (após checagem) | +20 |
| WhatsApp | +15 |
| E-mail encontrado | +10 |

Teto 100. Guarde `score_details` (jsonb) com os pontos de cada regra, para mostrar o "porquê" na tela. Recalcule a nota quando `check_sites` terminar.

### Deduplicação e status

- Chave única: `maps_link`. Encontrou de novo → atualiza dados e `search_id`, **preserva** `status`, `do_not_contact` e `client_id`.
- Status: `new`, `contacted`, `meeting`, `won`, `discarded` (rótulos em português na tela).
- `do_not_contact = true` tira o prospect de listas, CSV e "copiar telefones" para sempre.
- "Virar cliente" (`won`): cria o cliente via `ClientsApi`, grava `client_id` e publica `ProspectConverted`.

### Tela

Tabela com nome, categoria, nota (com tooltip do porquê), avaliações, telefone/WhatsApp (link `https://wa.me/<numero>`), site, e-mail, bairro, status (select HTMX) e link "Abrir no Maps". Filtros: Sem site, Só rede social, WhatsApp, Com e-mail, nota mínima, status. Ações: exportar CSV (UTF-8 com BOM, separador `;`, para abrir certo no Excel em português) e copiar telefones. Ordenação padrão: nota decrescente.

---

## Ads (somente leitura)

- Porta `AdsPlatformGateway` com `list_campaigns(account_id)` e `daily_insights(account_id, since, until)`. Adaptador `adapters/meta_graph.py` usando httpx direto na Graph API, versão em `META_API_VERSION` (padrão `v27.0`), token do usuário do sistema em `META_SYSTEM_TOKEN`.
- Insights: `GET /{version}/act_{id}/insights` com `level=campaign`, `time_increment=1`, `fields=campaign_id,campaign_name,spend,impressions,reach,inline_link_clicks,actions,action_values`, `time_range={"since","until"}`. Siga a paginação (`paging.next`).
- Gravação por upsert em `ads_campaign_daily` com chave `(date, campaign_id)`. `results` = soma das `actions` cujo `action_type` é o `result_action_type` do cliente; `actions` guarda o bruto.
- Jobs: `ads.sync_account` (hoje), `ads.resync_7d` (últimos 7 dias), `ads.sync_structure` (nomes e status, 1x/dia). Um job por conta.
- Rate limit: leia o cabeçalho `x-business-use-case-usage`; acima de 75% de uso, `RetryLater` com o tempo sugerido. Erros 17/80004 → `RetryLater(300)`.
- Painel: lista de clientes (investimento do mês, resultados, custo por resultado) e tela do cliente (filtro de período, cards, gráfico diário investimento × resultados em Chart.js, tabela por campanha).
- Nunca chamar endpoints de escrita da Meta no MVP.

---

## Reports

- `GenerateMonthlyReport(client_id, year, month, comment)` monta um **snapshot** (jsonb) com totais, variação sobre o mês anterior, série diária e tabela por campanha, lidos via `AdsApi` (nunca da tabela de `ads` diretamente).
- O PDF é gerado sob demanda a partir do snapshot: template `templates/reports/monthly.html` → WeasyPrint; gráfico diário em PNG com matplotlib (backend `Agg`), embutido como data URI.
- Seções: capa (logos do cliente e da Upside, período), resumo com variação, gráfico diário, tabela por campanha, comentário do gestor.
- Relatório gerado nunca muda: reabrir o PDF usa o mesmo snapshot.

---

## Modelo de dados (resumo)

`app_user`, `job`, `client`, `hunter_search`, `hunter_prospect`, `ads_account`, `ads_campaign`, `ads_campaign_daily`, `report`, `sync_log`. Campos detalhados na seção "Modelo de dados" da especificação. Convenções:

- PK `uuid` (gerado no app), exceto IDs da Meta (`act_…`, IDs de campanha) que usam o próprio ID como PK `text`.
- `created_at` / `updated_at` `timestamptz` em todas as tabelas.
- Dinheiro em `numeric(14,2)`; contagens em `bigint`.
- Uma migração Alembic por mudança; nunca editar migração já aplicada.

---

## Configuração (`.env.example`)

```dotenv
APP_ENV=dev
SESSION_SECRET=troque-por-um-valor-aleatorio
FERNET_KEY=gere-com-python-c-from-cryptography.fernet-import-Fernet-print-Fernet.generate_key
DATABASE_URL=postgresql+psycopg://upside:upside@db:5432/leadhunter
SCRAPER_URL=http://scraper:8080
HUNTER_DAILY_LIMIT=20
META_API_VERSION=v27.0
META_SYSTEM_TOKEN=
ADMIN_EMAIL=admin@upsidemkt.com.br
ADMIN_PASSWORD=troque-esta-senha
TZ=America/Sao_Paulo
```

`.env` nunca vai para o Git. O token da Meta, se gravado no banco (por conta), é sempre cifrado com `FERNET_KEY`.

---

## Testes e "pronto"

- `domain`: testes unitários puros (nota, sinais, WhatsApp, tipo de site, mapeamento de quantidade).
- Adaptadores: respx simulando as respostas reais do scraper (inclusive chaves PascalCase do job e `emails` vazio) e da Graph API (paginação, rate limit).
- Repositórios e jobs: Postgres real via testcontainers.
- Uma tarefa só está pronta quando: `make check` passa, a migração sobe e desce (`alembic upgrade head && alembic downgrade -1 && alembic upgrade head`), e o fluxo funciona no `docker compose up`.

---

## Ordem de implementação

Siga em ordem; cada etapa termina rodando no compose.

1. **Base**: `pyproject.toml` (uv), Dockerfile (Python 3.12 slim + libs do WeasyPrint: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b`), `docker-compose.yml` (db, scraper, web, worker), Makefile, `.env.example`, `.importlinter`, configs de ruff e pyright, `app/shared` (config, db, auth, events), Alembic, login, `make seed`, cadastro de clientes, layout base com HTMX.
2. **Fila de jobs**: tabela `job`, `enqueue`, decorator de handler, `worker.py` com loop e APScheduler, `RetryLater`, `/admin/jobs`, testes.
3. **Hunter — busca**: submodule `vendor/mazyos-scraper`, `CompanySearchProvider` + `MazyosScraperProvider`, jobs `start_scrape`/`poll_scrape`, limite diário, tela de nova busca com progresso.
4. **Hunter — qualificação**: sinais, `check_sites`, `ScoringRules`, lista com filtros, status, CSV, copiar telefones, virar cliente, `do_not_contact`.
5. **Ads — sincronização**: `AdsPlatformGateway` + `MetaGraphGateway`, vínculo conta ↔ cliente, jobs de sync, `sync_log`.
6. **Ads — painel**.
7. **Reports**: snapshot, template, PDF, histórico por cliente.

Comece pela etapa 1 se o repositório estiver vazio.

---

## Não fazer

- Não chamar o Google Maps de outra forma além do serviço `scraper`, e não remover o limite diário.
- Não adicionar Redis, Celery, Kafka ou outro serviço de fila: a tabela `job` resolve o MVP.
- Não criar frontend separado (React/Next) no MVP.
- Não usar endpoints de escrita da Meta.
- Não logar tokens, senhas, cookies ou o conteúdo de `.env`.
- Não importar o interno de outro módulo para "ganhar tempo": exponha o que precisa na API pública do dono.
