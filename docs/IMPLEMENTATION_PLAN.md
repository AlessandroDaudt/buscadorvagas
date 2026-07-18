# Plano de implementação — agente de vagas de Alessandro Luis Daudt

Status: proposta para revisão  
Baseline analisado: `autopilot-jobhunt` 0.4.4  
Data da auditoria: 2026-07-18  
Timezone padrão: `America/Sao_Paulo`

## 1. Objetivo e princípios

Este plano evolui o projeto existente de forma incremental para um agente pessoal de
busca de empregos que descubra, normalize, avalie e acompanhe vagas para Alessandro Luis
Daudt. O sistema deverá manter uma pessoa no controle: poderá localizar vagas e preparar
documentos, mas nunca enviará candidaturas sem uma ação explícita do usuário fora do fluxo
automatizado.

Princípios de implementação:

- preservar os comandos CLI, o servidor MCP, a integração TinyFish e os fluxos que já
  funcionam;
- separar domínio, persistência e integrações gradualmente, sem uma reescrita integral;
- manter descrições de vagas e páginas coletadas como dados não confiáveis;
- manter secrets somente em variáveis de ambiente ou secret manager;
- não depender de um único provedor de IA, site ou formato de armazenamento;
- tornar decisões automatizadas explicáveis, rastreáveis e reproduzíveis;
- usar fixtures e mocks para que a suíte normal não dependa de sites ou APIs reais;
- registrar decisões arquiteturais relevantes em `docs/adr/` antes da implementação.

## 2. Baseline da auditoria

### 2.1 Estado do workspace

- A pasta contém os arquivos do projeto e o arquivo `autopilot-jobhunt-main.zip`, mas não
  contém `.git` e não é reconhecida como um repositório Git.
- Não existem `config.json` nem `.env`; somente os respectivos templates.
- `state/` e `output/` contêm apenas `.gitkeep`.
- `resume/YOUR_RESUME.md` ainda é o template genérico.
- Não existem banco, migrations, Docker Compose, painel web ou diretório de ADRs.
- O ambiente não possui Python funcional, launcher `py`, `uv`, Docker, Ruff ou mypy.

Consequências:

- não foi possível verificar branch, histórico ou mudanças locais;
- não foi possível executar testes, lint, type checking, build ou auditoria de
  dependências;
- commits locais somente poderão ser preparados quando o workspace for um clone Git;
- antes da Fase 1 deverá ser instalado Python 3.11 ou superior em um ambiente virtual.

### 2.2 Baseline de validação

Comandos tentados:

```powershell
python -m pytest --cov=job_hunt --cov-report=term-missing
python -m ruff check job_hunt tests conftest.py
python -m mypy
python -m pip check
```

Resultado: todos foram interrompidos antes da execução porque `python.exe` é apenas o
alias da Microsoft Store. Nenhum artefato de teste ou cobertura foi criado.

Existem 80 testes declarados. A configuração de CI exige cobertura mínima de 85% e roda
Ruff, mypy e pytest em Python 3.11, 3.12 e 3.13.

## 3. Arquitetura atual

### 3.1 Linguagem, frameworks e dependências

- Python 3.11+ e empacotamento com Hatchling.
- CLI exposta pelos comandos `autopilot` e `autopilot-jobhunt`.
- TinyFish para busca e leitura de páginas.
- SDK OpenAI apontado para OpenRouter.
- Anthropic opcional e Claude CLI como alternativas.
- `requests` para Telegram e WhatsApp.
- FastMCP opcional para integração com assistentes.
- pytest, Ruff e mypy como ferramentas de desenvolvimento.

As dependências possuem limites mínimos amplos e não há lockfile ou constraints
reproduzíveis.

### 3.2 Módulos

| Arquivo | Responsabilidade atual |
|---|---|
| `job_hunt/main.py` | CLI, scaffolding, configuração e exportação CSV |
| `job_hunt/scanner.py` | descoberta, fetch, scoring, persistência e Telegram |
| `job_hunt/llm_utils.py` | despacho entre OpenRouter, Anthropic e Claude CLI |
| `job_hunt/drafter.py` | currículo, cover letter e informações da vaga |
| `job_hunt/notifier.py` | Telegram e WhatsApp |
| `job_hunt/tools.py` | adaptadores usados pelo MCP |
| `job_hunt/mcp_server.py` | ferramentas MCP de scan, draft e export |
| `job_hunt/log.py` | log textual em console e arquivo |

