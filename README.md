# Autopilot Job Hunt — edição Alessandro Luis Daudt

Agente pessoal de descoberta e acompanhamento de vagas para cybersecurity, identity/IAM,
endpoint security, suporte enterprise e infraestrutura. O sistema coleta vagas, normaliza e
deduplica resultados, calcula compatibilidade explicável, estima salário, envia alertas e gera
documentos sob demanda.

> O agente **não envia candidaturas**. Salvar, descartar, planejar, marcar como candidatado e
> gerar documentos exigem uma ação explícita do usuário. A submissão final acontece fora do
> sistema, no canal oficial da empresa.

## Estado funcional

- perfil estruturado de Alessandro, preferências e empresas em arquivos configuráveis;
- SQLite para instalação simples e PostgreSQL para produção, com migrations Alembic;
- conectores extensíveis, incluindo Greenhouse, Lever e compatibilidade com o coletor TinyFish;
- normalização, deduplicação, atualização de republicações, snapshots e histórico;
- filtros geográficos que sinalizam falso remoto e restrições sem descarte silencioso;
- score explicável 0–100 com regras determinísticas e ajuste LLM estruturado e limitado;
- OpenAI, OpenRouter, Anthropic, Gemini e APIs locais compatíveis com OpenAI;
- fallback, retries, timeout, cache, limites de tokens/custo e ledger de uso;
- estimativa salarial rastreável, preservando moeda e distinguindo publicado de estimado;
- Telegram enriquecido e callbacks assinados; nenhuma ação de candidatura automática;
- currículo mestre JSON, currículo direcionado e cover letter em Markdown e DOCX, com PDF
  quando o ambiente suportar a conversão;
- painel FastAPI autenticado, responsivo, com dashboard, busca, filtros e pipeline auditável;
- scheduler com timezone, dias, horário, timeout, retry e exclusão mútua;
- logs JSON sem secrets, métricas locais, Docker Compose e CI de qualidade/segurança.

Fontes adicionais como Workday, SmartRecruiters, Ashby, Gupy, Remotive e feeds oficiais
continuam no roadmap. LinkedIn e Indeed só podem ser integrados por meios permitidos pelos
respectivos termos; não há CAPTCHA bypass ou evasão antibot.

## Arquitetura

```text
Conectores oficiais/TinyFish
        │
        ▼
normalização → deduplicação → Job + JobSnapshot + JobSource
        │
        ├─ filtros geográficos e funcionais
        ▼
score determinístico → análise LLM estruturada → explicação + custo/cache
        │
        ├─ salário publicado/inferido/estimado
        ├─ Telegram
        ├─ documentos sob demanda
        └─ painel e pipeline de candidatura

scheduler → subprocesso de scan com lock → SearchRun + relatório + métricas
```

As decisões importantes estão registradas em [docs/adr](docs/adr). O plano completo e o
baseline da arquitetura original estão em
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Configuração obrigatória

Os arquivos factuais são a fonte principal; não espalhe dados pessoais no código:

| Arquivo | Conteúdo |
|---|---|
| `config/candidate_profile.json` | experiência, formação, certificações, idiomas e competências |
| `config/search_preferences.json` | cargos, tecnologias, empresas, filtros e agenda |
| `config/salary_benchmarks.json` | faixas manuais e conversão opcional |
| `resume/master_resume.en.json` | currículo mestre factual em inglês |
| `companies.json` | páginas de carreira monitoradas |
| `config.json` | compatibilidade do scanner original e provedores |
| `.env` | somente secrets e overrides operacionais |

O currículo mestre inicial está com `approved: false`. Revise datas, contato, resultados e
descrições factuais e só então altere para `true`. A geração real de documentos é bloqueada sem
um currículo aprovado.

Copie os exemplos:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
Copy-Item config.example.json config.json
```

```bash
# Linux
cp .env.example .env
cp config.example.json config.json
```

No mínimo, configure `TINYFISH_API_KEY` para o scanner legado ou habilite conectores oficiais
na aplicação. Configure apenas um provedor de IA; os demais são opcionais.

## Instalação sem Docker

Requer Python 3.11–3.13.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -c constraints.lock -e ".[dev,documents]"
autopilot db seed
autopilot --help
```

Se a política do PowerShell bloquear a ativação, use diretamente
`.venv\Scripts\python.exe` e `.venv\Scripts\autopilot.exe`.

### Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints.lock -e '.[dev,documents]'
autopilot db seed
autopilot --help
```

SQLite é o padrão e cria `state/autopilot.db`. Para PostgreSQL, instale o extra `postgres` e
defina uma URL com credenciais codificadas corretamente:

```bash
python -m pip install -c constraints.lock -e '.[postgres,documents]'
export DATABASE_URL='postgresql+psycopg://user:password@host:5432/autopilot'
autopilot db upgrade
```

## Execução

```bash
autopilot scan                 # busca manual, com lock e SearchRun
autopilot schedule --once      # busca limitada pelo max_duration_minutes
autopilot schedule             # daemon conforme search_preferences.json
autopilot web                  # painel em http://127.0.0.1:8000
autopilot documents '#1'       # geração explícita para uma vaga selecionada
autopilot export --min 70      # CSV do resultado
```

A agenda padrão usa `America/Sao_Paulo`, 08:00, segunda a sexta. Altere `enabled`, `days`,
`time` e `max_duration_minutes` em `config/search_preferences.json`. A trava
`state/scan.lock` impede duas buscas simultâneas; locks abandonados expiram após o limite da
execução mais uma margem de segurança.

## Painel seguro

Gere um hash Argon2 sem colocar a senha em histórico de shell:

```bash
autopilot panel hash-password
```

Preencha em `.env`:

```dotenv
PANEL_USERNAME="admin"
PANEL_PASSWORD_HASH="resultado_do_comando"
PANEL_SESSION_SECRET="segredo_aleatorio_com_ao_menos_32_bytes"
PANEL_ALLOWED_HOSTS="localhost,127.0.0.1"
PANEL_SECURE_COOKIE="false"
```

Use `PANEL_SECURE_COOKIE=true` quando o painel estiver atrás de HTTPS. Não exponha a porta
diretamente à Internet: use VPN ou reverse proxy com TLS e autenticação adicional. O painel
desabilita documentação pública da API, valida Host/CSRF/tamanho, aplica CSP e nunca retorna
secrets completos.

## Docker Compose

Docker Desktop ou Docker Engine com Compose v2:

```bash
cp .env.example .env                 # PowerShell: Copy-Item .env.example .env
cp config.example.json config.json   # PowerShell: Copy-Item config.example.json config.json
# configure POSTGRES_PASSWORD, painel, provedor e TinyFish no .env
docker compose config                # valida antes de iniciar
docker compose up -d --build
docker compose ps
```

O painel fica em `http://127.0.0.1:8000`. Os serviços são:

