# Autopilot Job Hunt — local first

Agente local de descoberta, análise e preparação de candidaturas. Ele consulta diretamente páginas
públicas configuradas em `companies.json`, calcula um score determinístico explicável, usa Ollama
local para uma revisão limitada e gera relatórios/documentos no computador. Nenhuma candidatura é
enviada automaticamente.

## Garantias

- `LOCAL_ONLY=true` é o padrão e bloqueia provedores de IA, scraping, notificações e webhooks externos.
- Não existe fallback para nuvem e nenhuma chave de API externa é necessária.
- Currículo, perfil, prompts, embeddings, análises, banco, logs e documentos permanecem locais.
- A descrição da vaga é conteúdo não confiável; não controla prompts, URLs, arquivos ou score.
- A descoberta usa somente URLs de carreira/ATS públicas allowlisted; não usa motores de busca.
- `draft` apenas cria arquivos para revisão humana. Não preenche formulário, clica em Apply ou envia dados.

## Fluxo

```text
companies.json
    → registry/conector público
    → HTTP HTTPS + SSRF/robots/rate limit/auditoria
    → vaga normalizada e deduplicada
    → score determinístico
    → ajuste estruturado Ollama (máximo ±10 por componente)
    → JSON/SQLite local
    → CSV + JSON + HTML + documentos locais
```

Conectores implementados e testados: Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
JSON-LD `JobPosting` e HTML estático genérico. Playwright não é dependência obrigatória.

## Início rápido — Windows 11, WSL2 e RTX 3060

Pré-requisitos: Docker Desktop usando WSL2, driver NVIDIA atual e PowerShell.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap-local.ps1
```

O bootstrap preserva arquivos existentes, constrói a imagem, inicia Ollama, baixa `qwen3:8b` e
`qwen3-embedding:0.6b`, testa inferência/GPU e executa o diagnóstico.

Operação diária:

```powershell
.\scripts\start-local.ps1
docker compose run --rm autopilot autopilot scan
docker compose run --rm autopilot autopilot draft '#1'
docker compose run --rm autopilot autopilot export --min 60 --days 7
docker compose run --rm autopilot autopilot doctor
.\scripts\stop-local.ps1
```

Interface web diária: abra `http://127.0.0.1:8000` (sem login e sem exposição de rede). Buscas,
vagas, empresas, currículo, documentos, exportações, agenda e diagnóstico estão disponíveis no menu.
Consulte [docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md) para fluxos e limites de segurança.

MCP via stdio:

```powershell
docker compose run --rm -i autopilot autopilot mcp
```

O painel completo fica exclusivamente em `http://127.0.0.1:8000`, sem tela de login. O acesso é
local, com proteção CSRF, validação de origem e links de download temporários; veja [SETUP.md](SETUP.md).

## Configuração

`config.json` define modelos, perfil e comportamento. `.env` contém somente overrides locais e um
segredo opcional para assinar a sessão CSRF e downloads temporários. O padrão é:

```json
{
  "local_only": true,
  "llm_provider": "ollama",
  "ollama": {
    "base_url": "http://ollama:11434",
    "chat_model": "qwen3:8b",
    "embedding_model": "qwen3-embedding:0.6b",
    "context_size": 8192,
    "max_concurrency": 1,
    "cpu_only": false
  }
}
```

Para Python executado no host, use `OLLAMA_BASE_URL=http://localhost:11434`. Modo CPU exige
`OLLAMA_CPU_ONLY=true`; nunca é selecionado silenciosamente.

Entradas antigas de `companies.json` continuam válidas. Campos opcionais:

```json
{
  "name": "Company",
  "careers_url": "https://company.example/careers",
  "connector": "auto",
  "enabled": true,
  "allowed_domains": ["company.example", "boards.greenhouse.io"],
  "location": "Remote",
  "region": "Global"
}
```

## Dados e modo offline

- estado/SQLite/cache/auditoria: `state/` ou volume `autopilot_state`;
- relatórios e documentos: `output/` ou volume `autopilot_output`;
- modelos: volume `ollama_data`;
- configuração/perfil: bind mounts graváveis pelo painel, sempre com escrita atômica; currículo
  estruturado original: somente leitura; versões importadas ficam no SQLite local.

Sem internet, ainda funcionam exportação, filtros, análise e drafts de vagas armazenadas, MCP,
painel e Ollama. Somente descoberta de vagas novas fica indisponível.

Backup e restauração:

```powershell
.\scripts\backup-local.ps1
.\scripts\restore-local.ps1 -Archive .\backups\arquivo.zip -Force
```

O backup inclui os arquivos pessoais do host e, quando o Docker está disponível, os volumes
`autopilot_state` e `autopilot_output`. Os pesos de `ollama_data` não são duplicados; podem ser
baixados novamente com `pull-models.ps1`. O restore exige `-Force`, cria um backup prévio e valida
os caminhos dos arquivos internos antes de restaurar os volumes.

Para apagar os dados Docker: pare os serviços e, somente após backup, execute explicitamente
`docker compose down --volumes`. Arquivos bind-mounted não são apagados por esse comando.

## Desenvolvimento e validação

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest --cov=job_hunt --cov-report=term-missing
.venv\Scripts\python.exe -m build
docker compose config
docker compose build
```

Testes marcados `local_model`, `gpu` e `live_source` são opcionais e separados da suíte offline.

Documentação: [arquitetura](docs/LOCAL_ARCHITECTURE.md), [conectores](docs/CONNECTORS.md),
[política de rede](docs/NETWORK_POLICY.md), [GPU Windows](docs/GPU_SETUP_WINDOWS.md) e
[solução de problemas](docs/TROUBLESHOOTING_LOCAL.md), além do [painel web](docs/WEB_INTERFACE.md).