O ponto de entrada principal é `job_hunt.main:main`. O fluxo MCP delega às mesmas
funções por meio de `job_hunt.tools`.

### 3.3 Pipeline atual

1. `load_config()` combina `config.json` e `.env`.
2. `load_companies()` lê `companies.json`.
3. `run_scan()` cria um cliente TinyFish e carrega o currículo Markdown.
4. Para cada empresa, `discover_job_urls()` lê a página de carreiras, expande páginas
   ATS reconhecidas e executa uma busca `site:<domínio>`.
5. URLs presentes em `state/seen_jobs.json` são ignoradas.
6. `fetch_job_details()` guarda até 3.000 caracteres de cada página.
7. `score_jobs()` envia lotes de até dez vagas ao LLM e extrai uma matriz JSON livre.
8. Somente itens que o próprio LLM marcou como `worth_applying` são mantidos.
9. Resultados são escritos em `last_scan.json`, `job_history.json` e CSV.
10. Os melhores resultados são enviados ao Telegram quando configurado.

### 3.4 Fontes existentes

O projeto possui uma única implementação de coleta, baseada no TinyFish, que atua sobre
140 páginas configuradas. Há reconhecimento por expressão regular para links de:

- Greenhouse;
- Lever;
- Workday;
- SmartRecruiters;
- Ashby;
- Workable, apenas em parte do fluxo de listagem.

Isso ainda não constitui conectores separados. Erros e capacidades não são reportados
por fonte de forma uniforme.

### 3.5 Scoring atual

O score de 0 a 100, a decisão `worth_applying`, título, stack, localização e justificativa
são produzidos inteiramente pelo LLM. Não existem:

- dimensões determinísticas;
- correspondência semântica independente;
- schema tipado;
- validação entre score e limiar;
- distinção entre requisito obrigatório e desejável;
- explicação detalhada ou versão do prompt persistida.

### 3.6 Provedores de LLM

Há uma função comum `chat_with_llm()`, com suporte a:

- OpenRouter por API compatível com OpenAI, com cadeia de fallback;
- Anthropic API;
- Claude CLI.

Já existem timeout HTTP e retry simples por rate limit. Ainda faltam OpenAI direto,
Gemini, endpoint local configurável, políticas uniformes de retry, schemas, cache, custo,
orçamento, telemetria de tokens e consenso.

### 3.7 Telegram

O Telegram envia mensagens HTML usando `sendMessage`. É uma integração unidirecional;
não há bot polling/webhook, callbacks, autenticação de ações, persistência de notificações
ou preferências de silenciamento.

### 3.8 Persistência

A persistência usa arquivos locais:

- `state/seen_jobs.json`;
- `state/last_scan.json`;
- `state/job_history.json`;
- `output/jobs_<data>.csv`;
- `output/<empresa>-<data>/` para documentos.

Não há transações, migrations, escrita atômica, lock entre processos, relacionamentos,
snapshots completos ou trilha de auditoria.

### 3.9 Agendamento e execução

- execução manual pela CLI ou MCP;
- script `setup_cron.sh` para cron diário às 02:30 no horário local da máquina;
- sem timezone explícito, dias configuráveis, duração máxima ou lock;
- Dockerfile inicia apenas o servidor MCP por `stdio`;
- não existe Compose nem scheduler para Windows.

### 3.10 Documentos

`drafter.py` produz:

- currículo personalizado em Markdown;
- cover letter em Markdown;
- informações da candidatura em texto.

Não há currículo mestre estruturado, importação, DOCX/PDF, idioma automático, metadados,
versionamento, diff ou verificação factual após geração.

### 3.11 Testes e CI

A suíte cobre CLI, configuração, exportação, scanner, LLMs, drafter, Telegram, MCP e
persistência básica. TinyFish, LLMs, subprocessos e HTTP são mockados.

A GitHub Action atual executa:

- Ruff;
- mypy;
- pytest com cobertura;
- smoke test de importação;
- gitleaks;
- publicação no PyPI em tags.

