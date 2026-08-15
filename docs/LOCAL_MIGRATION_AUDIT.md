# Auditoria da migração para execução local

Data da auditoria: 2026-07-20  
Repositório: `tarunlnmiit/autopilot-jobhunt`  
Branch: `main`  
Estado inicial: repositório Git válido, sete commits à frente de `origin/main`, sem alterações
rastreadas pendentes e com `autopilot-jobhunt-main.zip` não rastreado. O ZIP não faz parte da
migração e não será alterado.

## Escopo e método

Foram inspecionados o manifesto Python, requisitos, imagem e Compose, documentação principal,
configurações de exemplo, lista de empresas, CLI, scanner, drafter, utilitários de LLM,
notificações, ferramentas MCP, logging, modelos de domínio, análise, persistência, conectores,
segurança de URL, scheduler, painel e testes. Também foram pesquisadas referências a TinyFish,
OpenRouter, OpenAI, Anthropic, Gemini, Claude CLI, Telegram, WhatsApp/TextMeBot e chamadas de
rede ou subprocessos.

Nenhum segredo foi lido ou registrado. Somente os nomes das variáveis existentes em `.env` e a
estrutura não secreta de `config.json` foram inspecionados.

## Arquitetura encontrada

O projeto local não corresponde apenas ao scanner original. Os sete commits locais adicionaram
aproximadamente 11.798 linhas em 134 arquivos e formam duas camadas parcialmente sobrepostas:

1. O fluxo legado em `job_hunt/scanner.py`, `job_hunt/drafter.py`, `job_hunt/llm_utils.py` e
   `job_hunt/notifier.py`. Esse fluxo usa dicionários, arquivos JSON/CSV e integrações externas.
2. Uma arquitetura mais nova e tipada com:
   - modelos Pydantic em `job_hunt/domain/`;
   - score determinístico e análise estruturada em `job_hunt/analysis/`;
   - conectores em `job_hunt/connectors/`;
   - normalização e deduplicação em `job_hunt/normalization.py` e
     `job_hunt/persistence/job_ingestion.py`;
   - SQLAlchemy/Alembic com SQLite por padrão e PostgreSQL opcional;
   - gerador rastreável de documentos em `job_hunt/documents/`;
   - scheduler com lock de arquivo;
   - painel FastAPI local autenticado;
   - métricas e relatórios de execução;
   - servidor MCP preservado.

A migração deve integrar o fluxo legado à arquitetura nova, sem descartar as melhorias locais.

## Comandos existentes

- `autopilot init`
- `autopilot scan`
- `autopilot draft #N`
- `autopilot draft URL`
- `autopilot export [--min N] [--days N]`
- `autopilot mcp`
- `autopilot schedule [--once]`
- `autopilot web`
- `autopilot panel hash-password`
- `autopilot documents #N [--language en|pt-BR]`
- `autopilot db upgrade|current|seed|import-legacy`

Não existia `autopilot doctor` no início da auditoria.

## Fluxo atual de busca

`main.load_config()` carrega `.env` e `config.json`, exige `TINYFISH_API_KEY` e então
`main.main()` chama `operations.execute_scan()`. O wrapper cria lock, relatório, métricas e uma
execução no banco. `scanner.run_scan()` instancia `TinyFish`, consulta a página de carreira,
expande links de ATS, executa uma busca externa `site:domínio`, baixa descrições e só então pontua
as vagas. Greenhouse e Lever existem como conectores oficiais, mas ainda não alimentam o comando
`autopilot scan`.

O scanner persiste resultados em JSON/CSV e opcionalmente no banco relacional. A coleta legada
marca URLs vistas antes da conclusão de todas as etapas, o que pode esconder uma vaga após falha
de análise.

## Fluxo atual de análise

Há dois caminhos:

- o caminho legado envia lotes de descrições e um resumo do currículo para `chat_with_llm()` e
  deixa o modelo produzir diretamente um score de 0 a 100;
- o caminho `explainable` já calcula filtros e componentes determinísticos e pode pedir uma
  revisão estruturada a um roteador de provedores.

O segundo caminho é a base correta, mas o roteador permite OpenAI, OpenRouter, Gemini, Anthropic e
endpoint OpenAI-compatible arbitrário. A configuração de exemplo ainda seleciona OpenRouter.
Não existe validação central de `LOCAL_ONLY`, nem garantia de ajuste limitado a ±10 em todos os
caminhos.