- `database`: PostgreSQL 17 com healthcheck;
- `panel`: aplicação web não-root;
- `scheduler`: processo agendador; o subprocesso executado por ele é o worker atual.

Não existe broker ou fila nesta fase. Os volumes `postgres_data`, `state_data` e `output_data`
são persistentes. A imagem remove Linux capabilities, ativa `no-new-privileges` e não contém
`.env`, `config.json` ou o currículo pessoal no contexto de build.

## Provedores de IA e custo

Defina `ai.enabled`, provedor principal e fallbacks em `config.json`; secrets ficam em `.env`.
Variáveis suportadas incluem `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY` e uma URL compatível com OpenAI para modelo local. O modelo OpenAI pode ser
selecionado por `OPENAI_MODEL`.

Os prompts ficam em `job_hunt/prompts/job_analysis/v1/`, são versionados e separam a descrição
não confiável das instruções. O ajuste do LLM não substitui o score determinístico e fica
limitado. Configure limites por execução e mês antes de habilitar consenso entre modelos.

## Telegram

Crie o bot com `@BotFather` e defina:

```dotenv
TELEGRAM_TOKEN="..."
TELEGRAM_CHAT_ID="..."
TELEGRAM_CALLBACK_SECRET="segredo_aleatorio_com_ao_menos_32_bytes"
```

Alertas incluem cargo, empresa, localização/modalidade, idade da vaga, score, salário,
forças, lacunas, restrições, fonte e link oficial. Payloads de ações são assinados com HMAC e
validados contra chat/usuário permitido. O projeto contém a camada segura de callbacks; a
exposição de webhook público exige TLS e configuração operacional adicional.

## Backup e restauração

Pare scheduler/painel ou assegure um snapshot consistente antes do backup.

SQLite:

```bash
python -c "import sqlite3; src=sqlite3.connect('state/autopilot.db'); dst=sqlite3.connect('backup-autopilot.db'); src.backup(dst); dst.close(); src.close()"
# restauração: mantenha o original, copie o backup para state/autopilot.db e execute:
autopilot db upgrade
```

PostgreSQL no Compose:

```bash
docker compose exec -T database pg_dump -U autopilot -d autopilot -Fc > autopilot.dump
docker compose exec -T database pg_restore -U autopilot -d autopilot --clean --if-exists < autopilot.dump
docker compose run --rm panel autopilot db upgrade
```

Guarde também, de forma criptografada e separada: `config/`, `companies.json`, currículo mestre
aprovado e documentos de `output`. Nunca inclua `.env` em repositório ou backup sem proteção.

## Qualidade e segurança

```bash
python -m ruff check job_hunt tests conftest.py
python -m mypy
python -m pytest --cov=job_hunt --cov-report=term-missing
python -m alembic check
python -m pip check
python -m build
```

O GitHub Actions também executa `pip-audit`, Bandit, Gitleaks, validação de pacote e build da
imagem sem push. Atualize o lock com:

```bash
python -m pip install uv
uv pip compile --all-extras --universal pyproject.toml --output-file constraints.lock
```

Descrições de vagas e páginas coletadas são dados não confiáveis: nunca são executadas como
comandos. Requisições têm allowlist/validação contra SSRF, limites e timeout. Logs usam JSON e
redaction, com contexto `run_id`, `source_id` e `job_id` quando disponível.

## Diagnóstico rápido

- `config.json not found`: copie `config.example.json`.
- `TINYFISH_API_KEY not set`: configure `.env`; placeholders são rejeitados.
- `no approved master resume`: revise e aprove `resume/master_resume.en.json`.
- cookie do painel não persiste localmente: use `PANEL_SECURE_COOKIE=false` apenas em HTTP local.
- `another scan owns state/scan.lock`: existe uma busca em andamento; não apague o lock de um
  processo ativo. Locks realmente abandonados expiram automaticamente.
- horário incorreto: confirme `timezone`, `days` e hora do host/contêiner.
- DOCX funciona, PDF não: instale uma conversão suportada no host ou mantenha Markdown/DOCX.
- Compose rejeita a configuração: preencha `POSTGRES_PASSWORD` e valide com
  `docker compose config`.

Mais detalhes históricos estão em [docs](docs/README.md). Vulnerabilidades devem ser relatadas
conforme [SECURITY.md](SECURITY.md); privacidade e dados enviados a provedores estão descritos
em [PRIVACY.md](PRIVACY.md).

## Licença

MIT. Baseado no projeto original `tarunlnmiit/autopilot-jobhunt`, evoluído incrementalmente para
o perfil e o fluxo de revisão de Alessandro Luis Daudt.