Ainda não há build explícito em toda alteração, auditoria de dependências, verificação de
migrations, testes de Compose ou política de publicação alinhada ao fork personalizado.

## 4. Funcionalidades existentes a preservar

- CLI `init`, `scan`, `draft`, `export` e `mcp`.
- Regra de nunca enviar candidaturas automaticamente.
- TinyFish como conector legado durante a migração.
- Lista externa de empresas.
- Descoberta por página de carreiras e busca por domínio.
- Pacing conservador e fallback de modelos OpenRouter.
- Anthropic e Claude CLI opcionais.
- Telegram opcional e falha não fatal.
- Exportação CSV e documentos locais.
- Servidor MCP e camada `tools.py`.
- Suíte sem dependência permanente de rede.

## 5. Problemas encontrados

### 5.1 Funcionais e de dados

1. Deduplicação apenas por URL; URLs equivalentes, múltiplas fontes e republicações geram
   resultados incorretos.
2. URLs vistas não são revisitadas, então alterações e encerramentos não são percebidos.
3. A URL é marcada como vista antes da confirmação de análise; uma falha pode ocultar a
   vaga permanentemente.
4. `score_jobs()` captura erros e retorna lista vazia, impedindo o fallback externo de
   distinguir “nenhuma vaga aprovada” de “análise falhou”.
5. Vagas abaixo do limiar não permanecem no histórico, dificultando auditoria e ajuste de
   filtros.
6. `worth_applying` não é copiado para o objeto persistido, deixando a coluna CSV
   inconsistente.
7. `companies_path` é aceito em `tool_scan()` mas não é efetivamente usado.
8. O processo altera o diretório global com `os.chdir()`, o que é inseguro sob
   concorrência.
9. Arquivos JSON podem ser corrompidos se uma escrita for interrompida.
10. O CSV diário pode ser sobrescrito por uma segunda execução no mesmo dia.

### 5.2 Configuração e manutenção

1. Configuração sem schema e com validação parcial.
2. Perfil profissional condensado em strings livres.
3. Prompts embutidos em `scanner.py` e `drafter.py`.
4. Scanner concentra descoberta, scoring, persistência, relatórios e notificações.
5. Integrações não possuem interfaces ou contratos comuns.
6. Não há IDs estáveis de domínio nem injeção explícita de dependências.
7. Documentação recomenda secrets também em `config.json`.
8. Não há lockfile/constraints para builds reproduzíveis.
9. A lista atual é orientada a ML/IA na Europa e não ao perfil de Alessandro.

### 5.3 Segurança e privacidade

1. Descrições não confiáveis são interpoladas diretamente em prompts, permitindo prompt
   injection e manipulação do score ou dos documentos.
2. Respostas do LLM são carregadas sem schema, tipos, intervalos ou limites de tamanho.
3. Campos não confiáveis são inseridos em Telegram HTML sem escape.
4. URLs externas não passam por política de esquema, DNS/IP, redirects, portas ou hosts.
5. Não há limite central de tamanho de respostas/páginas e o texto completo pode existir
   antes do truncamento local.
6. Logs não são estruturados e podem registrar títulos, justificativas e erros externos
   sem política central de redaction.
7. O painel futuro exigirá autenticação, autorização, CSRF, cookies seguros condicionais,
   rate limiting e proteção de endpoints administrativos.
8. Currículo e descrição transitam por TinyFish e pelo provedor LLM; o usuário deve poder
   escolher, consentir e controlar retenção.
9. Callbacks futuros do Telegram precisarão validar bot, chat, usuário, ação, expiração e
   replay.

## 6. Funcionalidades ausentes

- perfil e currículo mestre estruturados de Alessandro;
- empresas, cargos, tecnologias e preferências solicitadas;
- domínio unificado de vagas e fontes;
- conectores por plataforma;
- deduplicação composta e snapshots;
- filtros por localização, modalidade, contrato, salário e estado;
- detecção explicável de falso remoto;
- scoring híbrido e explicável por dimensão;
- análise estruturada e versionada por LLM;
- OpenAI direto, Gemini e endpoints locais;
- custos, cache, fallback uniforme e consenso;
- estimativa salarial com proveniência e conversão;
- geração DOCX/PDF e rastreabilidade de documentos;
- bot Telegram com ações;
- banco relacional e migrations;
- aplicações e eventos de pipeline;
- painel web;
- scheduler robusto e relatório de execução;
- observabilidade estruturada;
- Compose e operação documentada para Windows e Linux.