## Fluxo atual de documentos

`autopilot draft` resolve `#N` no último scan ou aceita URL, mas sempre baixa a descrição com
TinyFish. Em seguida envia descrição e currículo ao provedor configurado e grava Markdown em
`output/`. A camada nova `documents/generator.py` produz pacotes rastreáveis em Markdown e,
opcionalmente, DOCX/PDF, com manifesto e sem envio automático. O comando `documents #N` usa a
descrição já armazenada e score determinístico quando necessário.

## Dependências e serviços externos encontrados

- TinyFish para descoberta, busca e download de vagas;
- OpenRouter, OpenAI, Anthropic, Gemini e Claude CLI para IA;
- Telegram e TextMeBot/WhatsApp para notificações;
- PostgreSQL obrigatório no Compose atual, embora SQLite já seja suportado pelo código;
- páginas públicas de Greenhouse e Lever, que são permitidas no novo modelo local;
- nenhum Ollama nativo no código ou no Compose inicial.

As dependências diretas proibidas ainda presentes são `tinyfish`, `openai` e o extra opcional
`anthropic`. `requests` e `httpx` também são usados, mas podem permanecer exclusivamente para
fontes públicas e serviços locais, passando por uma política central.

## Chamadas de rede encontradas

- `job_hunt/scanner.py` e `job_hunt/drafter.py`: TinyFish;
- `job_hunt/llm_utils.py` e `job_hunt/llm/providers.py`: OpenRouter, OpenAI, Gemini, Anthropic e
  Claude CLI;
- `job_hunt/notifier.py` e `job_hunt/telegram.py`: Telegram e TextMeBot;
- `job_hunt/connectors/greenhouse.py`: API pública Greenhouse;
- `job_hunt/connectors/lever.py`: API pública Lever;
- `job_hunt/security/urls.py`: cliente HTTP com validação de URL, DNS, redirects e tamanho.

O cliente seguro existente já bloqueia credenciais em URL, portas não padrão, localhost, IPs
privados/não roteáveis, redirects inseguros e respostas grandes. Ainda faltam HTTPS obrigatório,
auditoria JSONL, cache, rate limit por domínio, Content-Type, robots.txt e uma allowlist composta
por empresa/conector.

## Arquivos de estado e dados

- `state/seen_jobs.json`
- `state/last_scan.json`
- `state/job_history.json`
- `state/last_run_report.json`
- `state/metrics.json`
- `state/autopilot.db` (SQLite padrão)
- `state/scan.lock`
- `output/jobs_*.csv` e pacotes de documentos
- logs configuráveis, normalmente `scan.log`

No diretório auditado já existiam `state/autopilot.db` e `state/metrics.json`. O histórico JSON
legado ainda não existia. Escritas JSON do scanner não são atômicas e não têm backup ou recuperação
de corrupção.

## Riscos de privacidade e segurança

1. Currículo e descrições podem ser enviados a provedores de IA em nuvem.
2. TinyFish recebe URLs e conteúdo de descoberta/coleta.
3. Telegram/WhatsApp enviam resumos de vagas e metadados a terceiros.
4. Um endpoint OpenAI-compatible configurável pode ser externo e não é validado como local.
5. O prompt legado não delimita de modo suficiente a descrição como conteúdo não confiável.
6. O modelo legado controla diretamente o score e não valida estritamente a resposta.
7. O drafter não valida de forma determinística fatos adicionados pelo modelo.
8. Os logs evitam alguns segredos, mas mensagens de exceção externas podem incluir detalhes e o
   fluxo legado ainda registra títulos/URLs; prompts integrais não devem ser registrados.
9. O Compose atual exige PostgreSQL e não contém Ollama nem GPU, além de copiar currículos
   estruturados para a imagem.
10. Não existe auditoria mínima de rede em `state/network_audit.jsonl`.

## Funcionalidades a preservar

- pacote `job_hunt` e entrada `job_hunt.main:main`;
- comandos `scan`, `draft`, `export` e `mcp` e seus formatos usuais;
- arquivos `companies.json`, `config.json`, `.env`, `resume/`, `state/` e `output/`;
- histórico existente e importação idempotente;
- score explicável e persistência relacional local já implementados;
- geração manual de documentos e revisão humana;
- scheduler, relatórios, logs e painel local quando úteis;
- regra absoluta de não enviar candidaturas, formulários, currículos ou e-mails automaticamente.

