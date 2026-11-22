# Upside LeadHunter

Plataforma interna da Upside, com três áreas:

- **Hunter** — encontra empresas para prospectar (nicho + bairro/cidade), calcula sinais e uma nota de 0 a 100.
- **Ads** — mostra, somente leitura, as campanhas Meta de cada cliente.
- **Reports** — gera o relatório mensal do cliente em PDF.

Tudo roda em Docker: você **não** precisa instalar Python, Postgres nem nada do projeto na máquina.

---

## O que instalar antes

| | |
| --- | --- |
| **Git** | https://git-scm.com/downloads |
| **Docker Desktop** | https://www.docker.com/products/docker-desktop — no Windows, aceite a instalação do WSL 2 quando ele pedir |

Abra o Docker Desktop e espere o ícone da baleia ficar estável **antes** de rodar qualquer comando abaixo. Deixe o projeto numa pasta do disco `C:` (o Docker monta a pasta do projeto dentro do container).

---

## Passo a passo (primeira vez)

Todos os comandos rodam no **PowerShell**, dentro da pasta do projeto.

### 1. Clonar o repositório

O scraper do Google Maps é um submodule, então clone com `--recurse-submodules`:

```powershell
git clone --recurse-submodules https://github.com/Thafonso/upside-software.git
```

```powershell
cd upside-software
```

Se você já tinha clonado sem a flag, a pasta `vendor/mazyos-scraper` estará vazia. Conserte com:

```powershell
git submodule update --init --recursive
```

### 2. Criar o arquivo `.env`

```powershell
Copy-Item .env.example .env
```

Agora gere os dois segredos da aplicação:

```powershell
docker run --rm python:3.12-slim python -c "import base64, os, secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(32)); print('FERNET_KEY=' + base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Abra o `.env` e troque as linhas `SESSION_SECRET=` e `FERNET_KEY=` pelas duas linhas que o comando imprimiu. No mesmo arquivo, defina o seu login:

```dotenv
ADMIN_EMAIL=seu.email@upsidemkt.com.br
ADMIN_PASSWORD=uma-senha-sua
```

O `.env` nunca vai para o Git — ele é o único lugar com segredos e cada máquina tem o seu.

### 3. Subir os containers

```powershell
.\make.ps1 up
```

Na primeira vez isso compila o scraper (Go) e a imagem Python: pode levar de 5 a 15 minutos. Nas próximas vezes sobe em segundos.

### 4. Criar as tabelas

```powershell
.\make.ps1 migrate
```

### 5. Criar o seu usuário

```powershell
.\make.ps1 seed
```

Isso usa o `ADMIN_EMAIL` / `ADMIN_PASSWORD` do `.env` e cria (ou atualiza) uma conta de administrador.

### 6. Abrir o app

http://localhost:8000 — faça login com o e-mail e a senha do passo 2.

As telas são:

| Tela | Endereço |
| --- | --- |
| Clientes | `/clients` |
| Hunter | `/hunter` |
| Ads | `/ads` |
| Relatórios | `/reports` |
| Fila de jobs (admin) | `/admin/jobs` |

O scraper fica em http://localhost:8080, mas é só para debug — o app fala com ele por dentro da rede do Docker.

---

## Comandos do dia a dia

No Windows use `.\make.ps1 <alvo>`; no macOS/Linux, `make <alvo>`.

| Comando | O que faz |
| --- | --- |
| `.\make.ps1 up` | sobe (ou reconstrói) tudo em segundo plano |
| `.\make.ps1 down` | derruba os containers (o banco continua salvo) |
| `.\make.ps1 logs` | acompanha o log do `web` e do `worker` (Ctrl+C para sair) |
| `.\make.ps1 migrate` | aplica as migrações pendentes do banco |
| `.\make.ps1 seed` | cria/atualiza o usuário do `.env` |
| `.\make.ps1 test` | roda os testes |
| `.\make.ps1 check` | lint + tipos + arquitetura + testes (é o que diz se está tudo ok) |
| `.\make.ps1 shell` | abre um bash dentro do container `web` |

Depois de dar `git pull`, rode `.\make.ps1 up` e `.\make.ps1 migrate` — pode ter vindo dependência nova ou migração nova.

---

## Criar usuário para outra pessoa da equipe

Ainda não existe tela de cadastro; o comando de seed resolve. Ele pergunta a senha num prompt, para a senha não ficar no histórico do PowerShell:

```powershell
$c = Get-Credential -UserName "pessoa@upsidemkt.com.br" -Message "Senha do novo usuario"; docker compose run --rm -e "ADMIN_EMAIL=$($c.UserName)" -e "ADMIN_PASSWORD=$($c.GetNetworkCredential().Password)" web python -m app.shared.seed
```

Três coisas para saber:

- a conta criada é sempre **admin**, com o nome "Administrador";
- não há tela de trocar senha — para trocar, rode o comando de novo com o mesmo e-mail;
- rodar com um e-mail que já existe **redefine a senha** daquela conta.

---

## Ads e Reports precisam do token da Meta

Sem `META_SYSTEM_TOKEN` no `.env`, a sincronização de campanhas falha e o painel de Ads fica vazio (e o relatório mensal sai sem números). O Hunter e o resto do app funcionam normalmente sem o token. Peça o token do usuário do sistema ao Thiago e coloque em:

```dotenv
META_SYSTEM_TOKEN=
```

---

## Se algo der errado

**`cannot connect to the Docker daemon`** — o Docker Desktop está fechado. Abra e espere ele terminar de iniciar.

**`port is already allocated` na 8080** — o MazyOS desktop está aberto usando essa porta. Feche o MazyOS, ou troque só a porta publicada no `docker-compose.yml` (de `"8080:8080"` para `"8081:8080"`, por exemplo). Por dentro da rede do Docker o app sempre usa `http://scraper:8080`, então trocar a porta publicada não quebra nada.

**`port is already allocated` na 5432** — você tem um Postgres instalado na máquina. Mesma solução: troque a porta publicada do serviço `db` (`"5433:5432"`).

**O container `web` sobe e morre** — quase sempre é `.env` faltando ou com `SESSION_SECRET`/`FERNET_KEY` curtos; a aplicação valida a configuração na partida e para de propósito. Veja o motivo com `.\make.ps1 logs`.

**A build do serviço `scraper` falha** (erro de contexto ou de Dockerfile não encontrado) — o submodule não foi baixado. Confira se `vendor/mazyos-scraper` tem arquivos; se estiver vazia, rode `git submodule update --init --recursive` e suba de novo.

**Começar do zero** — isto **apaga o banco local** (clientes, prospects, relatórios):

```powershell
docker compose down -v
```

Depois: `.\make.ps1 up`, `.\make.ps1 migrate`, `.\make.ps1 seed`.

---

## Para o Claude Code dele

Leia o [CLAUDE.md](CLAUDE.md) antes de mexer em qualquer coisa: ele é o contrato do projeto — arquitetura (monolito modular com fronteiras rígidas entre `clients`, `hunter`, `ads` e `reports`), stack, convenções e o que não fazer. Em resumo:

- um módulo só importa outro pela API pública (`from app.<modulo> import <Api>`); o `import-linter` reprova o resto;
- trabalho demorado (scraper, Meta, checagem de site) vira job na tabela `job`, nunca chamada direta numa rota HTTP;
- código, comentários, nomes e commits em **inglês**; textos de interface em **português do Brasil**;
- uma tarefa só está pronta quando `.\make.ps1 check` passa.