## 7. Arquitetura proposta

### 7.1 Estratégia incremental

O pacote `job_hunt` será mantido. Os módulos atuais inicialmente se tornarão fachadas e
delegarão para componentes novos. Cada extração será acompanhada por testes de
caracterização, evitando um “big bang”.

Estrutura alvo indicativa:

```text
job_hunt/
  cli/
  config/
  domain/
    models/
    services/
    enums.py
  application/
    collection.py
    analysis.py
    documents.py
    notifications.py
    applications.py
  connectors/
    base.py
    tinyfish.py
    greenhouse.py
    lever.py
    smartrecruiters.py
    ashby.py
    workday.py
    feeds/
  llm/
    base.py
    providers/
    schemas.py
    cache.py
    budgets.py
  persistence/
    database.py
    repositories/
    migrations/
  scoring/
    deterministic.py
    semantic.py
    llm_review.py
    aggregate.py
  salary/
  documents/
  notifications/
  scheduler/
  web/
  prompts/
```

Os nomes finais serão confirmados por ADR e podem ser simplificados para evitar pastas
com apenas um arquivo.

### 7.2 Configuração

Configuração não secreta será validada e separada por finalidade:

- `config/candidate_profile.json` — perfil factual e preferências;
- `config/search_preferences.json` — cargos, tecnologias, filtros e agenda;
- `companies.json` — preservado e ampliado para não quebrar o fluxo atual;
- `resume/master_resume.json` — currículo mestre factual e versionável pelo usuário;
- `.env` — secrets, URLs de banco e chaves criptográficas;
- `.env.example` — nomes e exemplos sem valores reais.

Variáveis de ambiente terão precedência clara. Secrets presentes em arquivos JSON serão
rejeitados ou migrados com aviso, nunca exibidos pelo painel.

### 7.3 Modelo de domínio

Serão criados modelos tipados para:

- `CandidateProfile`;
- `ResumeMaster`;
- `Company`;
- `Job`;
- `JobSource`;
- `JobSnapshot`;
- `JobAnalysis`;
- `SalaryEstimate`;
- `GeneratedDocument`;
- `Application`;
- `ApplicationEvent`;
- `SearchRun`;
- `Notification`;
- `PromptVersion`;
- `LLMUsage`;
- `UserSetting`.

IDs públicos deverão usar UUIDs. Estados, modalidade, senioridade, contrato, moeda,
periodicidade e classificação serão enums ou vocabulários controlados, mantendo também
o valor bruto da fonte.

### 7.4 Persistência

Proposta a confirmar no ADR 0001:

- SQLAlchemy 2 para independência de banco;
- SQLite com WAL para instalação local;
- PostgreSQL recomendado em produção;
- Alembic para migrations;
- repositories para impedir que CLI, conectores e painel dependam de consultas diretas;
- transações por lote/fonte;
- timestamps UTC no banco e conversão para `America/Sao_Paulo` na interface;
- retenção configurável de snapshots, análises, documentos e logs.

### 7.5 Conectores

Contrato comum proposto:

```python
class JobConnector(Protocol):
    source_name: str

    def discover(self, context: SearchContext) -> CollectionResult: ...
```

Cada `CollectionResult` terá vagas normalizadas, avisos, erros, duração, paginação e
status. O conector legado TinyFish será adaptado primeiro. Em seguida serão priorizados
feeds/APIs oficiais e ATS com endpoints públicos permitidos.

Não serão implementados CAPTCHA bypass, evasão de autenticação, rotação abusiva de
identidade ou scraping contrário aos termos. LinkedIn e Indeed somente serão usados por
integrações permitidas e explicitamente habilitadas.

### 7.6 Normalização e deduplicação

A estratégia será registrada no ADR 0002 e combinará:

1. fonte + identificador externo;
2. URL canônica sem parâmetros de tracking;
3. hash normalizado da descrição;
4. empresa normalizada + título + localização;
5. similaridade textual limitada a candidatos plausíveis.