## Diferenças em relação a `origin/main`

O checkout local possui sete commits adicionais. Eles introduzem persistência relacional,
deduplicação explicável, conectores Greenhouse/Lever/TinyFish, score determinístico, roteador de LLM
estruturado, salário, documentos, Telegram rico, painel FastAPI, scheduler, métricas, Compose com
PostgreSQL e extensa cobertura de testes. Essas melhorias serão preservadas quando compatíveis.

O projeto original e os commits locais continuam, entretanto, orientados a TinyFish/OpenRouter e
integrações externas. Portanto a mudança necessária é uma migração incremental das bordas (config,
LLM, rede, conectores, notificações e operação), não uma reescrita dos modelos e serviços internos.

## Linha de base de validação

- Python do comando `python`: indisponível por PATH/alias da Microsoft Store.
- Python pelo launcher: CPython 3.12 disponível.
- Ambiente virtual `.venv`: existente e ignorado pelo Git.
- Testes: **181 aprovados** em 2026-07-20.
- Avisos: um `StarletteDeprecationWarning` sobre `httpx` no `TestClient` do FastAPI.
- Docker CLI: 29.6.1.
- Docker Compose: 5.3.0.
- Docker Desktop: daemon indisponível durante a auditoria; Compose/build/up não validados.
- WSL: versão padrão 2, distribuição padrão `docker-desktop`.
- GPU no host: NVIDIA GeForce RTX 3060, 12.288 MiB, driver 610.74.
- GPU no Docker/Ollama: não validada porque o daemon estava parado.

### Validação pós-migração (20/07/2026)

- Docker Desktop foi iniciado e o Compose final foi construído sem erro.
- `ollama/ollama` 0.32.1 detectou CUDA 8.6 e NVIDIA GeForce RTX 3060 com 12 GiB.
- `OLLAMA_NO_CLOUD=true` foi confirmado no log do runtime (`Ollama cloud disabled: true`).
- `qwen3:8b` e `qwen3-embedding:0.6b` foram baixados e verificados pelo Ollama.
- Chat real retornou localmente; após warm-up, a inferência de diagnóstico levou 0,67 s e `/api/ps`
  reportou 8,07 GiB de VRAM ativa/100% GPU.
- Embedding real retornou um vetor com 1.024 dimensões.
- `autopilot`, `scheduler` e `ollama` ficaram saudáveis; portas publicadas somente em
  `127.0.0.1`.

## Plano de migração

1. Criar validação central `LOCAL_ONLY=true`, recusar chaves/provedores/endpoints externos e
   migrar exemplos sem alterar silenciosamente arquivos pessoais.
2. Substituir `llm_utils.py` e o roteador estruturado por Ollama HTTP nativo, JSON validado,
   limites, métricas e fallback apenas determinístico.
3. Evoluir `security/urls.py` para um cliente HTTP central auditado, com HTTPS, allowlist, cache,
   rate limit, Content-Type e política de redirects; adicionar robots.txt.
4. Criar registry e conectores funcionais Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
   JSON-LD e HTML genérico com fixtures offline.
5. Orquestrar os conectores no scanner, normalizar/deduplicar e tornar estado JSON atômico,
   versionado, recuperável e compatível.
6. Fazer o score determinístico ser sempre a base; permitir ao Ollama somente ajuste validado
   entre -10 e +10 e tratar a vaga como entrada não confiável.
7. Refatorar `draft` para usar vaga armazenada ou cliente seguro, gerar artefatos locais e validar
   fidelidade factual.
8. Remover TinyFish, provedores em nuvem, Telegram/WhatsApp e suas dependências/configurações;
   manter terminal, HTML, CSV, JSON e notificação local opcional.
9. Substituir o Compose por Ollama + autopilot/MCP + scheduler com GPU e volumes locais, sem banco
   externo obrigatório; adicionar scripts idempotentes e `autopilot doctor`.
10. Atualizar testes, documentação e executar Ruff, mypy, pytest, cobertura, build e validações do
    Compose. Itens dependentes de Docker/GPU/fontes públicas serão reportados exatamente como
    validados, não validados ou bloqueados.
