# Solução de problemas local

## Docker daemon indisponível

Inicie o Docker Desktop, aguarde o engine Linux e valide `docker version`. O cliente instalado sem
o daemon ativo não é suficiente.

## Ollama indisponível ou modelo ausente

```powershell
docker compose up -d ollama
.\scripts\pull-models.ps1
docker compose logs --tail 100 ollama
```

No host Python, use `OLLAMA_BASE_URL=http://localhost:11434`; dentro do Compose use
`http://ollama:11434`.

## GPU não aparece

Execute `nvidia-smi`, confirme WSL2/Docker Desktop e rode `scripts/test-gpu.ps1`. Uma inferência deve
estar ativa para aparecer em `ollama ps`. O sistema não muda para CPU silenciosamente.

## Fonte sem vagas

Consulte `state/network_audit.jsonl` e `state/last_run_report.json`. `blocked_by_robots`, CAPTCHA,
login e páginas apenas JavaScript são ignorados com segurança. Configure o conector ATS e a
allowlist quando conhecidos; não tente contornar a proteção do site.

## Operação offline

`export`, `draft #N`, MCP, painel, filtros e Ollama continuam locais. `scan` não encontra vagas novas
sem internet, mas o histórico não é apagado.

## Configuração externa rejeitada

Remova chaves/seletores antigos de `.env` e `config.json`, mantenha `LOCAL_ONLY=true` e execute
`autopilot doctor`. Não desabilite a validação para reutilizar endpoints externos.