O processo produzirá uma decisão explicável: `new`, `updated`, `republished`,
`duplicate` ou `unchanged`. Snapshots preservarão mudanças relevantes e datas de
primeira/última visualização.

### 7.7 Filtros e localização

Filtros serão regras puras, testáveis e configuráveis. Uma vaga não será descartada sem
registrar motivos. Restrições geográficas serão classificadas, por exemplo:

- global e aceita Brasil;
- remoto Brasil;
- remoto LATAM;
- remoto restrito a outro país;
- autorização de trabalho incompatível;
- híbrido/presencial compatível com Rio Grande do Sul;
- modalidade ambígua, exigindo revisão.

### 7.8 Scoring

O score final de 0 a 100 combinará:

- regras determinísticas para títulos, requisitos, localização, idioma, formação,
  certificações, senioridade e salário;
- similaridade semântica, opcional e com implementação substituível;
- análise estruturada do LLM;
- penalidades explícitas apenas para requisitos realmente obrigatórios.

Pesos serão configuráveis e normalizados. O LLM não poderá determinar sozinho o score
final nem alterar o limiar de aprovação.

O resultado persistido conterá:

- score total e componentes;
- evidências e versão das regras;
- conhecimentos comprovados, relacionados, transferíveis e ausentes;
- pontos fortes, lacunas, riscos e restrições;
- requisitos obrigatórios e desejáveis não atendidos;
- recomendação e nível de confiança;
- provedor, modelo, prompt, tokens, custo e cache.

### 7.9 LLMs e proteção de prompts

Proposta a confirmar no ADR 0003:

- interface única de provider;
- OpenAI direto, OpenRouter, Anthropic, Gemini e API local compatível com OpenAI;
- provider principal e lista de fallback;
- timeout, retry exponencial com jitter e circuit breaker simples;
- limite por chamada, execução e mês;
- cache por hash de prompt, vaga, perfil, schema e modelo;
- respostas estruturadas validadas;
- prompts versionados fora do código;
- consenso opcional e divergências persistidas.

Descrições serão delimitadas como dados, precedidas por instruções de segurança e nunca
terão autoridade de sistema. Saídas passarão por validação factual e de schema. Nenhum
texto coletado será executado como comando, URL automática ou instrução de ferramenta.

### 7.10 Salário

O módulo manterá valor e moeda originais e aplicará a precedência:

1. publicado na vaga;
2. faixa oficial da empresa;
3. configuração manual;
4. fonte externa legalmente acessível;
5. estimativa por cargo, senioridade, empresa, país e modalidade.

Cada resultado terá intervalo, moeda, periodicidade, bruto/líquido/desconhecido, tipo
`published`, `inferred`, `converted` ou `estimated`, fonte, data, confiança e
justificativa. Conversões BRL serão opcionais e datadas.

### 7.11 Documentos

O currículo mestre será JSON estruturado, legível e validado. Markdown continuará como
primeiro formato de saída. DOCX será adicionado de maneira isolada; PDF será opcional e
somente ativado com backend confiável disponível.

Toda geração guardará vaga, data, versão, idioma, modelo, prompt, hash do currículo
mestre e diff estruturado. Uma etapa de verificação comparará afirmações geradas com a
fonte factual e bloqueará fatos, datas, tecnologias, certificações ou métricas não
suportados.

### 7.12 Telegram

Mensagens usarão escape de HTML e limites de tamanho. Ações poderão operar por polling
local ou webhook HTTPS configurável. Callbacks usarão IDs opacos, expiração, allowlist
de usuário/chat, prevenção de replay e validação do estado atual da vaga.

Gerar documentos continuará exigindo uma ação explícita. “Abrir vaga” apontará para a
URL oficial; não haverá preenchimento ou envio automático de formulários.

### 7.13 Painel web

Proposta a confirmar no ADR 0004: FastAPI com páginas renderizadas no servidor e
JavaScript mínimo. Essa abordagem reutiliza os modelos Python e reduz a necessidade de
um frontend separado.

Requisitos de segurança:

