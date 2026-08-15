# Instalação local

## Requisitos

- Windows 11 com virtualização e WSL2;
- Docker Desktop configurado para contêineres Linux e backend WSL2;
- driver NVIDIA com suporte WSL2;
- RTX 3060 12 GB ou GPU compatível;
- ao menos 15 GB livres para imagens e modelos.

Valide no host:

```powershell
wsl --status
docker version
docker compose version
nvidia-smi
```

Inicie o Docker Desktop antes do bootstrap. Depois execute:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap-local.ps1
```

O script só cria `.env`/`config.json` se não existirem e nunca substitui currículo. Modelos padrão:
`qwen3:8b` para chat e `qwen3-embedding:0.6b` para embeddings.

## Configuração manual

```powershell
Copy-Item .env.example .env       # somente se .env não existir
Copy-Item config.example.json config.json  # somente se config.json não existir
docker compose config
docker compose build
docker compose up -d ollama
.\scripts\pull-models.ps1
docker compose up -d autopilot scheduler
docker compose ps
```

O painel não possui login e é publicado apenas em `127.0.0.1`. Opcionalmente, defina ao menos 32
bytes aleatórios em `PANEL_SESSION_SECRET` para manter estáveis a sessão CSRF e os links temporários
entre reinícios. Não exponha a porta em `0.0.0.0` e não use proxy público.

## Python local

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[mcp,documents,dev]'
$env:OLLAMA_BASE_URL='http://localhost:11434'
.venv\Scripts\autopilot.exe doctor
```

## Operação

```powershell
docker compose run --rm autopilot autopilot scan
docker compose run --rm autopilot autopilot draft '#1'
docker compose run --rm autopilot autopilot draft 'https://dominio-allowlisted/job/123'
docker compose run --rm autopilot autopilot export --min 60
docker compose run --rm autopilot autopilot mcp
```

Uma URL de draft precisa pertencer à allowlist da empresa em `companies.json`. Nenhuma operação
envia candidatura ou currículo.

Veja [GPU_SETUP_WINDOWS.md](docs/GPU_SETUP_WINDOWS.md) e
[TROUBLESHOOTING_LOCAL.md](docs/TROUBLESHOOTING_LOCAL.md).
