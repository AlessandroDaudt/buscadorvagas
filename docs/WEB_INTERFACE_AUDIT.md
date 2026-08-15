# Auditoria inicial da interface web

Data da auditoria: 20/07/2026. Baseline executado antes da alteração: 150 testes passaram,
3 foram ignorados por dependerem opcionalmente de modelo/GPU/fonte pública e houve um aviso de
depreciação Starlette/httpx no ambiente de teste.

## Estrutura encontrada

A interface é uma aplicação FastAPI monolítica em `job_hunt/web/app.py`. Ela serve uma única página
HTML (`templates/dashboard.html`), uma tela de login, CSS próprio e JavaScript nativo. Não há CDN,
Node ou framework de frontend. O painel usa SQLite/SQLAlchemy e executa a migração Alembic no início.

Arquivos existentes:

- `app.py`: criação do FastAPI, dependências e todas as rotas;
- `queries.py`: consultas de dashboard, paginação e detalhes;
- `schemas.py`: contratos Pydantic da API;
- `security.py`: login, sessão, CSRF, limite de requisição, Trusted Host e headers;
- `templates/dashboard.html` e `templates/login.html`;
- `static/app.css`, `static/login.css`, `static/dashboard.js` e favicon local.

## Rotas iniciais

| Método | Rota | Função |
|---|---|---|
| GET | `/health` | health check |
| GET/POST | `/login` | formulário e autenticação |
| POST | `/logout` | encerra sessão |
| GET | `/` | SPA do dashboard |
| GET | `/api/dashboard` | totais e agrupamentos reais |
| GET | `/api/metrics` | métricas locais |
| GET | `/api/jobs` | filtros limitados, ordenação e paginação |
| GET | `/api/jobs/compare` | comparação de duas ou três vagas |
| GET | `/api/jobs/{id}` | detalhes, análise, salário, pipeline e documentos |
| POST | `/api/jobs/{id}/disposition` | salvar/descartar/restaurar |
| POST | `/api/jobs/{id}/application` | atualizar pipeline e notas |
| POST | `/api/companies/{id}/silence` | silenciar empresa |
| GET/POST | `/api/settings` | ler/salvar conjunto restrito de settings |
| POST | `/api/jobs/{id}/documents` | gerar pacote de currículo/carta |

OpenAPI, Swagger e Redoc já estavam desativados.

## Autenticação e segurança encontradas

O painel exigia usuário/senha Argon2, tela de login, rate limit em memória, `SessionMiddleware` e
cookie de login. O mesmo cookie guardava o token CSRF. Já existiam `TrustedHostMiddleware`, CSP sem
scripts inline, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, limite global de corpo,
SameSite e respostas sem cache.

A autenticação deve ser removida. O token CSRF continuará em cookie de sessão estritamente local,
sem identidade de usuário. Operações mutáveis também precisam validar `Origin`/`Referer`, impedindo
que uma página externa dispare ações contra localhost.

## Integrações existentes

- **Scanner:** CLI chama `operations.execute_scan(config, companies, scanner.run_scan)`. O wrapper
  aplica `ScanLock`, métricas e `SearchRunRecord`. A web ainda não iniciava scans.
- **Scheduler:** `scheduler.py` calcula próxima execução, possui lock e executa a mesma CLI em processo
  filho com timeout. Configuração vem de `config/search_preferences.json`; a web não controlava a agenda.
- **Banco:** `Database.session()` fornece transação por contexto. Já existem registros de empresa,
  vaga, snapshots, análise, salário, pipeline, runs, currículo e documentos.
- **Documentos:** `DocumentGenerator` gera Markdown/DOCX e PDF opcional, sempre a partir de
  `MasterResume.approved`. A rota existente duplica a montagem de `UnifiedJob`; isso deve virar serviço.
- **Currículo:** `documents.importer` aceita MD/TXT/DOCX/PDF, mas valida apenas tamanho/extensão e
  extrai texto simples; não há assinatura, MIME, ZIP bomb, versionamento Markdown nem upload web.
- **Ollama/GPU:** `OllamaClient` já oferece tags, chat, embeddings e `/api/ps`; `doctor` agrega os checks.
- **Empresas/rede:** conectores e `SafeHttpClient` já implementam allowlist, SSRF, redirects, tamanho,
  content type, robots, cache, rate limit e auditoria.

## Funcionalidades disponíveis inicialmente

- três indicadores no dashboard;
- listagem paginada básica e filtro por texto/modalidade/status/score;
- detalhes em painel lateral;
- salvar, descartar e restaurar vaga;
- registrar pipeline manual;
- gerar currículo direcionado e cover letter;
- agrupamentos por empresa, fonte e pipeline;
- API de settings limitada.

## Lacunas

Não existiam navegação multipágina, scans assíncronos, tarefas persistentes, progresso/cancelamento,
CRUD de empresas, filtros avançados, gestão completa de notas, downloads seguros, exportação web,
gestão de scheduler, sistema/doctor, estado Ollama/GPU, edição segura de preferências, importação web,
conversão estruturada, versões de currículo, aprovação/ativação/restauração, gestão/edição de documentos,
atividades recentes ou tratamento completo de estados vazios e feedback.

## Componentes reutilizáveis

- `execute_scan`, `run_scan`, `ScanLock` e `SearchRunRecord`;
- consultas/persistência SQLAlchemy e Alembic;
- `ApplicationService`, `JobIngestionService` e repositórios;
- `DocumentGenerator` e `GeneratedDocumentRepository`;
- `SafeHttpClient`, registry/conectores e validação SSRF;
- `StateStore` para backups e escrita atômica;
- `OllamaClient`, `run_doctor`, métricas e logs;
- configuração tipada (`CandidateProfile`, `SearchPreferences`, `ScheduleConfiguration`);
- CSP, Trusted Host, limite de corpo e CSRF, adaptados para uso sem login.

## Plano de implementação

1. Extrair dependências/serviços compartilhados e remover apenas autenticação, mantendo sessão CSRF.
2. Adicionar tabelas Alembic para tarefas e versões de currículo; worker local persistente e recuperável.
3. Criar layout server-rendered e navegação, com JS local apenas para polling e ações.
4. Entregar dashboard e scans; depois vagas/detalhes e empresas.
5. Criar `resume_import` dedicado com validação de assinatura/MIME, extração determinística e versões.
6. Expor documentos, exportações, scheduler, sistema e configurações pelos mesmos serviços Python.
7. Cobrir segurança, uploads, tarefas e regressão; atualizar Docker/docs e testar manualmente por HTTP.

As rotas permanecerão pequenas: HTML e API chamarão a mesma camada de serviços; CLI e MCP não serão
substituídos nem invocados por subprocesso quando já houver função Python reutilizável.