- autenticação obrigatória fora de modo local explicitamente configurado;
- hash de senha moderno ou OIDC;
- autorização em endpoints administrativos;
- CSRF para mutações baseadas em cookie;
- cookies `HttpOnly`, `SameSite` e `Secure` somente sob HTTPS;
- rate limiting;
- validação e sanitização de entrada/HTML;
- secrets mascarados e nunca devolvidos ao navegador;
- bind em localhost por padrão.

### 7.14 Scheduler, observabilidade e relatórios

O scheduler terá execução manual e programada, timezone, dias, horário, duração máxima,
lock distribuído/local e retry por fonte. O modelo de worker será registrado no ADR
0005; inicialmente será preferido um processo simples, evitando Celery/Redis sem
necessidade comprovada.

Logs serão estruturados com timestamp, módulo, nível, run ID, source ID, job ID, duração
e status. Métricas iniciais serão agregadas em banco/log, sem exigir uma plataforma
externa.

## 8. Fases de implementação

### Fase 0 — Auditoria e planejamento

Entregas:

- este plano;
- baseline reproduzível após instalação do Python;
- restauração ou criação correta do clone Git pelo usuário;
- ADRs iniciais antes da Fase 1.

Critério de saída: plano revisado e testes atuais executados ou bloqueio de ambiente
formalmente aceito.

### Fase 1 — Configuração, domínio e persistência

Entregas:

- perfil estruturado inicial de Alessandro;
- currículo mestre inicial sem fatos inventados;
- configuração de cargos, tecnologias, localização e empresas;
- modelos de domínio e schemas;
- SQLite, suporte PostgreSQL e migrations;
- repositories e importação dos JSONs existentes;
- comandos de diagnóstico e migrations;
- ADRs de banco e formato do currículo.

Mudanças serão introduzidas atrás de adaptadores, mantendo export e comandos atuais.

### Fase 2 — Coleta, normalização e histórico

Entregas:

- interface de conectores;
- adaptador TinyFish legado;
- conectores oficiais/ATS prioritários;
- normalização e validação de URLs;
- deduplicação composta;
- snapshots, republicação e relatório de fontes;
- limites de conteúdo, timeout, retry e proteção SSRF.

### Fase 3 — Filtros, scoring e LLMs

Entregas:

- filtros configuráveis e falso remoto;
- scoring determinístico por dimensões;
- similaridade semântica opcional;
- schemas e prompts versionados;
- providers adicionais, fallback, cache e orçamento;
- explicação completa e consenso opcional;
- proteção e testes de prompt injection.

### Fase 4 — Salário, alertas e relatórios

Entregas:

- salário publicado/inferido/estimado;
- conversão opcional BRL;
- Telegram enriquecido e ações autenticadas;
- preferências e silenciamentos;
- relatório persistido de cada execução.

### Fase 5 — Currículo e cover letter

Entregas:

- importação assistida e edição do currículo mestre;
- geração Markdown e DOCX;
- PDF opcional;
- inglês e português;
- versionamento, hash, diff e verificação factual;
- cover letters objetivas e configuráveis.

### Fase 6 — Painel e pipeline de candidatura

Entregas:

- autenticação e configurações seguras;
- dashboard, lista, busca, filtros, paginação e comparação;
- detalhes, análise e geração de documentos;
- estados e eventos de candidatura;
- configurações sem exposição de secrets.

### Fase 7 — Qualidade e produção

Entregas:

- cobertura dos casos de aceite;
- hardening, auditoria de dependências e secret scanning;
- logging estruturado e métricas;
- scheduler robusto;
- Docker Compose para aplicação, banco, worker e scheduler quando necessários;
- documentação Windows, Linux, Docker, backup, restauração e troubleshooting;
- CI com build, migrations e auditorias.

## 9. Arquivos previstos

### 9.1 Arquivos existentes a alterar gradualmente

- `README.md`;
- `SETUP.md`;
- `.env.example`;
- `.gitignore`;
- `config.example.json` e cópia empacotada;
- `companies.json` e cópia empacotada;
- `pyproject.toml`;
- `requirements.txt`, que deverá ser consolidado ou gerado para não divergir;
- `Dockerfile`;
- `.github/workflows/ci.yml`;
- `job_hunt/main.py`;
- `job_hunt/scanner.py`;
- `job_hunt/llm_utils.py`;
- `job_hunt/drafter.py`;
- `job_hunt/notifier.py`;
- `job_hunt/tools.py`;
- `job_hunt/mcp_server.py`;
- `job_hunt/log.py`;
- testes atuais afetados por compatibilidade.

### 9.2 Arquivos/diretórios novos propostos

- `docs/adr/` e ADRs numerados;
- `config/candidate_profile.example.json`;
- `config/search_preferences.example.json`;
- `resume/master_resume.example.json`;
- pacotes de domínio, conectores, persistência, scoring, salário, documentos,
  notificações, scheduler e web descritos na seção 7;
- diretório de migrations;
- `tests/fixtures/jobs/` com páginas locais;
- testes unitários, de integração, migrations, documentos e segurança;
- `compose.yml`;
- arquivo de constraints/lock compatível com a decisão de empacotamento.

Arquivos pessoais reais continuarão ignorados pelo Git; somente templates sanitizados
serão versionados.

## 10. Dependências novas propostas

As dependências somente serão adicionadas quando a fase correspondente começar e após
ADR/revisão:

| Dependência | Uso | Fase |
|---|---|---|
| Pydantic 2 | schemas de configuração, domínio e LLM | 1 |
| SQLAlchemy 2 | persistência SQLite/PostgreSQL | 1 |
| Alembic | migrations | 1 |
| driver PostgreSQL | produção opcional | 1/7 |
| HTTPX | cliente HTTP com limites e políticas uniformes | 2 |
| SDKs OpenAI/Anthropic/Gemini | providers opcionais | 3 |
| `python-docx` | exportação DOCX | 5 |
| FastAPI/Uvicorn/Jinja2 | painel server-rendered | 6 |
| biblioteca de hash de senha | autenticação local | 6 |
| APScheduler ou equivalente | somente se cron/processo simples for insuficiente | 7 |
| `pip-audit` | auditoria de dependências no CI | 7 |

Será evitada uma biblioteca pesada de embeddings na instalação padrão. Similaridade
textual determinística será o baseline; embeddings poderão ser um extra opcional.

## 11. Estratégia de testes

### 11.1 Baseline por fase

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\ruff.exe check job_hunt tests conftest.py
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\pytest.exe --cov=job_hunt --cov-report=term-missing
.\.venv\Scripts\python.exe -m pip check
```

Cada fase deverá executar testes focados durante o desenvolvimento e a suíte completa
antes da entrega. Cobertura não poderá cair abaixo do gate existente de 85%.

### 11.2 Categorias

- unitários para normalização, filtros, scoring e salário;
- parsing por conector usando fixtures locais;
- integração entre repositories e SQLite temporário;
- migrations vazias e com dados legados;
- contratos de providers LLM com respostas gravadas/mocks;
- Telegram com HTTP e callbacks mockados;
- documentos com inspeção de conteúdo e metadados;
- web com cliente de teste e verificações de autenticação/CSRF;
- Compose e smoke tests sem chamar sites reais;
- segurança para URL, HTML, secrets, limites e prompt injection.

### 11.3 Casos obrigatórios

- remoto restrito aos Estados Unidos;
- remoto global aceitando Brasil;
- duplicata em duas fontes;
- mesma vaga republicada;
- salário anual em USD;
- salário mensal em BRL;
- salário ausente;
- resposta inválida ou fora do schema do LLM;
- timeout e fallback de LLM;
- falha e retry controlado do Telegram;
- descrição com tentativa de prompt injection;
- URL privada, loopback, link-local, esquema inválido e redirect proibido;
- duas execuções simultâneas;
- rollback de migration e importação repetida/idempotente;
- documento tentando introduzir um fato não presente no currículo mestre.

### 11.4 Validação adicional

- build de wheel e sdist;
- instalação limpa do artefato;
- `alembic upgrade head` e verificação de migrations pendentes;
- auditoria de dependências;
- gitleaks;
- smoke test de CLI, MCP e painel;
- validação do Compose;
- teste manual pequeno por provider somente quando credenciais forem fornecidas.

## 12. Estratégia de migração

1. Criar testes de caracterização para o formato JSON atual.
2. Adicionar banco e repositories sem remover arquivos existentes.
3. Implementar um importador idempotente para `seen_jobs.json`, `last_scan.json` e
   `job_history.json`.
4. Fazer dual-read temporário: banco primeiro, JSON como fallback somente durante a
   janela de migração.
5. Opcionalmente fazer dual-write por uma versão, com métricas de divergência.
6. Criar backup datado antes da primeira migração de dados reais.
7. Validar contagens, URLs, scores, datas e hashes após importação.
8. Tornar o banco a fonte principal após aceite.
9. Preservar JSON/CSV apenas como exportação e backup, sem exclusão automática.
10. Fornecer comando documentado de rollback e restauração.

SQLite será o padrão local. A migração SQLite → PostgreSQL usará o mesmo schema e um
comando de export/import testado; não dependerá de copiar o arquivo SQLite diretamente.

## 13. Critérios de aceite

### 13.1 Fase 1

- perfil de Alessandro carregado de um arquivo estruturado e validado;
- nenhuma chave secreta em arquivos de configuração não secretos;
- empresas, cargos e tecnologias prioritários configuráveis;
- modelos e migrations criam todas as entidades mínimas;
- SQLite funciona localmente e PostgreSQL é configurável;
- dados legados podem ser importados de forma idempotente;
- comandos atuais continuam disponíveis;
- testes, lint e tipos passam.

### 13.2 Produto funcional

- múltiplas fontes consultadas por uma interface comum;
- vagas normalizadas, deduplicadas, atualizadas e historizadas;
- restrições geográficas explicadas;
- score de 0 a 100 reproduzível e explicado por dimensão;
- respostas LLM validadas e resistentes a conteúdo malicioso;
- salário identificado ou estimado com confiança e origem;
- alertas Telegram úteis e ações autenticadas;
- currículo e cover letter rastreáveis, sem fatos inventados;
- candidaturas e mudanças de estado auditáveis;
- painel autenticado e funcional;
- execução manual e agendada sem concorrência duplicada;
- relatório de execução, métricas e custos;
- execução local, por Docker Compose e documentação operacional;
- CI cobrindo lint, tipos, testes, build, migrations, dependências e secrets;
- nenhuma credencial exposta;
- nenhuma candidatura automática.

## 14. ADRs previstos

- ADR 0001 — SQLite, PostgreSQL, SQLAlchemy e Alembic.
- ADR 0002 — identidade, normalização e deduplicação de vagas.
- ADR 0003 — abstração de providers LLM, schemas, cache e orçamento.
- ADR 0004 — framework e modelo de autenticação do painel.
- ADR 0005 — scheduler, jobs, lock e workers.
- ADR 0006 — formato do currículo mestre e rastreabilidade documental.

## 15. Informações pendentes do usuário

O perfil inicial fornecido é suficiente para criar a estrutura, mas os seguintes dados
factuais deverão ser preenchidos ou aprovados antes de gerar documentos finais:

- e-mail, telefone, LinkedIn, GitHub/portfólio e preferência de exposição;
- títulos, localidades e datas exatas de Microsoft, Dell e demais experiências;
- responsabilidades, resultados e métricas autorizadas;
- instituições, cursos e datas de formação;
- datas, validade e identificadores públicos das certificações;
- tecnologias classificadas como comprovadas, relacionadas, transferíveis ou não
  atendidas;
- salário mínimo por modalidade, moeda e periodicidade;
- contratos aceitos, disponibilidade, viagens e frequência presencial máxima;
- autorização de trabalho e países elegíveis;
- idioma padrão dos documentos;
- URLs oficiais das empresas prioritárias;
- providers, modelos, budgets e política de consenso;
- Telegram e usuários/chats autorizados;
- horário e dias de busca;
- retenção, backup e política de privacidade;
- autenticação preferida do painel.

## 16. Condições para iniciar a Fase 1

1. Revisão e aprovação deste plano.
2. Workspace apontando para um clone Git real ou aceite explícito para inicializar um
   repositório novo a partir destes arquivos.
3. Python 3.11+ instalado e ambiente virtual criado.
4. Execução do baseline de Ruff, mypy e pytest.
5. Decisão ADR 0001 sobre persistência.

Nenhum push, pull request, deploy, publicação de pacote ou imagem será realizado sem
autorização explícita.
